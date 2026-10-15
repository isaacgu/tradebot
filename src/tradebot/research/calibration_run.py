"""Causal train-only calibration and frozen validation, never order execution.

Real-data entry requires the existing purpose-scoped, guard-owned snapshot. This
module cannot create a release, relax data acceptance, or attest to profitability.
"""

from __future__ import annotations

import hashlib
import importlib.util
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean

from tradebot.data.storage import FileDigest, sha256_path
from tradebot.research.authorization import AuthorizationError, ResearchPurpose
from tradebot.research.engine import DecisionRecord, ReplayConfig, iter_decisions
from tradebot.research.feed import ReplayBar
from tradebot.research.fitting import (
    FitProvenance,
    ForecastCalibration,
    ForecastObservation,
    apply_calibration,
    fit_forecast_scale,
    load_calibration,
)
from tradebot.research.guarded import ApprovedSnapshotStream
from tradebot.research.report import ReplayProvenance, canonical_bytes, implementation_identity
from tradebot.research.splits import ChronologicalSplit
from tradebot.strategies.momentum import momentum_components


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """A completed fit and measured selected-bar accounting, without financial metrics."""

    calibration: ForecastCalibration
    decisions_by_status: tuple[tuple[str, int], ...]
    source_quality_flags: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]
    release_sha256: str | None
    registry_sha256: str | None


@dataclass(frozen=True, slots=True)
class CalibratedObservation:
    """One frozen validation forecast in unitless [-20, 20] forecast units."""

    observation: ForecastObservation
    value: float
    calibration_sha256: str


@dataclass(frozen=True, slots=True)
class CalibrationValidation:
    """Completed forecast validation, not an economic backtest or strategy selection."""

    forecasts: tuple[CalibratedObservation, ...]
    decisions_by_status: tuple[tuple[str, int], ...]
    source_quality_flags: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]
    release_sha256: str | None
    registry_sha256: str | None


def calibration_implementation_identity() -> tuple[FileDigest, ...]:
    """Hash the replay and fitting producer sources, including split implementation."""
    entries = list(implementation_identity())
    for name in (
        "tradebot.research.calibration_run",
        "tradebot.research.fitting",
        "tradebot.research.registry",
        "tradebot.research.splits",
    ):
        module = importlib.util.find_spec(name)
        if module is None or module.origin is None:
            raise RuntimeError(f"missing calibration implementation source: {name}")
        entries.append(FileDigest(name, sha256_path(Path(module.origin))))
    return tuple(sorted(entries, key=lambda entry: entry.path))


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _check_input(
    records: Iterable[ReplayBar],
    config: ReplayConfig,
    provenance: ReplayProvenance,
    purpose: ResearchPurpose | None,
    required_purpose: ResearchPurpose,
) -> None:
    if len(config.instruments) != 1:
        raise ValueError("fit one instrument per artifact; pooled calibration is not implemented")
    if isinstance(records, ApprovedSnapshotStream):
        if purpose is not required_purpose:
            raise AuthorizationError(f"this operation requires {required_purpose.value}")
        if (
            provenance.source_kind != "immutable_clean_snapshot"
            or provenance.dataset_id != records.spec.dataset_id
            or provenance.source_manifest != records.spec.files
        ):
            raise AuthorizationError("calibration provenance differs from the approved snapshot")
    elif provenance.source_kind != "synthetic":
        raise AuthorizationError("calibration requires a synthetic fixture or approved snapshot")
    # iter_decisions checks the actual authority eagerly before obtaining an
    # input iterator, and again when iteration begins. A provenance label alone
    # never permits a real venue or purpose.


def _observation(decision: DecisionRecord) -> ForecastObservation | None:
    if decision.status != "forecast":
        return None
    if decision.features is None or decision.forecast is None:
        raise RuntimeError("forecast decision lacks its causal features or forecast")
    raw = fmean(value for _, value in momentum_components(decision.features))
    return ForecastObservation(
        instrument=decision.instrument,
        observation_start=decision.bar_open,
        decision_at=decision.decision_at,
        raw_forecast=raw,
    )


def _flags(
    source_flags: dict[str, Counter[str]],
) -> tuple[tuple[str, tuple[tuple[str, int], ...]], ...]:
    return tuple(
        (source, tuple(sorted(flags.items()))) for source, flags in sorted(source_flags.items())
    )


