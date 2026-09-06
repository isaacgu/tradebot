"""Freeze a real-data sample, import once, and rebuild twice for Gate-1 evidence.

This is offline and read-only with respect to MT5 and the acquisition work directory.
It never calls the terminal or rewrites completed checkpoints. Unknown calendars and
human reference checks are deliberately not upgraded to PASS by reproducible bytes.
"""

from __future__ import annotations

import argparse
import calendar as month_calendar
import json
import platform
import random
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import cast

from tradebot.core.config import _UniqueKeyLoader
from tradebot.core.time_rules import NEW_YORK
from tradebot.core.timestamps import require_utc
from tradebot.data.acquisition_probe import (
    fx_session_bounds as acquisition_fx_session_bounds,
)
from tradebot.data.acquisition_probe import parse_plan
from tradebot.data.calendar import ExpectedLiquidityCalendar
from tradebot.data.corpus import ProbeArtifact, discover_probe_artifacts, import_raw_artifact
from tradebot.data.quality import QualityThresholds
from tradebot.data.source_clock import SourceClockPolicy
from tradebot.data.storage import dataset_id, file_manifest, sha256_path

_ROOT = Path(__file__).resolve().parents[1]
_RANDOM_SAMPLE = "random-sample"
_REFERENCE_MONTH = "reference-month"
_CLOCK_REFERENCE_MONTH = "clock-reference-month"
_SAMPLE_ROLE = "RANDOM_SAMPLE"
_PREHISTORY_ROLE = "PREHISTORY"
_TARGET_ROLE = "REFERENCE_MONTH_TARGET"
_LOOKAHEAD_ROLE = "LOOKAHEAD"
_DEFAULT_MINIMUM_LOOKAHEAD_TICKS = QualityThresholds().price_reversion_ticks


@dataclass(frozen=True, slots=True)
class SelectedArtifact:
    """One immutable producer input with its explicit selection role."""

    artifact: ProbeArtifact
    role: str
    canonical_close_date: date


@dataclass(frozen=True, slots=True)
class ReferenceMonthSelection:
    """Exact canonical-close-month inputs plus bounded context."""

    instrument: str
    reference_month: str
    expected_target_close_dates: tuple[date, ...]
    chunks: tuple[SelectedArtifact, ...]

    @property
    def artifacts(self) -> tuple[ProbeArtifact, ...]:
        return tuple(item.artifact for item in self.chunks)

    @property
    def target_chunks(self) -> tuple[SelectedArtifact, ...]:
        return tuple(item for item in self.chunks if item.role == _TARGET_ROLE)


@dataclass(frozen=True, slots=True)
class ClockSelectedArtifact:
    """An acquisition-label chunk, not a canonical trading session or tick count."""

    artifact: ProbeArtifact
    mapped_intervals: tuple[tuple[datetime, datetime], ...]
    canonical_close_dates_overlapped: tuple[date, ...]
    context_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClockReferenceMonthSelection:
    instrument: str
    reference_month: str
    expected_target_close_dates: tuple[date, ...]
    required_windows: tuple[tuple[date, str, datetime, datetime], ...]
    chunks: tuple[ClockSelectedArtifact, ...]


@dataclass(frozen=True, slots=True)
class SourceClockInputs:
    policy: SourceClockPolicy
    path: Path
    sha256: str
    evidence: tuple[tuple[str, Path, str], ...]

    def verify_unchanged(self) -> None:
        for path, digest in ((self.path, self.sha256), *((p, d) for _, p, d in self.evidence)):
            if sha256_path(path) != digest:
                raise ValueError(f"source-clock policy/evidence changed: {path}")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "identity": self.policy.identity,
            "policy": self.policy.to_dict(),
            "evidence": [
                {"name": name, "path": path, "sha256": digest}
                for name, path, digest in self.evidence
            ],
            "approval_status": "NOT_ASSESSED_BY_PRODUCER",
        }


def _reference_month_days(value: str) -> tuple[date, ...]:
    """Return Monday-through-Friday canonical close dates for ``YYYY-MM``."""

    try:
        parsed = date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise ValueError("reference_month must be canonical YYYY-MM") from exc
    if parsed.strftime("%Y-%m") != value:
        raise ValueError("reference_month must be canonical YYYY-MM")
    return tuple(
        date(parsed.year, parsed.month, day)
        for day in range(1, month_calendar.monthrange(parsed.year, parsed.month)[1] + 1)
        if date(parsed.year, parsed.month, day).weekday() < 5
    )


def _canonical_close_date(artifact: ProbeArtifact) -> date:
    return artifact.request.end.astimezone(NEW_YORK).date()


def _adjacent_trading_day(day: date, *, step: int) -> date:
    if step not in (-1, 1):
        raise ValueError("step must be -1 or 1")
    candidate = day + timedelta(days=step)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=step)
    return candidate


