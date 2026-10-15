"""End-to-end fitting controls on invented data, including real guard machinery."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_guarded_integration import _open, _payload_fixture, _provenance

from tradebot.data.storage import FileDigest, dataset_id
from tradebot.features.causal import FeatureConfig
from tradebot.research.authorization import AuthorizationError, ResearchPurpose
from tradebot.research.calibration_run import (
    calibration_implementation_identity,
    fit_replay,
    validate_frozen_replay,
)
from tradebot.research.demo import synthetic_records
from tradebot.research.engine import ReplayConfig, iter_decisions
from tradebot.research.feed import ReplayBar
from tradebot.research.fitting import publish_calibration
from tradebot.research.report import ReplayProvenance, canonical_bytes
from tradebot.research.splits import ChronologicalSplit, TimeWindow
from tradebot.strategies.momentum import MomentumConfig


def setup() -> tuple[tuple[ReplayBar, ...], ReplayConfig, ReplayProvenance, ChronologicalSplit]:
    rows = tuple(row for row in synthetic_records() if row.bar.instrument == "Synthetic/EURUSD")
    manifest = (FileDigest("invented.json", hashlib.sha256(canonical_bytes(rows)).hexdigest()),)
    provenance = ReplayProvenance(dataset_id(manifest), "synthetic", manifest, "UNCOMMITTED")
    start = rows[0].bar.ts_open
    split = ChronologicalSplit(
        TimeWindow(start, start + timedelta(minutes=75)),
        TimeWindow(start + timedelta(minutes=80), start + timedelta(minutes=150)),
        TimeWindow(start + timedelta(minutes=155), start + timedelta(minutes=160)),
        timedelta(minutes=5),
        timedelta(minutes=1),
    )
    return rows, ReplayConfig(("Synthetic/EURUSD",), 60), provenance, split


def test_fit_freeze_validate_is_repeatable_and_counts_actual_samples(tmp_path: Path) -> None:
    rows, config, provenance, split = setup()
    first = fit_replay(rows, config, provenance, split)
    assert first == fit_replay(rows, config, provenance, split)
    assert first.calibration.sample_count == 9
    assert dict(first.decisions_by_status) == {"forecast": 9, "warmup": 64}
    assert first.calibration.training_mean_absolute_scaled == pytest.approx(10)
    assert first.release_sha256 is None
    artifact = publish_calibration(first.calibration, output_root=tmp_path)
    validation = validate_frozen_replay(
        rows,
        config,
        provenance,
        split,
        calibration_path=artifact,
        calibration_sha256=first.calibration.sha256,
    )
    assert dict(validation.decisions_by_status) == {"forecast": 3, "suppressed": 1, "warmup": 64}
    assert len(validation.forecasts) == 3
    assert validation.source_quality_flags == (("synthetic-v1", (("TS_RECV_IMPUTED", 1),)),)
    assert all(-20 <= row.value <= 20 for row in validation.forecasts)
    assert all(row.observation.decision_at < split.lockbox.start for row in validation.forecasts)
    assert artifact.read_bytes() == first.calibration.as_bytes()


def test_validation_prices_cannot_change_training_estimate() -> None:
    rows, config, provenance, split = setup()
    changed = tuple(
        replace(
            row,
            bar=replace(
                row.bar,
                open=row.bar.open * 2,
                high=row.bar.high * 2,
                low=row.bar.low * 2,
                close=row.bar.close * 2,
            ),
        )
        if row.bar.ts_open >= split.validation.start
        else row
        for row in rows
    )
    baseline = fit_replay(rows, config, provenance, split).calibration
    # This tests routing/estimation, not fabricated snapshot provenance: arbitrary
    # synthetic iterables are a caller convention; real streams verify exact files.
    modified = fit_replay(changed, config, provenance, split).calibration
    assert modified == baseline


def test_raw_strength_not_capped_forecast_is_fitted() -> None:
    rows, config, provenance, split = setup()
    normal = fit_replay(rows, config, provenance, split).calibration
    extreme_config = replace(config, momentum=MomentumConfig(forecast_scale=1000.0))
    extreme = fit_replay(rows, extreme_config, provenance, split).calibration
    assert extreme.scale == normal.scale
    assert extreme.observations_sha256 == normal.observations_sha256
    assert extreme.provenance.config_sha256 != normal.provenance.config_sha256


def test_artifact_identity_includes_producer_and_attempt_ledger() -> None:
    names = {entry.path for entry in calibration_implementation_identity()}
    assert {
        "tradebot.research.registry",
        "tradebot.research.calibration_run",
        "tradebot.research.fitting",
        "tradebot.research.splits",
        "tradebot.strategies.momentum",
        "tradebot.research.engine",
    } <= names


def test_all_flagged_training_has_no_silent_default() -> None:
    rows, config, provenance, split = setup()
    flagged = tuple(
        replace(row, bar=replace(row.bar, quality_flags=("TS_RECV_IMPUTED",))) for row in rows
    )
    with pytest.raises(ValueError, match="at least two"):
        fit_replay(flagged, config, provenance, split)


def test_invalid_unselected_tail_still_fails_full_consumption() -> None:
    rows, config, provenance, split = setup()
    with pytest.raises(ValueError, match="strictly increasing"):
        fit_replay((*rows, rows[-1]), config, provenance, split)


def test_overlapping_unselected_tail_cannot_hide_behind_partition_filter() -> None:
    rows, config, provenance, split = setup()
    bad_tail = replace(
        rows[-1],
        bar=replace(
            rows[-1].bar,
            ts_open=rows[-2].bar.ts_open,
            ts_event=rows[-2].bar.ts_event,
        ),
    )
    with pytest.raises(ValueError, match="non-overlapping"):
        fit_replay((*rows[:-1], bad_tail), config, provenance, split)


class ForbiddenInput:
    def __iter__(self) -> Iterator[ReplayBar]:
        raise AssertionError("denied request must not acquire input iterator")


def test_real_venue_and_wrong_input_kind_denied_before_read() -> None:
    _, config, provenance, split = setup()
    with pytest.raises(AuthorizationError):
        fit_replay(ForbiddenInput(), ReplayConfig(("FBS-Demo/EURUSD",), 60), provenance, split)
    with pytest.raises(AuthorizationError):
        fit_replay(
            ForbiddenInput(),
            config,
            replace(provenance, source_kind="immutable_clean_snapshot"),
            split,
        )
    with pytest.raises(AuthorizationError):
        fit_replay(
            ForbiddenInput(), config, provenance, split, purpose=ResearchPurpose.STRATEGY_TRAINING
        )
    with pytest.raises(ValueError, match="one instrument"):
        fit_replay(
            ForbiddenInput(),
            replace(config, instruments=("Synthetic/EURUSD", "Synthetic/GBPUSD")),
            provenance,
            split,
        )


@pytest.mark.parametrize("partition", ["lockbox", "unknown"])
def test_lockbox_denied_before_iterator_acquisition(partition: str) -> None:
    _, config, _, split = setup()
    with pytest.raises(ValueError, match="only training and validation"):
        iter_decisions(ForbiddenInput(), config, split=split, partition=partition)


def test_split_and_partition_must_be_paired() -> None:
    _, config, _, split = setup()
    with pytest.raises(ValueError, match="together"):
        iter_decisions(ForbiddenInput(), config, split=split)
    with pytest.raises(ValueError, match="together"):
        iter_decisions(ForbiddenInput(), config, partition="training")
    with pytest.raises(TypeError, match="ChronologicalSplit"):
        iter_decisions(ForbiddenInput(), config, split="wrong", partition="training")  # type: ignore[arg-type]


def test_frozen_config_and_tampered_artifact_denied_before_data_read(tmp_path: Path) -> None:
    rows, config, provenance, split = setup()
    fitted = fit_replay(rows, config, provenance, split).calibration
    artifact = publish_calibration(fitted, output_root=tmp_path)
    with pytest.raises(ValueError, match="differs"):
        validate_frozen_replay(
            ForbiddenInput(),
            replace(config, momentum=MomentumConfig(forecast_scale=9.0)),
            provenance,
            split,
            calibration_path=artifact,
            calibration_sha256=fitted.sha256,
        )
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        validate_frozen_replay(
            ForbiddenInput(),
            config,
            provenance,
            split,
            calibration_path=artifact,
            calibration_sha256=fitted.sha256,
        )


def test_no_validation_forecasts_fails_instead_of_reporting_success(tmp_path: Path) -> None:
    rows, config, provenance, split = setup()
    fitted = fit_replay(rows, config, provenance, split).calibration
    artifact = publish_calibration(fitted, output_root=tmp_path)
    constant = tuple(
        replace(
            row,
            bar=replace(
                row.bar,
                open=Decimal("1.1"),
                high=Decimal("1.1"),
                low=Decimal("1.1"),
                close=Decimal("1.1"),
            ),
        )
        if row.bar.ts_open >= split.validation.start
        else row
        for row in rows
    )
    with pytest.raises(ValueError, match="no usable forecasts"):
        validate_frozen_replay(
            constant,
            config,
            provenance,
            split,
            calibration_path=artifact,
            calibration_sha256=fitted.sha256,
        )


def test_invented_guard_owned_snapshot_fits_only_selected_rows_and_verifies_eof(
    tmp_path: Path,
) -> None:
    fixture = _payload_fixture(tmp_path, ResearchPurpose.STRATEGY_TRAINING)
    stream = _open(fixture, fixture.pin())
    start = fixture.scope.start_utc
    split = ChronologicalSplit(
        TimeWindow(start, start + timedelta(minutes=25)),
        TimeWindow(start + timedelta(minutes=30), start + timedelta(minutes=60)),
        TimeWindow(start + timedelta(minutes=65), start + timedelta(minutes=80)),
        timedelta(minutes=5),
        timedelta(minutes=1),
    )
    config = ReplayConfig(
        (f"{fixture.scope.venue}/EURUSD",), 60, MomentumConfig(FeatureConfig((2, 4), 2, 2))
    )
    fitted = fit_replay(stream, config, _provenance(fixture), split, purpose=fixture.purpose)
    assert stream.completed
    assert stream.consumed_records == 80
    assert fitted.calibration.sample_count == 20
    assert sum(dict(fitted.decisions_by_status).values()) == 24
    assert fitted.release_sha256 == stream.release_sha256
    assert fitted.registry_sha256 == stream.registry_sha256
    stream.close()


def test_changed_split_rejected_before_even_warmup_access(tmp_path: Path) -> None:
    rows, config, provenance, split = setup()
    fitted = fit_replay(rows, config, provenance, split).calibration
    artifact = publish_calibration(fitted, output_root=tmp_path)
    with pytest.raises(ValueError, match="split differs"):
        validate_frozen_replay(
            ForbiddenInput(),
            config,
            provenance,
            replace(split, label_horizon=timedelta(0)),
            calibration_path=artifact,
            calibration_sha256=fitted.sha256,
        )


def test_evaluation_release_cannot_authorize_fitting(tmp_path: Path) -> None:
    fixture = _payload_fixture(tmp_path, ResearchPurpose.ECONOMIC_EVALUATION)
    stream = _open(fixture, fixture.pin())
    _, _, _, split = setup()
    with pytest.raises(AuthorizationError, match="requires STRATEGY_TRAINING"):
        fit_replay(
            stream,
            ReplayConfig((f"{fixture.scope.venue}/EURUSD",), 60),
            _provenance(fixture),
            split,
            purpose=fixture.purpose,
        )
    assert stream.consumed_records == 0
    stream.close()
