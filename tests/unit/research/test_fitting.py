"""Known-answer calibration, held-out leakage canaries and immutable model evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from tradebot.research.fitting import (
    FitProvenance,
    ForecastCalibration,
    ForecastObservation,
    apply_calibration,
    fit_forecast_scale,
    load_calibration,
    publish_calibration,
)
from tradebot.research.report import canonical_bytes
from tradebot.research.splits import ChronologicalSplit, TimeWindow

START = datetime(2024, 1, 1, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
INSTRUMENT = "Synthetic/GBPUSD"


def _at(minutes: float) -> datetime:
    return START + MINUTE * minutes


def _split() -> ChronologicalSplit:
    return ChronologicalSplit(
        training=TimeWindow(_at(0), _at(10)),
        validation=TimeWindow(_at(12), _at(20)),
        lockbox=TimeWindow(_at(22), _at(30)),
        embargo=2 * MINUTE,
        label_horizon=MINUTE,
    )


def _provenance() -> FitProvenance:
    return FitProvenance(INSTRUMENT, "1" * 64, "2" * 64, "3" * 64, "4" * 64)


def _observation(value: float, minute: float = 0) -> ForecastObservation:
    return ForecastObservation(INSTRUMENT, _at(minute), _at(minute + 0.5), value)


def _fit(values: tuple[float, ...] = (-1.0, 3.0)) -> ForecastCalibration:
    return fit_forecast_scale(
        (_observation(value, index) for index, value in enumerate(values)),
        _split(),
        _provenance(),
    )


def test_known_answer_scale_and_actual_counts_are_deterministic() -> None:
    calibration = _fit()
    assert calibration.scale == 5.0
    assert calibration.sample_count == 2
    assert calibration.training_mean_absolute_raw == 2.0
    assert calibration.training_mean_absolute_scaled == 10.0
    assert calibration.training_mean_absolute_capped == 10.0
    assert calibration.clipped_sample_count == 0
    assert calibration.first_decision_at == _at(0.5)
    assert calibration.last_decision_at == _at(1.5)
    assert calibration.as_bytes() == _fit().as_bytes()
    assert calibration.sha256 == hashlib.sha256(calibration.as_bytes()).hexdigest()
    samples = canonical_bytes(_observation(-1.0)) + canonical_bytes(_observation(3.0, 1))
    assert calibration.observations_sha256 == hashlib.sha256(samples).hexdigest()
    payload = json.loads(calibration.as_bytes())
    assert payload["target_basis"] == "UNCAPPED_TRAINING_FORECAST"
    assert payload["evidence_class"] == "ENGINEERING_ONLY"
    assert payload["execution_enabled"] is False
    assert payload["gate_approvals_claimed"] == []


def test_clipping_does_not_misreport_post_cap_mean_as_target() -> None:
    calibration = _fit((10.0, 1.0, 1.0, 1.0, 1.0, 1.0))
    assert calibration.scale == 4.0
    assert calibration.training_mean_absolute_scaled == 10.0
    assert calibration.training_mean_absolute_capped == pytest.approx(40 / 6)
    assert calibration.clipped_sample_count == 1


def test_exact_cap_is_not_counted_as_clipped() -> None:
    calibration = _fit((0.0, 2.0))
    assert calibration.scale == 10.0
    assert calibration.clipped_sample_count == 0
    assert calibration.training_mean_absolute_capped == 10.0


def test_huge_finite_training_values_do_not_overflow_the_mean() -> None:
    calibration = _fit((1e308, -1e308))
    assert calibration.training_mean_absolute_raw == 1e308
    assert calibration.scale == 1e-307
    assert calibration.training_mean_absolute_scaled == 10.0


@pytest.mark.parametrize("values", [(), (1.0,), (0.0, 0.0), (2.0, 2.0)])
def test_insufficient_or_constant_training_rejected(values: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match="nonconstant"):
        _fit(values)


@pytest.mark.parametrize("values", [(0.0, 5e-324), (0.0, 1e-323)])
def test_degenerate_units_reject_underflow_or_infinite_scale(values: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match=r"positive.*finite"):
        _fit(values)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, 1, "1"])
def test_observations_reject_nonfinite_and_nonfloat_values(value: object) -> None:
    with pytest.raises(ValueError, match="finite float"):
        _observation(cast(float, value))


@pytest.mark.parametrize("instrument", ["GBPUSD", "Synthetic/US500", " /GBPUSD", "x/y/EURUSD", 1])
def test_observations_require_bound_venue_qualified_fx_instrument(instrument: object) -> None:
    with pytest.raises(ValueError, match="venue-qualified"):
        replace(_observation(1.0), instrument=cast(str, instrument))


@pytest.mark.parametrize("field", ["observation_start", "decision_at"])
def test_observations_reject_non_utc_and_non_datetime(field: str) -> None:
    for value in (START.replace(tzinfo=None), START.astimezone(timezone(MINUTE))):
        with pytest.raises(ValueError, match="UTC"):
            replace(_observation(1.0), **{field: value})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="datetime"):
        replace(_observation(1.0), **{field: None})  # type: ignore[arg-type]


def test_observation_rejects_impossible_order() -> None:
    with pytest.raises(ValueError, match="precede"):
        replace(_observation(1.0), decision_at=_at(-1))


@pytest.mark.parametrize("field", ["dataset_id", "snapshot_sha256", "config_sha256", "code_sha256"])
@pytest.mark.parametrize("invalid", ["f" * 63, "F" * 64, "z" * 64, "", None])
def test_provenance_requires_pinned_sha256(field: str, invalid: object) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        replace(_provenance(), **{field: invalid})  # type: ignore[arg-type]


def test_calibration_and_nested_provenance_are_frozen() -> None:
    calibration = _fit()
    with pytest.raises(FrozenInstanceError):
        calibration.scale = 123.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        calibration.provenance.config_sha256 = "5" * 64  # type: ignore[misc]


@pytest.mark.parametrize("minute", [-1, 9, 10, 11, 12, 22, 30])
def test_fitting_rejects_purged_embargo_validation_lockbox_and_outside(minute: int) -> None:
    with pytest.raises(ValueError, match="training-only"):
        fit_forecast_scale((_observation(1.0), _observation(2.0, minute)), _split(), _provenance())


def test_late_training_observation_is_purged_not_moved_into_validation() -> None:
    delayed = replace(_observation(2.0, 1), decision_at=_at(12))
    with pytest.raises(ValueError, match="training-only"):
        fit_forecast_scale((_observation(1.0), delayed), _split(), _provenance())


def test_declared_label_horizon_endpoint_may_equal_training_end() -> None:
    accepted = replace(_observation(2.0, 8), decision_at=_at(9))
    fit = fit_forecast_scale((_observation(1.0), accepted), _split(), _provenance())
    assert fit.last_decision_at == _at(9)
    too_late = replace(accepted, decision_at=_at(9) + timedelta(microseconds=1))
    with pytest.raises(ValueError, match="training-only"):
        fit_forecast_scale((_observation(1.0), too_late), _split(), _provenance())


@pytest.mark.parametrize("times", [(1, 0), (0, 0)])
def test_fitting_rejects_reordered_and_duplicate_observations(times: tuple[int, int]) -> None:
    rows = tuple(_observation(float(index), minute) for index, minute in enumerate(times))
    with pytest.raises(ValueError, match="chronological"):
        fit_forecast_scale(rows, _split(), _provenance())


def test_fitting_rejects_duplicate_observation_start_even_if_decision_advances() -> None:
    rows = (_observation(1.0), replace(_observation(2.0), decision_at=_at(1)))
    with pytest.raises(ValueError, match="chronological"):
        fit_forecast_scale(rows, _split(), _provenance())


def test_fit_cannot_mix_instruments() -> None:
    eur = replace(_observation(2.0, 1), instrument="Synthetic/EURUSD")
    with pytest.raises(ValueError, match="instrument"):
        fit_forecast_scale((_observation(1.0), eur), _split(), _provenance())


def test_fit_verifies_eof_before_returning_a_calibration() -> None:
    def invalid_feed() -> Iterator[ForecastObservation]:
        yield _observation(1.0)
        yield _observation(2.0, 1)
        raise RuntimeError("snapshot EOF verification failed")

    with pytest.raises(RuntimeError, match="EOF"):
        fit_forecast_scale(invalid_feed(), _split(), _provenance())


def test_fit_checks_argument_types_before_opening_iterable() -> None:
    class Unreadable:
        def __iter__(self) -> Iterator[ForecastObservation]:
            pytest.fail("invalid request must not acquire input")

    with pytest.raises(TypeError, match="ChronologicalSplit"):
        fit_forecast_scale(Unreadable(), cast(ChronologicalSplit, None), _provenance())
    with pytest.raises(TypeError, match="FitProvenance"):
        fit_forecast_scale(Unreadable(), _split(), cast(FitProvenance, None))
    with pytest.raises(TypeError, match="ForecastObservation"):
        fit_forecast_scale(cast(list[ForecastObservation], [None]), _split(), _provenance())


@pytest.mark.parametrize(
    ("raw", "expected"), [(-1000.0, -20.0), (3.0, 15.0), (0.0, 0.0), (1e308, 20.0)]
)
def test_frozen_validation_application_preserves_estimator(raw: float, expected: float) -> None:
    calibration = _fit()
    before = calibration.as_bytes()
    result = apply_calibration(
        _observation(raw, 12), calibration, split=_split(), config_sha256="3" * 64
    )
    assert result == expected
    assert calibration.as_bytes() == before


@pytest.mark.parametrize("minute", [0, 9, 10, 19, 20, 22, 30])
def test_application_never_trains_or_unlocks_lockbox(minute: int) -> None:
    with pytest.raises(ValueError, match="validation only"):
        apply_calibration(_observation(1.0, minute), _fit(), split=_split(), config_sha256="3" * 64)


def test_apply_checks_instrument_config_and_split_binding() -> None:
    calibration = _fit()
    observation = _observation(1.0, 12)
    with pytest.raises(ValueError, match="instrument"):
        apply_calibration(
            replace(observation, instrument="Synthetic/EURUSD"),
            calibration,
            split=_split(),
            config_sha256="3" * 64,
        )
    with pytest.raises(ValueError, match="config"):
        apply_calibration(observation, calibration, split=_split(), config_sha256="5" * 64)
    with pytest.raises(ValueError, match="split"):
        apply_calibration(
            observation,
            calibration,
            split=replace(_split(), label_horizon=timedelta(0)),
            config_sha256="3" * 64,
        )


def test_apply_checks_all_object_types() -> None:
    with pytest.raises(TypeError, match="observation"):
        apply_calibration(
            cast(ForecastObservation, None), _fit(), split=_split(), config_sha256="3" * 64
        )
    with pytest.raises(TypeError, match="calibration"):
        apply_calibration(
            _observation(1.0, 12),
            cast(ForecastCalibration, None),
            split=_split(),
            config_sha256="3" * 64,
        )
    with pytest.raises(TypeError, match="split"):
        apply_calibration(
            _observation(1.0, 12),
            _fit(),
            split=cast(ChronologicalSplit, None),
            config_sha256="3" * 64,
        )


def test_content_addressed_artifact_roundtrip_and_identical_retry(tmp_path: Path) -> None:
    calibration = _fit()
    path = publish_calibration(calibration, output_root=tmp_path)
    assert path == tmp_path / calibration.sha256 / "calibration.json"
    assert path.read_bytes() == calibration.as_bytes()
    assert publish_calibration(calibration, output_root=tmp_path) == path
    assert load_calibration(path, expected_sha256=calibration.sha256) == calibration
    assert not list(tmp_path.glob(".pending-*"))


def test_existing_different_artifact_is_not_overwritten(tmp_path: Path) -> None:
    calibration = _fit()
    path = publish_calibration(calibration, output_root=tmp_path)
    path.write_bytes(b"corrupted")
    with pytest.raises(FileExistsError, match="immutable"):
        publish_calibration(calibration, output_root=tmp_path)
    assert path.read_bytes() == b"corrupted"
    assert not list(tmp_path.glob(".pending-*"))


def test_publish_rejects_invalid_object(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="calibration"):
        publish_calibration(cast(ForecastCalibration, None), output_root=tmp_path)


def test_load_checks_missing_hash_and_bounded_size(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="regular"):
        load_calibration(tmp_path / "missing.json", expected_sha256="0" * 64)
    path = publish_calibration(_fit(), output_root=tmp_path)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_calibration(path, expected_sha256="0" * 64)
    path.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="bounded"):
        load_calibration(path, expected_sha256="0" * 64)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("evidence_class", "APPROVED"),
        ("scale", 999.0),
        ("target_mean_absolute", 11.0),
        ("sample_count", True),
        ("sample_count", 1),
        ("clipped_sample_count", -1),
        ("clipped_sample_count", 3),
        ("training_mean_absolute_capped", 11.0),
        ("training_mean_absolute_scaled", 9.0),
        ("training_mean_absolute_raw", 0.0),
        ("last_decision_at", _at(10).isoformat()),
        ("last_decision_at", _at(9.5).isoformat()),
        ("first_decision_at", _at(2).isoformat()),
        ("unknown_field", "unsupported"),
        ("execution_enabled", True),
    ],
)
def test_load_rejects_tampered_schema_and_statistics(
    tmp_path: Path, field: str, value: object
) -> None:
    payload = json.loads(_fit().as_bytes())
    payload[field] = value
    path = tmp_path / "calibration.json"
    raw = canonical_bytes(payload)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="invalid calibration"):
        load_calibration(path, expected_sha256=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("raw", [b"{}", b"[]", b"bad JSON", b'"text"', b"[" * 1100 + b"]" * 1100])
def test_load_rejects_malformed_or_deeply_nested_json(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "calibration.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="invalid calibration"):
        load_calibration(path, expected_sha256=hashlib.sha256(raw).hexdigest())


def test_load_rejects_noncanonical_and_duplicate_keys(tmp_path: Path) -> None:
    raw = _fit().as_bytes()
    for changed in (
        raw + b"\n",
        raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'),
    ):
        path = tmp_path / "calibration.json"
        path.write_bytes(changed)
        with pytest.raises(ValueError, match="invalid calibration"):
            load_calibration(path, expected_sha256=hashlib.sha256(changed).hexdigest())


def test_load_rejects_noninteger_duration(tmp_path: Path) -> None:
    payload = json.loads(_fit().as_bytes())
    payload["split"]["embargo_microseconds"] = True
    raw = canonical_bytes(payload)
    path = tmp_path / "calibration.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="invalid calibration"):
        load_calibration(path, expected_sha256=hashlib.sha256(raw).hexdigest())