def _validate_canonical_session(artifact: ProbeArtifact) -> None:
    expected_start, expected_end = acquisition_fx_session_bounds(artifact.request.session_date)
    if (artifact.request.start, artifact.request.end) != (expected_start, expected_end):
        raise ValueError(
            f"checkpoint has noncanonical FX session bounds: {artifact.request.chunk_id}"
        )


def select_reference_month(
    artifacts: Sequence[ProbeArtifact],
    *,
    instrument: str,
    reference_month: str,
    minimum_lookahead_ticks: int = _DEFAULT_MINIMUM_LOOKAHEAD_TICKS,
) -> ReferenceMonthSelection:
    """Select every close-month session, one prehistory and one lookahead session.

    Target chunks remain selected even when their recorded row count is zero. Missing
    target or context checkpoints fail closed instead of shrinking the month.
    """

    if type(minimum_lookahead_ticks) is not int:
        raise TypeError("minimum_lookahead_ticks must be an integer")
    if minimum_lookahead_ticks < 1:
        raise ValueError("minimum_lookahead_ticks must be positive")
    expected_close_dates = _reference_month_days(reference_month)
    eligible = sorted(
        (item for item in artifacts if item.request.logical_symbol == instrument),
        key=lambda item: (item.request.start, item.request.chunk_id),
    )
    by_open_date: dict[date, ProbeArtifact] = {}
    by_close_date: dict[date, ProbeArtifact] = {}
    for artifact in eligible:
        open_date = artifact.request.session_date
        if open_date in by_open_date:
            raise ValueError(f"overlapping completed windows for {instrument}/{open_date}")
        close_date = _canonical_close_date(artifact)
        if close_date in by_close_date:
            raise ValueError(f"duplicate completed {instrument} canonical close date {close_date}")
        _validate_canonical_session(artifact)
        by_open_date[open_date] = artifact
        by_close_date[close_date] = artifact
    missing = [day for day in expected_close_dates if day not in by_close_date]
    if missing:
        rendered = ", ".join(day.isoformat() for day in missing)
        raise ValueError(
            f"missing completed {instrument} reference-month target sessions: {rendered}"
        )
    targets = tuple(by_close_date[day] for day in expected_close_dates)
    prehistory_close = _adjacent_trading_day(expected_close_dates[0], step=-1)
    lookahead_close = _adjacent_trading_day(expected_close_dates[-1], step=1)
    prehistory = by_close_date.get(prehistory_close)
    lookahead = by_close_date.get(lookahead_close)
    if prehistory is None:
        raise ValueError(f"missing {instrument} prehistory checkpoint closing {prehistory_close}")
    if lookahead is None:
        raise ValueError(f"missing {instrument} lookahead checkpoint closing {lookahead_close}")
    if prehistory.expected_rows == 0:
        raise ValueError(f"{instrument} prehistory checkpoint is empty")
    if lookahead.expected_rows < minimum_lookahead_ticks:
        raise ValueError(
            f"{instrument} lookahead checkpoint has {lookahead.expected_rows} rows; "
            f"requires at least {minimum_lookahead_ticks}"
        )

    selected = (
        SelectedArtifact(prehistory, _PREHISTORY_ROLE, _canonical_close_date(prehistory)),
        *(SelectedArtifact(item, _TARGET_ROLE, _canonical_close_date(item)) for item in targets),
        SelectedArtifact(lookahead, _LOOKAHEAD_ROLE, _canonical_close_date(lookahead)),
    )
    return ReferenceMonthSelection(
        instrument=instrument,
        reference_month=reference_month,
        expected_target_close_dates=expected_close_dates,
        chunks=selected,
    )


def _true_session_window(close_date: date) -> tuple[datetime, datetime]:
    # Canonical close dates are weekdays. Their sessions open the preceding
    # civil day, including Sunday for a Monday close, never a fixed UTC hour.
    return acquisition_fx_session_bounds(close_date - timedelta(days=1))