def fit_replay(
    records: Iterable[ReplayBar],
    config: ReplayConfig,
    provenance: ReplayProvenance,
    split: ChronologicalSplit,
    *,
    purpose: ResearchPurpose | None = None,
) -> CalibrationResult:
    """Fit only training strengths; return only after full feed/identity verification.

    All non-training rows bypass the strategy. Their routing metadata and source
    hashes are still inspected, so this does not claim an untouched lockbox. The
    current strict quality policy suppresses every flag, including imputed receipt
    provenance; an all-suppressed dataset fails instead of getting a default scale.
    """
    _check_input(records, config, provenance, purpose, ResearchPurpose.STRATEGY_TRAINING)
    decisions = iter_decisions(records, config, purpose=purpose, split=split, partition="training")
    identity = calibration_implementation_identity()
    fit_provenance = FitProvenance(
        instrument=config.instruments[0],
        dataset_id=provenance.dataset_id,
        snapshot_sha256=_digest(provenance.source_manifest),
        config_sha256=_digest(config),
        code_sha256=_digest(identity),
    )
    counts: Counter[str] = Counter()
    source_flags: dict[str, Counter[str]] = {}

    def observations() -> Iterator[ForecastObservation]:
        for decision in decisions:
            counts[decision.status] += 1
            source_flags.setdefault(decision.source, Counter()).update(decision.quality_flags)
            observation = _observation(decision)
            if observation is not None:
                yield observation

    calibration = fit_forecast_scale(observations(), split, fit_provenance)
    if identity != calibration_implementation_identity():
        raise RuntimeError("calibration implementation changed during fitting")
    if isinstance(records, ApprovedSnapshotStream):
        records.verify_completed()
    return CalibrationResult(
        calibration,
        tuple(sorted(counts.items())),
        _flags(source_flags),
        records.release_sha256 if isinstance(records, ApprovedSnapshotStream) else None,
        records.registry_sha256 if isinstance(records, ApprovedSnapshotStream) else None,
    )


def validate_frozen_replay(
    records: Iterable[ReplayBar],
    config: ReplayConfig,
    provenance: ReplayProvenance,
    split: ChronologicalSplit,
    *,
    calibration_path: Path,
    calibration_sha256: str,
    purpose: ResearchPurpose | None = None,
) -> CalibrationValidation:
    """Load a pinned fit before validation; never refit or emit lockbox forecasts.

    This v1 binds both partitions to the same declared snapshot and configuration.
    It does not rank variants, simulate positions, calculate PnL or confer release.
    """
    _check_input(records, config, provenance, purpose, ResearchPurpose.ECONOMIC_EVALUATION)
    decisions = iter_decisions(
        records, config, purpose=purpose, split=split, partition="validation"
    )
    identity = calibration_implementation_identity()
    calibration = load_calibration(calibration_path, expected_sha256=calibration_sha256)
    if calibration.split != split:
        raise ValueError("frozen calibration split differs from the requested split")
    expected_provenance = FitProvenance(
        instrument=config.instruments[0],
        dataset_id=provenance.dataset_id,
        snapshot_sha256=_digest(provenance.source_manifest),
        config_sha256=_digest(config),
        code_sha256=_digest(identity),
    )
    if calibration.provenance != expected_provenance:
        raise ValueError("frozen calibration differs from snapshot/configuration/implementation")
    counts: Counter[str] = Counter()
    source_flags: dict[str, Counter[str]] = {}
    forecasts: list[CalibratedObservation] = []
    for decision in decisions:
        counts[decision.status] += 1
        source_flags.setdefault(decision.source, Counter()).update(decision.quality_flags)
        observation = _observation(decision)
        if observation is not None:
            value = apply_calibration(
                observation,
                calibration,
                split=split,
                config_sha256=expected_provenance.config_sha256,
            )
            forecasts.append(CalibratedObservation(observation, value, calibration_sha256))
    if not forecasts:
        raise ValueError("validation produced no usable forecasts")
    if identity != calibration_implementation_identity():
        raise RuntimeError("calibration implementation changed during validation")
    if load_calibration(calibration_path, expected_sha256=calibration_sha256) != calibration:
        raise RuntimeError("frozen calibration changed during validation")
    if isinstance(records, ApprovedSnapshotStream):
        records.verify_completed()
    return CalibrationValidation(
        tuple(forecasts),
        tuple(sorted(counts.items())),
        _flags(source_flags),
        records.release_sha256 if isinstance(records, ApprovedSnapshotStream) else None,
        records.registry_sha256 if isinstance(records, ApprovedSnapshotStream) else None,
    )
