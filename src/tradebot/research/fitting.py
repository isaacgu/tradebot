"""Training-only, one-scalar forecast calibration; no data admission or gate approval.

The fixed estimator is 10 / mean(abs(raw forecast)) on the training partition.
The target applies BEFORE the mandatory +/-20 cap; the measured post-cap mean
and clipping count are reported separately. No outcome, return or validation
value enters the estimate. These primitives do not open market data: a real-data
runner must first satisfy the purpose-scoped authorization boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean

from tradebot.core.timestamps import require_utc
from tradebot.research.report import canonical_bytes
from tradebot.research.splits import ChronologicalSplit, TimeWindow

_TARGET = 10.0
_CAP = 20.0
_MAX_ARTIFACT_BYTES = 65536


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _float(value: object, name: str, *, positive: bool = False) -> float:
    if type(value) is not float or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{name} must be a {'positive ' if positive else ''}finite float")
    return value


def _time(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be datetime")
    return require_utc(value, field=name)


def _instrument(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[^/\s]+/(GBPUSD|EURUSD)", value) is None:
        raise ValueError("instrument must be venue-qualified GBPUSD or EURUSD")
    return value


def _duration_us(value: timedelta) -> int:
    return (value.days * 86400 + value.seconds) * 1000000 + value.microseconds


def _split_value(split: ChronologicalSplit) -> dict[str, object]:
    return {
        "training": split.training,
        "validation": split.validation,
        "lockbox": split.lockbox,
        "embargo_microseconds": _duration_us(split.embargo),
        "label_horizon_microseconds": _duration_us(split.label_horizon),
    }


def _split_hash(split: ChronologicalSplit) -> str:
    return hashlib.sha256(canonical_bytes(_split_value(split))).hexdigest()


@dataclass(frozen=True, slots=True)
class ForecastObservation:
    """Unscaled, unclipped forecast with the actual availability decision time."""

    instrument: str
    observation_start: datetime
    decision_at: datetime
    raw_forecast: float

    def __post_init__(self) -> None:
        _instrument(self.instrument)
        object.__setattr__(self, "observation_start", _time(self.observation_start, "observation"))
        object.__setattr__(self, "decision_at", _time(self.decision_at, "decision_at"))
        if self.decision_at < self.observation_start:
            raise ValueError("decision_at cannot precede observation_start")
        _float(self.raw_forecast, "raw_forecast")


@dataclass(frozen=True, slots=True)
class FitProvenance:
    """Caller-bound identity, not independent verification or market-data permission."""

    instrument: str
    dataset_id: str
    snapshot_sha256: str
    config_sha256: str
    code_sha256: str

    def __post_init__(self) -> None:
        _instrument(self.instrument)
        for name in ("dataset_id", "snapshot_sha256", "config_sha256", "code_sha256"):
            _digest(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ForecastCalibration:
    """Frozen estimator and measured training diagnostics; never a promotion receipt."""

    provenance: FitProvenance
    split: ChronologicalSplit
    observations_sha256: str
    sample_count: int
    first_decision_at: datetime
    last_decision_at: datetime
    scale: float
    training_mean_absolute_raw: float
    training_mean_absolute_scaled: float
    training_mean_absolute_capped: float
    clipped_sample_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, FitProvenance):
            raise TypeError("provenance must be FitProvenance")
        if not isinstance(self.split, ChronologicalSplit):
            raise TypeError("split must be ChronologicalSplit")
        _digest(self.observations_sha256, "observations_sha256")
        if type(self.sample_count) is not int or self.sample_count < 2:
            raise ValueError("sample_count must be an integer >= 2")
        if (
            type(self.clipped_sample_count) is not int
            or not 0 <= self.clipped_sample_count <= self.sample_count
        ):
            raise ValueError("invalid clipped_sample_count")
        first = _time(self.first_decision_at, "first_decision_at")
        last = _time(self.last_decision_at, "last_decision_at")
        if not self.split.training.start <= first < last < self.split.training.end:
            raise ValueError("calibration decision range must be strictly inside training")
        if (
            _classify(self.split, ForecastObservation(self.provenance.instrument, last, last, 0.0))
            != "training"
        ):
            raise ValueError("calibration range crosses the purged training boundary")
        _float(self.scale, "scale", positive=True)
        _float(self.training_mean_absolute_raw, "training_mean_absolute_raw", positive=True)
        _float(self.training_mean_absolute_scaled, "training_mean_absolute_scaled", positive=True)
        _float(self.training_mean_absolute_capped, "training_mean_absolute_capped", positive=True)
        if (
            self.scale != _TARGET / self.training_mean_absolute_raw
            or not math.isclose(self.training_mean_absolute_scaled, _TARGET, rel_tol=1e-12)
            or self.training_mean_absolute_capped > _TARGET * (1 + 1e-12)
        ):
            raise ValueError("calibration statistics disagree with the fixed estimator")

    @property
    def split_sha256(self) -> str:
        """Return the exact training, validation, lockbox and embargo identity."""
        return _split_hash(self.split)

    def as_bytes(self) -> bytes:
        """Deterministic JSON; contains no wall-clock time or machine-local paths."""
        return canonical_bytes(
            {
                "schema_version": 1,
                "evidence_class": "ENGINEERING_ONLY",
                "estimator": "training-mean-absolute-raw-v1",
                "target_basis": "UNCAPPED_TRAINING_FORECAST",
                "target_mean_absolute": _TARGET,
                "forecast_cap": _CAP,
                "provenance": self.provenance,
                "split": _split_value(self.split),
                "split_sha256": self.split_sha256,
                "observations_sha256": self.observations_sha256,
                "sample_count": self.sample_count,
                "first_decision_at": self.first_decision_at,
                "last_decision_at": self.last_decision_at,
                "scale": self.scale,
                "training_mean_absolute_raw": self.training_mean_absolute_raw,
                "training_mean_absolute_scaled": self.training_mean_absolute_scaled,
                "training_mean_absolute_capped": self.training_mean_absolute_capped,
                "clipped_sample_count": self.clipped_sample_count,
                "gate_approvals_claimed": [],
                "execution_enabled": False,
            }
        )

    @property
    def sha256(self) -> str:
        """Content identity of the complete frozen artifact."""
        return hashlib.sha256(self.as_bytes()).hexdigest()


def _classify(split: ChronologicalSplit, observation: ForecastObservation) -> str:
    try:
        label_end = observation.decision_at + split.label_horizon
    except OverflowError as exc:
        raise ValueError("label horizon overflows datetime") from exc
    return split.classify(observation.observation_start, observation.decision_at, label_end)


def _capped(raw: float, scale: float) -> float:
    # Compare before multiplying: finite but extreme OOS forecasts may overflow
    # an uncapped product. The bounded economic signal remains exactly +/-20.
    if abs(raw) >= _CAP / scale:
        return math.copysign(_CAP, raw)
    return raw * scale


def fit_forecast_scale(
    observations: Iterable[ForecastObservation],
    split: ChronologicalSplit,
    provenance: FitProvenance,
) -> ForecastCalibration:
    """Consume training-only observations through EOF, then freeze a single scale.

    Reject, never silently drop, a validation/lockbox/embargo/cross-boundary row.
    The declared label horizon is conservatively purged although fitting this
    unsupervised scalar consumes no labels. Sample counts are measured, not
    evidence of sufficient statistical power. A constant raw series is invalid.
    """
    if not isinstance(split, ChronologicalSplit):
        raise TypeError("split must be ChronologicalSplit")
    if not isinstance(provenance, FitProvenance):
        raise TypeError("provenance must be FitProvenance")
    values: list[float] = []
    digest = hashlib.sha256()
    first: datetime | None = None
    last: datetime | None = None
    previous_start: datetime | None = None
    for observation in observations:
        if not isinstance(observation, ForecastObservation):
            raise TypeError("observations must contain ForecastObservation")
        if observation.instrument != provenance.instrument:
            raise ValueError("observation instrument differs from fit provenance")
        if _classify(split, observation) != "training":
            raise ValueError("fitting accepts training-only observations after purging")
        if (last is not None and observation.decision_at <= last) or (
            previous_start is not None and observation.observation_start <= previous_start
        ):
            raise ValueError("forecast observations must be strictly chronological and unique")
        first = observation.decision_at if first is None else first
        last = observation.decision_at
        previous_start = observation.observation_start
        values.append(observation.raw_forecast)
        digest.update(canonical_bytes(observation))
    if len(values) < 2 or min(values) == max(values):
        raise ValueError("calibration requires at least two nonconstant training forecasts")
    maximum = max(abs(value) for value in values)
    # Normalise the summands first so a finite mean cannot overflow an fsum.
    mean_raw = maximum * fmean(abs(value) / maximum for value in values)
    _float(mean_raw, "training_mean_absolute_raw", positive=True)
    scale = _TARGET / mean_raw
    _float(scale, "scale", positive=True)
    # Scaled training values cannot exceed target * sample_count; this form also
    # avoids overflowing raw*scale for exceptionally small raw forecast units.
    scaled = [abs(value) / mean_raw * _TARGET for value in values]
    capped = [abs(_capped(value, scale)) for value in values]
    if first is None or last is None:
        raise RuntimeError("calibration observation range is missing")
    return ForecastCalibration(
        provenance=provenance,
        split=split,
        observations_sha256=digest.hexdigest(),
        sample_count=len(values),
        first_decision_at=first,
        last_decision_at=last,
        scale=scale,
        training_mean_absolute_raw=mean_raw,
        training_mean_absolute_scaled=fmean(scaled),
        training_mean_absolute_capped=fmean(capped),
        clipped_sample_count=sum(value > _CAP for value in scaled),
    )


def apply_calibration(
    observation: ForecastObservation,
    calibration: ForecastCalibration,
    *,
    split: ChronologicalSplit,
    config_sha256: str,
) -> float:
    """Apply the frozen scalar in validation only; lockbox execution stays denied."""
    if not isinstance(observation, ForecastObservation):
        raise TypeError("observation must be ForecastObservation")
    if not isinstance(calibration, ForecastCalibration):
        raise TypeError("calibration must be ForecastCalibration")
    if not isinstance(split, ChronologicalSplit):
        raise TypeError("split must be ChronologicalSplit")
    if observation.instrument != calibration.provenance.instrument:
        raise ValueError("calibration instrument mismatch")
    if _digest(config_sha256, "config_sha256") != calibration.provenance.config_sha256:
        raise ValueError("calibration config mismatch")
    if _split_hash(split) != calibration.split_sha256:
        raise ValueError("calibration split mismatch")
    if _classify(split, observation) != "validation":
        raise ValueError("frozen calibration accepts validation only; lockbox is denied")
    return _capped(observation.raw_forecast, calibration.scale)


def publish_calibration(calibration: ForecastCalibration, *, output_root: Path) -> Path:
    """Publish atomically under its hash; identical retries never overwrite bytes."""
    if not isinstance(calibration, ForecastCalibration):
        raise TypeError("calibration must be ForecastCalibration")
    payload = calibration.as_bytes()
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / calibration.sha256
    with tempfile.TemporaryDirectory(prefix=".pending-calibration-", dir=output_root) as temporary:
        pending = Path(temporary)
        with (pending / "calibration.json").open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            pending.rename(target)
        except OSError:
            path = target / "calibration.json"
            if (
                target.is_symlink()
                or not target.is_dir()
                or path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != len(payload)
                or path.read_bytes() != payload
            ):
                raise FileExistsError(
                    "immutable calibration artifact already exists and differs"
                ) from None
    return target / "calibration.json"


def load_calibration(path: Path, *, expected_sha256: str) -> ForecastCalibration:
    """Verify the pinned bytes and canonical schema before accepting a frozen model."""
    _digest(expected_sha256, "expected_sha256")
    if path.is_symlink() or not path.is_file():
        raise ValueError("calibration must be a regular non-symlink file")
    with path.open("rb") as stream:
        payload = stream.read(_MAX_ARTIFACT_BYTES + 1)
    if len(payload) > _MAX_ARTIFACT_BYTES:
        raise ValueError("calibration exceeds bounded artifact size")
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("calibration artifact SHA-256 mismatch")
    try:
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("calibration JSON must be an object")
        provenance = FitProvenance(**decoded["provenance"])
        split_value = decoded["split"]
        windows = {
            name: TimeWindow(
                datetime.fromisoformat(split_value[name]["start"]),
                datetime.fromisoformat(split_value[name]["end"]),
            )
            for name in ("training", "validation", "lockbox")
        }
        for name in ("embargo_microseconds", "label_horizon_microseconds"):
            if type(split_value[name]) is not int:
                raise ValueError("split durations must be integer microseconds")
        split = ChronologicalSplit(
            **windows,
            embargo=timedelta(microseconds=split_value["embargo_microseconds"]),
            label_horizon=timedelta(microseconds=split_value["label_horizon_microseconds"]),
        )
        calibration = ForecastCalibration(
            provenance=provenance,
            split=split,
            observations_sha256=decoded["observations_sha256"],
            sample_count=decoded["sample_count"],
            first_decision_at=datetime.fromisoformat(decoded["first_decision_at"]),
            last_decision_at=datetime.fromisoformat(decoded["last_decision_at"]),
            scale=decoded["scale"],
            training_mean_absolute_raw=decoded["training_mean_absolute_raw"],
            training_mean_absolute_scaled=decoded["training_mean_absolute_scaled"],
            training_mean_absolute_capped=decoded["training_mean_absolute_capped"],
            clipped_sample_count=decoded["clipped_sample_count"],
        )
        if calibration.as_bytes() != payload:
            raise ValueError("calibration is not canonical or contains an unsupported schema")
        return calibration
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("invalid calibration artifact schema or statistics") from exc