def select_clock_reference_month(
    artifacts: Sequence[ProbeArtifact],
    *,
    instrument: str,
    reference_month: str,
    source_clock: SourceClockPolicy,
) -> ClockReferenceMonthSelection:
    """Cover true-UTC trading windows using piecewise-mapped acquisition requests.

    Select every relevant acquisition chunk, including Friday/weekend labels.
    Coverage means completed *request intervals*, never actual quote completeness.
    Policy holes are not bridged merely because the request endpoints map.
    """
    if (source_clock.source, source_clock.instrument, instrument) != (
        "FBS-Demo",
        "EURUSD",
        "EURUSD",
    ):
        raise ValueError("clock reference selection requires exact FBS-Demo/EURUSD scope")
    targets = _reference_month_days(reference_month)
    roles = (
        (_adjacent_trading_day(targets[0], step=-1), _PREHISTORY_ROLE),
        *((day, _TARGET_ROLE) for day in targets),
        (_adjacent_trading_day(targets[-1], step=1), _LOOKAHEAD_ROLE),
    )
    required = tuple((day, role, *_true_session_window(day)) for day, role in roles)
    selected: list[ClockSelectedArtifact] = []
    acquisition_dates: set[date] = set()
    for artifact in sorted(artifacts, key=lambda item: (item.request.start, item.request.chunk_id)):
        request = artifact.request
        if request.logical_symbol != instrument:
            continue
        # Artifacts outside the mapping's label scope cannot contribute.
        if request.end <= source_clock.label_start or request.start >= source_clock.label_end:
            continue
        if artifact.source != source_clock.source or request.broker_symbol != instrument:
            raise ValueError("source-clock acquisition source/symbol differs from exact scope")
        mapped: list[tuple[datetime, datetime]] = []
        for interval in source_clock.intervals:
            start = max(request.start, interval.label_start)
            end = min(request.end, interval.label_end)
            if start >= end:
                continue
            mapped_start = source_clock.apply(start, source=artifact.source, instrument=instrument)
            mapped_end = source_clock.apply(
                end - timedelta(microseconds=1), source=artifact.source, instrument=instrument
            ) + timedelta(microseconds=1)
            if mapped_start >= mapped_end:
                raise ValueError("source-clock mapped request interval is not increasing")
            mapped.append((mapped_start, mapped_end))
        overlapped = tuple(
            (day, role)
            for day, role, start, end in required
            if any(left < end and right > start for left, right in mapped)
        )
        if not overlapped:
            continue
        if request.start < source_clock.label_start or request.end > source_clock.label_end:
            raise ValueError("relevant acquisition chunk exceeds source-clock policy label scope")
        label_cursor = request.start
        for interval in source_clock.intervals:
            left, right = (
                max(request.start, interval.label_start),
                min(request.end, interval.label_end),
            )
            if left >= right:
                continue
            if left != label_cursor:
                raise ValueError(
                    "relevant acquisition request contains an unmapped label interval: "
                    f"{request.chunk_id}"
                )
            label_cursor = right
        if label_cursor != request.end:
            raise ValueError(
                "relevant acquisition request contains an unmapped label interval: "
                f"{request.chunk_id}"
            )
        if request.session_date in acquisition_dates:
            raise ValueError(f"duplicate acquisition-label start date: {request.session_date}")
        acquisition_dates.add(request.session_date)
        selected.append(
            ClockSelectedArtifact(
                artifact=artifact,
                mapped_intervals=tuple(mapped),
                canonical_close_dates_overlapped=tuple(day for day, _ in overlapped),
                context_roles=tuple(dict.fromkeys(role for _, role in overlapped)),
            )
        )
    missing: list[str] = []
    for day, role, start, end in required:
        spans = sorted(
            (max(left, start), min(right, end), item.artifact.request.chunk_id)
            for item in selected
            for left, right in item.mapped_intervals
            if left < end and right > start
        )
        cursor = start
        for left, right, chunk_id in spans:
            if left < cursor:
                raise ValueError(f"overlapping mapped acquisition requests at {chunk_id}")
            if left > cursor:
                missing.append(f"{role}/{day}: [{cursor.isoformat()}, {left.isoformat()})")
            cursor = right
        if cursor < end:
            missing.append(f"{role}/{day}: [{cursor.isoformat()}, {end.isoformat()})")
    if missing:
        raise ValueError(
            "missing mapped request coverage (not a tick-completeness verdict): "
            + "; ".join(missing)
        )
    return ClockReferenceMonthSelection(
        instrument, reference_month, targets, required, tuple(selected)
    )


def load_source_clock_inputs(
    path: Path | None, evidence_arguments: Sequence[str]
) -> SourceClockInputs | None:
    """Bind policy bytes and every named evidence digest to actual checked files."""
    if path is None:
        if evidence_arguments:
            raise ValueError("source-clock evidence requires a source-clock policy")
        return None
    digest = sha256_path(path)
    policy = SourceClockPolicy.from_json(path.read_bytes())
    supplied: dict[str, Path] = {}
    for value in evidence_arguments:
        name, separator, location = value.partition("=")
        if not separator or not name or not location or name in supplied:
            raise ValueError("source-clock evidence must be distinct NAME=PATH bindings")
        supplied[name] = Path(location)
    expected = dict(policy.evidence_sha256)
    if set(supplied) != set(expected):
        raise ValueError("source-clock evidence names must exactly match policy evidence")
    result = SourceClockInputs(
        policy,
        path,
        digest,
        tuple((name, supplied[name], checksum) for name, checksum in sorted(expected.items())),
    )
    result.verify_unchanged()
    return result


def _clock_selection_payload(reference: ClockReferenceMonthSelection) -> dict[str, object]:
    return {
        "reference_month": reference.reference_month,
        "month_label": "America/New_York true-UTC canonical session close date",
        "expected_target_sessions": len(reference.expected_target_close_dates),
        "target_close_dates": list(reference.expected_target_close_dates),
        "selected_acquisition_label_chunks": len(reference.chunks),
        "prehistory_sessions": 1,
        "lookahead_sessions": 1,
        "required_true_utc_windows": [
            {"canonical_close_date": day, "role": role, "start_utc": start, "end_utc": end}
            for day, role, start, end in reference.required_windows
        ],
        "coverage_status": "MAPPED_REQUEST_COVERAGE_COMPLETE",
        "actual_tick_completeness": "NOT_ESTABLISHED_BY_REQUEST_COVERAGE",
        "context_tick_counts": "NOT_INFERRED_FROM_ACQUISITION_CHUNK_ROW_COUNTS",
    }


def load_thresholds(path: Path) -> QualityThresholds:
    """Reject ambiguous/extra configuration rather than silently changing policy."""
    loader = _UniqueKeyLoader(path.read_text(encoding="utf-8"))
    try:
        payload = loader.get_single_data()
    finally:
        loader.dispose()
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "status", "thresholds"}:
        raise ValueError("quality config must contain schema_version, status and thresholds")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("unsupported quality schema")
    if payload["status"] != "prospective":
        raise ValueError("quality configuration must declare prospective policy")
    values = payload["thresholds"]
    expected = {
        "spread_multiplier",
        "price_sigma",
        "price_reversion_ticks",
        "gap_seconds",
        "fast_market_median_seconds",
        "rolling_horizon_seconds",
        "minimum_history",
    }
    if not isinstance(values, dict) or set(values) != expected:
        raise ValueError("quality thresholds must contain exactly the documented fields")
    for key in expected - {"spread_multiplier", "price_sigma"}:
        if type(values[key]) is not int or values[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("spread_multiplier", "price_sigma"):
        if not isinstance(values[key], str):
            raise ValueError(f"{key} must be a quoted decimal")
    return QualityThresholds(
        spread_multiplier=Decimal(values["spread_multiplier"]),
        price_sigma=Decimal(values["price_sigma"]),
        price_reversion_ticks=values["price_reversion_ticks"],
        gap_threshold=timedelta(seconds=values["gap_seconds"]),
        fast_market_median=timedelta(seconds=values["fast_market_median_seconds"]),
        rolling_horizon=timedelta(seconds=values["rolling_horizon_seconds"]),
        minimum_history=values["minimum_history"],
    )


def select_sample(
    artifacts: Sequence[ProbeArtifact], *, instrument: str, days: int, seed: int
) -> tuple[ProbeArtifact, ...]:
    """Sample distinct nonempty session dates, not duplicate dates across pairs."""
    if days < 1:
        raise ValueError("days must be positive")
    eligible = sorted(
        (a for a in artifacts if a.request.logical_symbol == instrument and a.expected_rows > 0),
        key=lambda a: a.request.chunk_id,
    )
    by_day: dict[date, ProbeArtifact] = {}
    for artifact in eligible:
        day = artifact.request.session_date
        if day in by_day:
            raise ValueError(f"overlapping completed windows for {instrument}/{day}")
        by_day[day] = artifact
    if len(by_day) < days:
        raise ValueError(
            f"need {days} distinct nonempty {instrument} days; only {len(by_day)} saved"
        )
    selected = random.Random(seed).sample(sorted(by_day), days)  # noqa: S311 -- sampling, not secrets
    return tuple(by_day[day] for day in sorted(selected))


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
    raise TypeError(f"unsupported evidence value {type(value).__name__}")


def _write_new(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, default=_json_default, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _code_hashes() -> dict[str, str]:
    # Hash the full corpus dependency subtree. Read-only monitoring modules do not
    # participate in raw/clean derivation and may evolve while this offline job runs.
    paths = [
        _ROOT / "scripts/build_gate1_corpus.py",
        _ROOT / "pyproject.toml",
        _ROOT / "uv.lock",
        _ROOT / "docs/SPEC.md",
        _ROOT / "src/tradebot/__init__.py",
        *sorted((_ROOT / "src/tradebot/core").rglob("*.py")),
        *sorted((_ROOT / "src/tradebot/data").rglob("*.py")),
    ]
    return {path.relative_to(_ROOT).as_posix(): sha256_path(path) for path in paths}


def _git_identity() -> dict[str, str]:
    result: dict[str, str] = {}
    for name, args in (("head", ["rev-parse", "HEAD"]), ("status", ["status", "--porcelain"])):
        completed = subprocess.run(  # noqa: S603 -- fixed read-only git arguments
            ["git", "--no-optional-locks", *args],  # noqa: S607 -- fixed read-only local Git
            cwd=_ROOT,
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
        result[name] = completed.stdout.strip()
    return result


def _utc_argument(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    return require_utc(parsed, field=field)


def _chunk_payload(item: SelectedArtifact | ClockSelectedArtifact) -> dict[str, object]:
    artifact = item.artifact
    if isinstance(item, ClockSelectedArtifact):
        return {
            "chunk_id": artifact.request.chunk_id,
            "role": "ACQUISITION_LABEL_CHUNK",
            "acquisition_label_session_date": artifact.request.session_date,
            "request_label_start": artifact.request.start,
            "request_label_end": artifact.request.end,
            "acquisition_chunk_rows": artifact.expected_rows,
            "canonical_close_dates_overlapped": list(item.canonical_close_dates_overlapped),
            "context_roles": list(item.context_roles),
            "mapped_request_intervals": [
                {"start_utc": start, "end_utc": end} for start, end in item.mapped_intervals
            ],
            "checkpoint_sha256": sha256_path(artifact.checkpoint_path),
            "source_sha256": artifact.compressed_sha256,
            "semantic_sha256": artifact.semantic_sha256,
        }
    return {
        "chunk_id": artifact.request.chunk_id,
        "role": item.role,
        "session_open_date": artifact.request.session_date,
        "canonical_close_date": item.canonical_close_date,
        "rows": artifact.expected_rows,
        "checkpoint_sha256": sha256_path(artifact.checkpoint_path),
        "source_sha256": artifact.compressed_sha256,
        "semantic_sha256": artifact.semantic_sha256,
    }


def run(args: argparse.Namespace) -> dict[str, object]:
    """Return a measured evidence report; leave approval and unmeasured checks pending."""
    from tradebot.data.corpus import rebuild_from_raw

    started = time.monotonic()
    started_at = datetime.now(UTC)
    # Fail before heavy work if local Git provenance cannot be read. Actual pipeline
    # files are independently hashed before and after the two immutable rebuilds.
    initial_git_identity = _git_identity()
    thresholds = load_thresholds(args.quality_config)
    code_before = _code_hashes()
    plan_payload = json.loads(args.plan.read_text(encoding="utf-8"))
    if not isinstance(plan_payload, dict):
        raise ValueError("plan must be an object")
    plan = parse_plan(cast(Mapping[str, object], plan_payload))
    clock_inputs = load_source_clock_inputs(
        getattr(args, "source_clock_policy", None), getattr(args, "source_clock_evidence", ())
    )
    selection_mode = cast(str, getattr(args, "selection_mode", _RANDOM_SAMPLE))
    if clock_inputs is not None and selection_mode != _CLOCK_REFERENCE_MONTH:
        raise ValueError("source-clock policy requires clock-reference-month selection")
    if clock_inputs is not None and (
        clock_inputs.policy.source != plan.source
        or clock_inputs.policy.instrument != args.instrument
    ):
        raise ValueError("source-clock policy does not match acquisition plan source/instrument")

    calendar_path = cast(Path | None, getattr(args, "calendar", None))
    calendar_known_at_text = cast(str | None, getattr(args, "calendar_known_at", None))
    calendar_instrument = (
        cast(str | None, getattr(args, "calendar_instrument", None))
        or f"{plan.source}/{args.instrument}"
    )
    calendar: ExpectedLiquidityCalendar | None = None
    calendar_sha256: str | None = None
    calendar_known_at: datetime | None = None
    if calendar_path is not None:
        if calendar_known_at_text is None:
            raise ValueError("calendar_known_at is required when a calendar is supplied")
        calendar_sha256 = sha256_path(calendar_path)
        calendar = ExpectedLiquidityCalendar.read(calendar_path)
        calendar_known_at = _utc_argument(calendar_known_at_text, "calendar_known_at")
        if calendar_known_at > started_at:
            raise ValueError("calendar_known_at cannot be later than the run start")
    elif calendar_known_at_text is not None:
        raise ValueError("calendar_known_at requires a calendar")

    print("Validating saved checkpoints and source checksums...", flush=True)
    artifacts = discover_probe_artifacts(plan, work_root=args.work_dir)
    original_artifact_count = len(artifacts)
    supplement_path = cast(Path | None, getattr(args, "source_supplement_manifest", None))
    supplement_digest = cast(str | None, getattr(args, "source_supplement_sha256", None))
    supplement_artifacts: tuple[ProbeArtifact, ...] = ()
    supplement_ordinal = max((item.ordinal for item in artifacts), default=-1) + 1
    if supplement_path is not None:
        from tradebot.data.source_supplement import load_supplement_artifacts

        if selection_mode != _CLOCK_REFERENCE_MONTH or supplement_digest is None:
            raise ValueError(
                "source supplement requires clock-reference-month and its expected hash"
            )
        supplement_artifacts = load_supplement_artifacts(
            supplement_path,
            artifact_root=supplement_path.parent,
            expected_manifest_sha256=supplement_digest,
            ordinal_start=supplement_ordinal,
        )
        artifacts = (*artifacts, *supplement_artifacts)
    elif supplement_digest is not None:
        raise ValueError("source supplement hash requires a manifest")
    reference: ReferenceMonthSelection | ClockReferenceMonthSelection | None = None
    selected: tuple[SelectedArtifact | ClockSelectedArtifact, ...]
    mode_payload: dict[str, object]
    if selection_mode == _CLOCK_REFERENCE_MONTH:
        if clock_inputs is None:
            raise ValueError("clock-reference-month requires a source-clock policy")
        reference = select_clock_reference_month(
            artifacts,
            instrument=args.instrument,
            reference_month=args.reference_month,
            source_clock=clock_inputs.policy,
        )
        selected = reference.chunks
        mode_payload = _clock_selection_payload(reference)
    elif selection_mode == _REFERENCE_MONTH:
        reference_month = cast(str | None, getattr(args, "reference_month", None))
        if reference_month is None:
            raise ValueError("reference_month is required in reference-month mode")
        reference = select_reference_month(
            artifacts,
            instrument=args.instrument,
            reference_month=reference_month,
            minimum_lookahead_ticks=thresholds.price_reversion_ticks,
        )
        selected = reference.chunks
        empty_targets = [
            item.artifact.request.chunk_id
            for item in reference.target_chunks
            if item.artifact.expected_rows == 0
        ]
        mode_payload = {
            "reference_month": reference.reference_month,
            "month_label": "America/New_York canonical session close date",
            "expected_target_sessions": len(reference.expected_target_close_dates),
            "selected_target_sessions": len(reference.target_chunks),
            "target_close_dates": list(reference.expected_target_close_dates),
            "empty_target_chunk_ids": empty_targets,
            "prehistory_sessions": 1,
            "lookahead_sessions": 1,
            "coverage_status": "COMPLETE",
        }
    elif selection_mode == _RANDOM_SAMPLE:
        sample = select_sample(
            artifacts,
            instrument=args.instrument,
            days=args.days,
            seed=args.seed,
        )
        selected = tuple(
            SelectedArtifact(item, _SAMPLE_ROLE, _canonical_close_date(item)) for item in sample
        )
        mode_payload = {"seed": args.seed, "days": args.days}
    else:
        raise ValueError(f"unsupported selection mode {selection_mode!r}")

    selected_artifacts = tuple(item.artifact for item in selected)
    calendar_binding: dict[str, object] = {
        "path": None if calendar_path is None else calendar_path,
        "sha256": calendar_sha256,
        "known_at_utc": calendar_known_at,
        "calendar_instrument": calendar_instrument,
        "approval_status": "NOT_ASSESSED_BY_PRODUCER",
    }
    selection: dict[str, object] = {
        "plan_hash": plan.plan_hash,
        "selection_mode": selection_mode,
        "instrument": args.instrument,
        "timeframes": list(args.timeframes),
        "initial_git": initial_git_identity,
        "code_hashes": code_before,
        "quality_config_sha256": sha256_path(args.quality_config),
        "calendar": calendar_binding,
        "acquisition_snapshot": {
            "completed_checkpoints": original_artifact_count,
            "expected_chunks": len(plan.chunks),
            "plan_complete": original_artifact_count == len(plan.chunks),
        },
        "chunks": [_chunk_payload(item) for item in selected],
        **mode_payload,
    }
    if clock_inputs is not None:
        selection["source_clock"] = clock_inputs.to_dict()
        clock_inputs.verify_unchanged()
    if supplement_path is not None:
        selection["source_supplement"] = {
            "path": supplement_path,
            "sha256": supplement_digest,
            "artifact_root": supplement_path.parent,
            "validated_acquisition_label_intervals": len(supplement_artifacts),
            "approval_status": "NOT_ASSESSED_BY_PRODUCER",
        }
    selection_path = args.output_dir / "selection.json"
    _write_new(selection_path, selection)
    imported = []
    raw_root = args.output_dir / "snapshot"
    for index, artifact in enumerate(selected_artifacts, 1):
        print(
            f"Import {index}/{len(selected_artifacts)}: {artifact.request.chunk_id}",
            flush=True,
        )
        imported.append(
            import_raw_artifact(artifact, data_root=raw_root, batch_size=args.batch_size)
        )
    raw_paths = tuple(path for item in imported for path in item.files)
    raw_before = file_manifest(raw_paths, relative_to=raw_root)
    results = []
    roots = (args.output_dir / "first", args.output_dir / "second")
    for index, output_root in enumerate(roots, 1):
        print(
            f"Rebuild {index}/2 from the same {len(raw_paths)} immutable raw files...", flush=True
        )
        results.append(
            rebuild_from_raw(
                tuple(imported),
                data_root=output_root,
                venue=args.venue,
                timeframes=tuple(args.timeframes),
                thresholds=thresholds,
                calendar=calendar,
                calendar_id=calendar_sha256,
                known_at=calendar_known_at,
                calendar_instrument=calendar_instrument,
                batch_size=args.batch_size,
                source_clock=None if clock_inputs is None else clock_inputs.policy,
            )
        )
        if clock_inputs is not None:
            clock_inputs.verify_unchanged()
    raw_after = file_manifest(raw_paths, relative_to=raw_root)
    first, second = results
    # Compare both identities and complete file manifests, including bytes on disk.
    first_manifest = file_manifest(
        [*first.clean_tick_files, *first.clean_bar_files], relative_to=roots[0]
    )
    second_manifest = file_manifest(
        [*second.clean_tick_files, *second.clean_bar_files], relative_to=roots[1]
    )
    unchanged_code = code_before == _code_hashes()
    identical = (
        first.corpus_id == second.corpus_id
        and first_manifest == second_manifest
        and bool(first.clean_bar_files)
    )
    raw_unchanged = raw_before == raw_after
    calendar_unchanged = calendar_path is None or sha256_path(calendar_path) == calendar_sha256
    if isinstance(reference, ClockReferenceMonthSelection):
        selection_scope_complete = True  # The mapped-request planner has already failed closed.
    elif reference is not None:
        selection_scope_complete = len(reference.target_chunks) == len(
            reference.expected_target_close_dates
        )
    else:
        selection_scope_complete = args.days >= 30
    reproducible = (
        identical
        and raw_unchanged
        and unchanged_code
        and calendar_unchanged
        and selection_scope_complete
    )
    reference_result: dict[str, object] | None = None
    if isinstance(reference, ClockReferenceMonthSelection):
        reference_result = {
            **_clock_selection_payload(reference),
            "instrument": reference.instrument,
            "acceptance_status": "INDETERMINATE",
            "acceptance_reason": (
                "mapped request coverage and producer reproducibility do not establish "
                "tick completeness or approve the source-clock policy, calendar, flags or Gate 1"
            ),
        }
    elif reference is not None:
        reference_result = {
            "reference_month": reference.reference_month,
            "instrument": reference.instrument,
            "month_label": "America/New_York canonical session close date",
            "expected_target_sessions": len(reference.expected_target_close_dates),
            "selected_target_sessions": len(reference.target_chunks),
            "target_primary_ticks": sum(
                item.artifact.expected_rows for item in reference.target_chunks
            ),
            "empty_target_chunk_ids": [
                item.artifact.request.chunk_id
                for item in reference.target_chunks
                if item.artifact.expected_rows == 0
            ],
            "prehistory_chunk_id": reference.chunks[0].artifact.request.chunk_id,
            "lookahead_chunk_id": reference.chunks[-1].artifact.request.chunk_id,
            "coverage_status": "COMPLETE",
            "acceptance_status": "INDETERMINATE",
            "acceptance_reason": (
                "producer reproducibility cannot approve the liquidity calendar, "
                "counted-flag policy, or Gate 1"
            ),
        }
    report: dict[str, object] = {
        "schema_version": 1,
        "started_at": started_at,
        "finished_at": datetime.now(UTC),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "scope": (
            "Gate-1 30-day reproducibility"
            if reference is None and args.days >= 30
            else (
                "engineering smoke only"
                if reference is None
                else "reference-month producer reproducibility engineering evidence"
            )
        ),
        "gate_approved": False,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pyarrow": version("pyarrow"),
            "tzdata": version("tzdata"),
        },
        "git": initial_git_identity,
        "git_snapshot_timing": "before_source_discovery",
        "selection": selection,
        "selection_file": {
            "path": selection_path.relative_to(args.output_dir).as_posix(),
            "sha256": sha256_path(selection_path),
        },
        "selected_primary_ticks": sum(item.expected_rows for item in selected_artifacts),
        "reference_month": reference_result,
        "calendar_input_unchanged": calendar_unchanged,
        "corpus_id": first.corpus_id,
        "dataset_id": dataset_id(first_manifest),
        "raw_manifest": [asdict(item) for item in raw_before],
        "clean_manifest": [asdict(item) for item in first_manifest],
        "raw_files_unchanged": raw_unchanged,
        "implementation_unchanged": unchanged_code,
        "independent_rebuilds_byte_identical": identical,
        "reproducibility_status": "PASSED" if reproducible else "NOT_SATISFIED",
        "quality": [asdict(summary) for summary in first.quality],
        "bar_rows_by_timeframe": dict(first.bar_rows_by_timeframe),
        "liquid_hours_flagged_bar_criterion": (
            "INDETERMINATE: producer does not approve the dated liquidity calendar "
            "or counted-flag policy"
        ),
        "five_hand_verified_reference_checks": "PENDING",
        "committed_sha_ci": "PENDING",
        "independent_human_review": "PENDING",
        "principal_approval": "PENDING",
    }
    if clock_inputs is not None:
        clock_inputs.verify_unchanged()
        report["source_clock_inputs_unchanged"] = True
        report["selected_primary_ticks_basis"] = "ACQUISITION_CHUNK_ROWS_NOT_TRUE_UTC_TARGET_COUNT"
    if supplement_path is not None:
        if supplement_digest is None:
            raise ValueError("source supplement expected hash is missing")
        if (
            load_supplement_artifacts(
                supplement_path,
                artifact_root=supplement_path.parent,
                expected_manifest_sha256=supplement_digest,
                ordinal_start=supplement_ordinal,
            )
            != supplement_artifacts
        ):
            raise ValueError("source supplement inputs changed during rebuild")
        report["source_supplement_inputs_unchanged"] = True
    _write_new(args.output_dir / "report.json", report)
    report_hash = sha256_path(args.output_dir / "report.json")
    _write_new(args.output_dir / "report.sha256.json", {"report.json": report_hash})
    print(
        json.dumps(
            {
                "report": str(args.output_dir / "report.json"),
                "sha256": report_hash,
                "status": report["reproducibility_status"],
            }
        ),
        flush=True,
    )
    return report


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan", type=Path, default=Path("configs/probes/fbs_tick_continuity_v1.json")
    )
    parser.add_argument("--work-dir", type=Path, default=Path("build/fbs-tick-continuity-v1"))
    parser.add_argument("--quality-config", type=Path, default=Path("configs/data_quality.yaml"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--instrument", default="EURUSD")
    parser.add_argument("--venue", default="FBS")
    parser.add_argument(
        "--selection-mode",
        choices=(_RANDOM_SAMPLE, _REFERENCE_MONTH, _CLOCK_REFERENCE_MONTH),
        default=_RANDOM_SAMPLE,
    )
    parser.add_argument("--reference-month")
    parser.add_argument("--days", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--calendar", type=Path)
    parser.add_argument("--calendar-known-at")
    parser.add_argument("--calendar-instrument")
    parser.add_argument("--source-clock-policy", type=Path)
    parser.add_argument("--source-clock-evidence", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--source-supplement-manifest", type=Path)
    parser.add_argument("--source-supplement-sha256")
    parser.add_argument("--batch-size", type=int, default=65536)
    parser.add_argument("--timeframes", nargs="+", choices=("1m", "1d"), default=["1m"])
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("output-dir must not already exist; evidence runs are append-only")
    if args.selection_mode == _CLOCK_REFERENCE_MONTH:
        if args.source_clock_policy is None:
            parser.error("clock-reference-month requires --source-clock-policy")
    elif args.source_clock_policy is not None or args.source_supplement_manifest is not None:
        parser.error("source-clock policy and supplements require clock-reference-month selection")
    if args.source_clock_evidence and args.source_clock_policy is None:
        parser.error("source-clock evidence requires --source-clock-policy")
    if (args.source_supplement_manifest is None) != (args.source_supplement_sha256 is None):
        parser.error(
            "source supplement requires both --source-supplement-manifest "
            "and --source-supplement-sha256"
        )
    if args.selection_mode == _RANDOM_SAMPLE:
        if args.reference_month is not None:
            parser.error("reference-month requires --selection-mode reference-month")
        args.days = 30 if args.days is None else args.days
        args.seed = 20260904 if args.seed is None else args.seed
    else:
        if args.reference_month is None:
            parser.error("reference-month mode requires --reference-month YYYY-MM")
        try:
            _reference_month_days(args.reference_month)
        except ValueError as exc:
            parser.error(str(exc))
        if args.days is not None or args.seed is not None:
            parser.error("reference-month mode does not accept --days or --seed")
    if args.days is not None and args.days < 1:
        parser.error("days must be positive")
    if args.batch_size < 1:
        parser.error("batch-size must be positive")
    if "1m" not in args.timeframes or len(args.timeframes) != len(set(args.timeframes)):
        parser.error("timeframes must include 1m and must not contain duplicates")
    if args.calendar is None:
        if args.calendar_known_at is not None:
            parser.error("--calendar-known-at requires --calendar")
        if args.calendar_instrument is not None:
            parser.error("--calendar-instrument requires --calendar")
    elif args.calendar_known_at is None:
        parser.error("--calendar requires --calendar-known-at")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    parser_args = _arguments(argv)
    try:
        run(parser_args)
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(f"Corpus evidence failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
