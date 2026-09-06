"""Build a provisional, read-only reference-month coverage diagnostic.

This utility deliberately does not create an ExpectedLiquidityCalendar and cannot
approve Gate 1.  It compares archived, generic FBS advertised-availability
hypotheses with immutable probe observations.  Availability is not liquidity.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

REPORT_SCHEMA: Final = "tradebot-reference-month-diagnostics-v1"
CANDIDATE_SCHEMA: Final = "tradebot-fbs-liquidity-calendar-candidate-v1"
RAW_HEADER: Final = b"tradebot.source-ticks.semantic.v1\n"
NY: Final = ZoneInfo("America/New_York")
MAX_JSON_BYTES: Final = 4 * 1024 * 1024
OFFSET_SCENARIOS: Final = (
    (
        "canonical_epoch_utc",
        0,
        "Baseline: use the MT5 API's documented UTC epoch without adjustment.",
    ),
    (
        "server_gmt_plus_2_interpretation",
        -120,
        "Counterfactual sensitivity only: reinterpret epochs as a fixed GMT+2 server clock.",
    ),
    (
        "server_gmt_plus_3_interpretation",
        -180,
        "Counterfactual sensitivity only: reinterpret epochs as a fixed GMT+3 server clock.",
    ),
)
CHECKPOINT_COUNT_FIELDS: Final = (
    "ask_nonpositive",
    "bid_nonpositive",
    "crossed_quotes",
    "exact_adjacent_duplicates",
    "locked_quotes",
    "negative_volume",
    "negative_volume_real",
    "time_field_mismatches",
    "timestamp_regressions",
)


class DiagnosticError(ValueError):
    """The frozen evidence is unsafe, malformed, or internally inconsistent."""


@dataclass(frozen=True)
class FrozenObservation:
    instrument: str
    candidate_open_date: date
    canonical_start_minute: int
    canonical_end_minute: int
    checkpoint_path: Path
    checkpoint_relative_path: str
    checkpoint_bytes: bytes
    checkpoint_sha256: str
    raw_path: Path
    raw_relative_path: str
    raw_sha256: str
    raw_semantic_sha256: str
    tick_count: int
    checkpoint: dict[str, Any]
    provenance: str


@dataclass(frozen=True)
class ScannedObservation:
    frozen: FrozenObservation
    active_minutes: frozenset[int]
    first_time_msc: int | None
    last_time_msc: int | None


@dataclass(frozen=True)
class FrozenJson:
    path: Path
    content: bytes
    sha256: str
    payload: dict[str, Any]


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def _as_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DiagnosticError(f"{label} must be an object")
    return value


def _as_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise DiagnosticError(f"{label} must be an array")
    return value


def _as_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise DiagnosticError(f"{label} must be a non-empty string")
    return value


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DiagnosticError(f"{label} must be an integer")
    return int(value)


def _read_bounded_bytes(path: Path, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_JSON_BYTES + 1)
    except OSError as exc:
        raise DiagnosticError(f"cannot read {label}: {path}") from exc
    if len(content) > MAX_JSON_BYTES:
        raise DiagnosticError(f"{label} exceeds the {MAX_JSON_BYTES}-byte limit")
    return content


def _read_frozen_json(path: Path, label: str) -> FrozenJson:
    if path.is_symlink():
        raise DiagnosticError(f"{label} must not be a symlink")
    content = _read_bounded_bytes(path, label)
    try:
        parsed = json.loads(content)
    except (UnicodeError, ValueError) as exc:
        raise DiagnosticError(f"{label} is not valid JSON") from exc
    return FrozenJson(path, content, _sha256_bytes(content), _as_dict(parsed, label))


def _ensure_unchanged(frozen: FrozenJson, label: str) -> None:
    current = _read_bounded_bytes(frozen.path, label)
    if current != frozen.content:
        raise DiagnosticError(f"{label} changed during the diagnostic")


def _safe_reference(root: Path, relative: str, label: str) -> Path:
    reference = Path(relative)
    if reference.is_absolute() or ".." in reference.parts:
        raise DiagnosticError(f"{label} escapes its immutable evidence root")
    resolved_root = root.resolve(strict=True)
    cursor = resolved_root
    for part in reference.parts:
        cursor /= part
        if cursor.is_symlink():
            raise DiagnosticError(f"{label} traverses a symlink")
    try:
        resolved = (resolved_root / reference).resolve(strict=True)
    except OSError as exc:
        raise DiagnosticError(f"{label} is missing: {relative}") from exc
    if not resolved.is_relative_to(resolved_root):
        raise DiagnosticError(f"{label} escapes its immutable evidence root")
    return resolved


def _parse_utc(value: Any, label: str) -> datetime:
    text = _as_str(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DiagnosticError(f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise DiagnosticError(f"{label} must be UTC")
    return parsed.astimezone(UTC)


def _minute(value: datetime) -> int:
    if value.second or value.microsecond:
        raise DiagnosticError("hypothesis and acquisition intervals must be minute-aligned")
    return int(value.timestamp()) // 60


def _minute_iso(value: int) -> str:
    return datetime.fromtimestamp(value * 60, tz=UTC).isoformat().replace("+00:00", "Z")


def _interval(value: Any, label: str) -> tuple[int, int]:
    parts = _as_list(value, label)
    if len(parts) != 2:
        raise DiagnosticError(f"{label} must contain exactly [start, end]")
    start = _minute(_parse_utc(parts[0], f"{label}.start"))
    end = _minute(_parse_utc(parts[1], f"{label}.end"))
    if end <= start:
        raise DiagnosticError(f"{label} must be a positive half-open interval")
    return start, end


def _minutes(intervals: list[tuple[int, int]]) -> set[int]:
    result: set[int] = set()
    for start, end in intervals:
        result.update(range(start, end))
    return result


def _month_days(month_text: str) -> list[date]:
    try:
        start = date.fromisoformat(f"{month_text}-01")
    except ValueError as exc:
        raise DiagnosticError("reference_month must be YYYY-MM") from exc
    if start.strftime("%Y-%m") != month_text:
        raise DiagnosticError("reference_month must be canonical YYYY-MM")
    if start.month == 12:
        following = date(start.year + 1, 1, 1)
    else:
        following = date(start.year, start.month + 1, 1)
    return [start + timedelta(days=offset) for offset in range((following - start).days)]


def _canonical_bounds_for_open_date(day: date) -> tuple[int, int]:
    start = datetime.combine(day, time(17), tzinfo=NY).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(17), tzinfo=NY).astimezone(UTC)
    return _minute(start), _minute(end)


def _canonical_bounds_for_close_date(day: date) -> tuple[int, int]:
    start = datetime.combine(day - timedelta(days=1), time(17), tzinfo=NY).astimezone(UTC)
    end = datetime.combine(day, time(17), tzinfo=NY).astimezone(UTC)
    return _minute(start), _minute(end)


def _close_date(end_minute: int) -> date:
    return datetime.fromtimestamp(end_minute * 60, tz=UTC).astimezone(NY).date()


def _validate_candidate(candidate: dict[str, Any]) -> tuple[str, list[str], list[dict[str, Any]]]:
    if candidate.get("schema_version") != CANDIDATE_SCHEMA:
        raise DiagnosticError("unsupported candidate schema")
    approval = _as_dict(candidate.get("approval"), "candidate.approval")
    if (
        approval.get("approved") is not False
        or approval.get("approved_entries") != 0
        or approval.get("loadable_by_expected_liquidity_calendar") is not False
    ):
        raise DiagnosticError("this diagnostic accepts only the unapproved, non-loadable candidate")
    month_text = _as_str(candidate.get("reference_month"), "candidate.reference_month")
    days = _month_days(month_text)
    instruments = [
        _as_str(item, "candidate.instrument_scope[]")
        for item in _as_list(candidate.get("instrument_scope"), "candidate.instrument_scope")
    ]
    if len(set(instruments)) != len(instruments) or not instruments:
        raise DiagnosticError("candidate.instrument_scope must be non-empty and unique")
    rows = [
        _as_dict(item, "candidate.review_rows[]")
        for item in _as_list(candidate.get("review_rows"), "candidate.review_rows")
    ]
    expected_keys = {(instrument, day.isoformat()) for instrument in instruments for day in days}
    actual_keys = {
        (
            _as_str(row.get("instrument"), "review_row.instrument"),
            _as_str(row.get("session_date"), "review_row.session_date"),
        )
        for row in rows
    }
    if len(actual_keys) != len(rows) or actual_keys != expected_keys:
        raise DiagnosticError(
            "candidate rows must contain exactly one row per instrument/open-date"
        )
    for row in rows:
        key_day = date.fromisoformat(_as_str(row["session_date"], "review_row.session_date"))
        canonical = _interval(row.get("canonical_session"), "review_row.canonical_session")
        if canonical != _canonical_bounds_for_open_date(key_day):
            raise DiagnosticError("candidate key/canonical interval identity mismatch")
        status = row.get("advertised_status_hypothesis")
        if status not in {"FULL", "PARTIAL", "CLOSED"}:
            raise DiagnosticError("candidate advertised status is unsupported")
        interval_values = _as_list(
            row.get("advertised_availability_intervals"),
            "review_row.advertised_availability_intervals",
        )
        intervals = [
            _interval(item, f"review_row.advertised_intervals[{index}]")
            for index, item in enumerate(interval_values)
        ]
        if status == "CLOSED" and intervals:
            raise DiagnosticError("CLOSED hypothesis cannot contain advertised-open intervals")
        if status == "FULL" and intervals != [canonical]:
            raise DiagnosticError("FULL hypothesis must equal the canonical interval")
        if status == "PARTIAL" and not intervals:
            raise DiagnosticError("PARTIAL hypothesis must contain an interval")
        previous_end: int | None = None
        for start, end in intervals:
            if start < canonical[0] or end > canonical[1]:
                raise DiagnosticError("advertised interval must stay inside the canonical interval")
            if previous_end is not None and start < previous_end:
                raise DiagnosticError("advertised intervals must be sorted and disjoint")
            previous_end = end
    return month_text, instruments, rows


def _checkpoint_payload(checkpoint_bytes: bytes, label: str) -> dict[str, Any]:
    if len(checkpoint_bytes) > MAX_JSON_BYTES:
        raise DiagnosticError(f"{label} exceeds the JSON size limit")
    try:
        checkpoint = _as_dict(json.loads(checkpoint_bytes), label)
    except (UnicodeError, ValueError) as exc:
        raise DiagnosticError(f"{label} is not valid JSON") from exc
    integrity = _as_dict(checkpoint.get("integrity"), f"{label}.integrity")
    unsigned = {key: value for key, value in checkpoint.items() if key != "integrity"}
    if integrity.get("algorithm") != "sha256" or integrity.get("payload_sha256") != _json_sha256(
        unsigned
    ):
        raise DiagnosticError(f"{label} has an invalid payload checksum")
    return checkpoint


def _freeze_checkpoint(
    *,
    probe_root: Path,
    checkpoint_relative_path: str,
    instrument: str,
    expected_observation: dict[str, Any] | None,
    provenance: str,
) -> FrozenObservation:
    checkpoint_path = _safe_reference(probe_root, checkpoint_relative_path, "checkpoint reference")
    checkpoint_bytes = _read_bounded_bytes(checkpoint_path, "checkpoint")
    checkpoint_sha = _sha256_bytes(checkpoint_bytes)
    checkpoint = _checkpoint_payload(checkpoint_bytes, "checkpoint")
    schema_version = _as_int(checkpoint.get("schema_version"), "checkpoint.schema_version")
    probe_version = _as_str(checkpoint.get("probe_version"), "checkpoint.probe_version")
    plan_hash = _as_str(checkpoint.get("plan_hash"), "checkpoint.plan_hash")
    run_id = _as_str(checkpoint.get("run_id"), "checkpoint.run_id")
    environment_sha = _as_str(checkpoint.get("environment_sha256"), "checkpoint.environment_sha256")
    if schema_version != 1 or probe_version != "fbs-tick-continuity-v1":
        raise DiagnosticError("unsupported checkpoint schema/probe version")
    if re.fullmatch(r"[0-9a-f]{64}", plan_hash) is None:
        raise DiagnosticError("checkpoint plan_hash is not canonical SHA-256")
    if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise DiagnosticError("checkpoint run_id is not canonical lowercase hex")
    if re.fullmatch(r"[0-9a-f]{64}", environment_sha) is None:
        raise DiagnosticError("checkpoint environment_sha256 is not canonical")
    if expected_observation is not None:
        if checkpoint_sha != expected_observation.get("checkpoint_sha256"):
            raise DiagnosticError("candidate/checkpoint file checksum mismatch")
        integrity = _as_dict(checkpoint["integrity"], "checkpoint.integrity")
        if integrity.get("payload_sha256") != expected_observation.get("checkpoint_payload_sha256"):
            raise DiagnosticError("candidate/checkpoint payload checksum mismatch")
        if run_id != expected_observation.get("run_id"):
            raise DiagnosticError("candidate/checkpoint run_id mismatch")

    chunk = _as_dict(checkpoint.get("chunk"), "checkpoint.chunk")
    source = _as_str(checkpoint.get("source"), "checkpoint.source")
    logical_symbol = _as_str(chunk.get("logical_symbol"), "checkpoint.chunk.logical_symbol")
    if instrument != f"{source}/{logical_symbol}":
        raise DiagnosticError("checkpoint instrument identity mismatch")
    open_day = date.fromisoformat(
        _as_str(chunk.get("session_date"), "checkpoint.chunk.session_date")
    )
    _as_str(chunk.get("window_id"), "checkpoint.chunk.window_id")
    canonical_start = _minute(_parse_utc(chunk.get("start_utc"), "checkpoint.chunk.start_utc"))
    canonical_end = _minute(_parse_utc(chunk.get("end_utc"), "checkpoint.chunk.end_utc"))
    if (canonical_start, canonical_end) != _canonical_bounds_for_open_date(open_day):
        raise DiagnosticError("checkpoint chunk boundary identity mismatch")
    raw = _as_dict(checkpoint.get("raw"), "checkpoint.raw")
    raw_relative_path = _as_str(raw.get("path"), "checkpoint.raw.path")
    raw_path = _safe_reference(probe_root, raw_relative_path, "raw reference")
    raw_sha = _sha256_path(raw_path)
    if raw_sha != _as_str(raw.get("compressed_sha256"), "checkpoint.raw.compressed_sha256"):
        raise DiagnosticError("raw compressed checksum mismatch")
    if raw_path.stat().st_size != _as_int(raw.get("compressed_bytes"), "raw.compressed_bytes"):
        raise DiagnosticError("raw compressed byte count mismatch")
    metrics = _as_dict(chunk.get("metrics"), "checkpoint.chunk.metrics")
    tick_count = _as_int(metrics.get("tick_count"), "checkpoint.chunk.metrics.tick_count")

    if expected_observation is not None:
        expected_raw_path = _as_str(expected_observation.get("raw_path"), "observation.raw_path")
        if expected_raw_path != raw_relative_path:
            raise DiagnosticError("candidate/checkpoint raw path mismatch")
        comparisons = {
            "raw_compressed_sha256": raw_sha,
            "raw_semantic_sha256": _as_str(
                raw.get("semantic_sha256"), "checkpoint.raw.semantic_sha256"
            ),
            "tick_count": tick_count,
        }
        for key, actual in comparisons.items():
            if expected_observation.get(key) != actual:
                raise DiagnosticError(f"candidate/checkpoint {key} mismatch")

    return FrozenObservation(
        instrument=instrument,
        candidate_open_date=open_day,
        canonical_start_minute=canonical_start,
        canonical_end_minute=canonical_end,
        checkpoint_path=checkpoint_path,
        checkpoint_relative_path=checkpoint_relative_path,
        checkpoint_bytes=checkpoint_bytes,
        checkpoint_sha256=checkpoint_sha,
        raw_path=raw_path,
        raw_relative_path=raw_relative_path,
        raw_sha256=raw_sha,
        raw_semantic_sha256=_as_str(raw.get("semantic_sha256"), "checkpoint.raw.semantic_sha256"),
        tick_count=tick_count,
        checkpoint=checkpoint,
        provenance=provenance,
    )


def _scan_raw(frozen: FrozenObservation) -> ScannedObservation:
    digest = hashlib.sha256()
    active_minutes: set[int] = set()
    rows = 0
    first_time_msc: int | None = None
    last_time_msc: int | None = None
    uncompressed_bytes = 0
    start_msc = frozen.canonical_start_minute * 60_000
    end_msc = frozen.canonical_end_minute * 60_000
    try:
        with gzip.open(frozen.raw_path, "rb") as stream:
            header = stream.readline()
            if header != RAW_HEADER:
                raise DiagnosticError(f"invalid canonical raw header: {frozen.raw_relative_path}")
            digest.update(header)
            uncompressed_bytes += len(header)
            for line_number, line in enumerate(stream, start=2):
                digest.update(line)
                uncompressed_bytes += len(line)
                if not line.endswith(b"\n"):
                    raise DiagnosticError(
                        f"unterminated raw row {line_number}: {frozen.raw_relative_path}"
                    )
                fields = line[:-1].split(b"\t")
                if len(fields) != 8:
                    raise DiagnosticError(
                        f"invalid raw field count at row {line_number}: {frozen.raw_relative_path}"
                    )
                try:
                    seconds = int(fields[0])
                    time_msc = int(fields[1])
                except ValueError as exc:
                    raise DiagnosticError(
                        f"invalid raw timestamp at row {line_number}: {frozen.raw_relative_path}"
                    ) from exc
                if seconds != time_msc // 1000 or not start_msc <= time_msc < end_msc:
                    raise DiagnosticError(
                        f"raw timestamp identity mismatch at row {line_number}: "
                        f"{frozen.raw_relative_path}"
                    )
                if first_time_msc is None:
                    first_time_msc = time_msc
                last_time_msc = time_msc
                active_minutes.add(time_msc // 60_000)
                rows += 1
    except (gzip.BadGzipFile, OSError) as exc:
        raise DiagnosticError(f"cannot stream raw artifact: {frozen.raw_relative_path}") from exc
    if digest.hexdigest() != frozen.raw_semantic_sha256:
        raise DiagnosticError(f"raw semantic checksum mismatch: {frozen.raw_relative_path}")
    if rows != frozen.tick_count:
        raise DiagnosticError(f"raw row count mismatch: {frozen.raw_relative_path}")
    raw = _as_dict(frozen.checkpoint.get("raw"), "checkpoint.raw")
    if uncompressed_bytes != _as_int(raw.get("uncompressed_bytes"), "raw.uncompressed_bytes"):
        raise DiagnosticError(f"raw uncompressed byte count mismatch: {frozen.raw_relative_path}")
    metrics = _as_dict(
        _as_dict(frozen.checkpoint.get("chunk"), "checkpoint.chunk").get("metrics"),
        "checkpoint.chunk.metrics",
    )
    if len(active_minutes) != _as_int(metrics.get("active_minutes"), "metrics.active_minutes"):
        raise DiagnosticError(f"raw active-minute count mismatch: {frozen.raw_relative_path}")
    return ScannedObservation(frozen, frozenset(active_minutes), first_time_msc, last_time_msc)


def _verify_observation_unchanged(observation: FrozenObservation) -> None:
    checkpoint_bytes = _read_bounded_bytes(observation.checkpoint_path, "checkpoint")
    if checkpoint_bytes != observation.checkpoint_bytes:
        raise DiagnosticError("checkpoint changed during the diagnostic")
    if _sha256_path(observation.raw_path) != observation.raw_sha256:
        raise DiagnosticError("raw artifact changed during the diagnostic")


def _shift(minutes: set[int] | frozenset[int], offset_minutes: int) -> set[int]:
    return {value + offset_minutes for value in minutes}


def _longest_run(minutes: set[int]) -> dict[str, Any] | None:
    if not minutes:
        return None
    ordered = sorted(minutes)
    best_start = current_start = ordered[0]
    best_end = current_end = ordered[0]
    for value in ordered[1:]:
        if value == current_end + 1:
            current_end = value
        else:
            if current_end - current_start > best_end - best_start:
                best_start, best_end = current_start, current_end
            current_start = current_end = value
    if current_end - current_start > best_end - best_start:
        best_start, best_end = current_start, current_end
    return {
        "minutes": best_end - best_start + 1,
        "start_utc": _minute_iso(best_start),
        "end_utc_exclusive": _minute_iso(best_end + 1),
    }


def _minute_metrics(
    expected: set[int], acquisition: set[int], observed: set[int]
) -> dict[str, Any]:
    if not observed <= acquisition:
        raise DiagnosticError("observed minutes must be a subset of verified acquisition windows")
    evaluable = expected & acquisition
    active = expected & observed
    unobserved = evaluable - observed
    unknown = expected - acquisition
    share = None if not evaluable else round(len(active) / len(evaluable), 12)
    return {
        "expected_advertised_minutes": len(expected),
        "evaluable_expected_minutes": len(evaluable),
        "observed_active_minutes_within_expected": len(active),
        "unobserved_evaluable_minutes": len(unobserved),
        "unknown_expected_minutes_due_to_unverified_window": len(unknown),
        "active_share_of_evaluable_advertised_minutes": share,
        "longest_unobserved_evaluable_run": _longest_run(unobserved),
        "longest_unknown_expected_run": _longest_run(unknown),
    }


def _source_flag_summary(
    observations: list[ScannedObservation], target_month: str
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    mt5_flags: Counter[int] = Counter()
    tick_rows = 0
    windows = 0
    for scanned in observations:
        if _close_date(scanned.frozen.canonical_end_minute).strftime("%Y-%m") != target_month:
            continue
        metrics = _as_dict(
            _as_dict(scanned.frozen.checkpoint.get("chunk"), "checkpoint.chunk").get("metrics"),
            "checkpoint.chunk.metrics",
        )
        tick_rows += scanned.frozen.tick_count
        windows += 1
        for field in CHECKPOINT_COUNT_FIELDS:
            counts[field] += _as_int(metrics.get(field), f"metrics.{field}")
        for item in _as_list(metrics.get("flag_counts"), "metrics.flag_counts"):
            pair = _as_list(item, "metrics.flag_counts[]")
            if len(pair) != 2:
                raise DiagnosticError("metrics.flag_counts entries must be [flag, count]")
            mt5_flags[_as_int(pair[0], "flag code")] += _as_int(pair[1], "flag count")
    return {
        "scope": "verified raw checkpoint windows whose canonical NY close date is in month",
        "checkpoint_windows": windows,
        "tick_rows": tick_rows,
        "checkpoint_observation_counts": dict(sorted(counts.items())),
        "mt5_tick_flag_value_counts": [
            {"flag_value": flag, "rows": rows} for flag, rows in sorted(mt5_flags.items())
        ],
        "classification": "SOURCE_CHECKPOINT_DIAGNOSTICS_NOT_P1_QUALITY_FLAGS",
    }


def _freeze_candidate_observations(
    rows: list[dict[str, Any]], probe_root: Path
) -> tuple[list[FrozenObservation], dict[str, str], dict[str, Any]]:
    frozen: list[FrozenObservation] = []
    parent_by_instrument: dict[str, str] = {}
    for row in rows:
        observation_value = row.get("observation")
        if observation_value is None:
            continue
        observation = _as_dict(observation_value, "review_row.observation")
        instrument = _as_str(row.get("instrument"), "review_row.instrument")
        checkpoint_relative = _as_str(
            observation.get("checkpoint_path"), "observation.checkpoint_path"
        )
        item = _freeze_checkpoint(
            probe_root=probe_root,
            checkpoint_relative_path=checkpoint_relative,
            instrument=instrument,
            expected_observation=observation,
            provenance="FROZEN_CANDIDATE_REFERENCE",
        )
        if item.candidate_open_date.isoformat() != row.get("session_date"):
            raise DiagnosticError("candidate row/checkpoint open-date mismatch")
        parent = str(Path(checkpoint_relative).parent)
        prior_parent = parent_by_instrument.setdefault(instrument, parent)
        if prior_parent != parent:
            raise DiagnosticError("candidate checkpoints span multiple instrument directories")
        frozen.append(item)
    singleton_fields = {
        "schema_version": {item.checkpoint.get("schema_version") for item in frozen},
        "probe_version": {item.checkpoint.get("probe_version") for item in frozen},
        "plan_hash": {item.checkpoint.get("plan_hash") for item in frozen},
        "window_id": {
            _as_dict(item.checkpoint.get("chunk"), "checkpoint.chunk").get("window_id")
            for item in frozen
        },
        "environment_sha256": {item.checkpoint.get("environment_sha256") for item in frozen},
    }
    if any(len(values) != 1 for values in singleton_fields.values()):
        raise DiagnosticError("candidate checkpoints do not share one frozen lineage")
    lineage = {key: next(iter(values)) for key, values in singleton_fields.items()}
    lineage["accepted_run_ids"] = sorted(
        {_as_str(item.checkpoint.get("run_id"), "checkpoint.run_id") for item in frozen}
    )
    return frozen, parent_by_instrument, lineage


def _freeze_boundary_supplements(
    *,
    instruments: list[str],
    parent_by_instrument: dict[str, str],
    probe_root: Path,
    boundary_open_date: date,
    lineage: dict[str, Any],
) -> list[FrozenObservation]:
    supplements: list[FrozenObservation] = []
    for instrument in instruments:
        parent = parent_by_instrument.get(instrument)
        if parent is None:
            raise DiagnosticError(f"cannot derive fixed boundary checkpoint path for {instrument}")
        relative = str(
            Path(parent) / f"{boundary_open_date.isoformat()}.source-ticks.tsv.checkpoint.json"
        ).replace("\\", "/")
        item = _freeze_checkpoint(
            probe_root=probe_root,
            checkpoint_relative_path=relative,
            instrument=instrument,
            expected_observation=None,
            provenance="SUPPLEMENTAL_CLOSE_MONTH_BOUNDARY_REFERENCE",
        )
        if item.candidate_open_date != boundary_open_date:
            raise DiagnosticError("supplemental checkpoint date mismatch")
        item_identity = {
            "schema_version": item.checkpoint.get("schema_version"),
            "probe_version": item.checkpoint.get("probe_version"),
            "plan_hash": item.checkpoint.get("plan_hash"),
            "window_id": _as_dict(item.checkpoint.get("chunk"), "checkpoint.chunk").get(
                "window_id"
            ),
            "environment_sha256": item.checkpoint.get("environment_sha256"),
        }
        if any(item_identity[key] != lineage[key] for key in item_identity):
            raise DiagnosticError("supplemental checkpoint lineage mismatch")
        if item.checkpoint.get("run_id") not in lineage["accepted_run_ids"]:
            raise DiagnosticError("supplemental checkpoint run_id is not candidate-frozen")
        supplements.append(item)
    return supplements


def _hypothesis_rows(
    *, month_text: str, instrument: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_close_date: dict[date, dict[str, Any]] = {}
    for row in rows:
        if row.get("instrument") != instrument:
            continue
        _, end = _interval(row.get("canonical_session"), "review_row.canonical_session")
        by_close_date[_close_date(end)] = row
    result: list[dict[str, Any]] = []
    for close_day in _month_days(month_text):
        candidate_row = by_close_date.get(close_day)
        if candidate_row is None:
            start, end = _canonical_bounds_for_close_date(close_day)
            status = "FULL" if close_day.weekday() < 5 else "CLOSED"
            intervals = [] if status == "CLOSED" else [(start, end)]
            result.append(
                {
                    "canonical_close_date": close_day.isoformat(),
                    "candidate_open_date": None,
                    "status": status,
                    "intervals": intervals,
                    "basis": ["conditions-20241014", "trading-hours-20241015"],
                    "origin": "DERIVED_RECURRING_EDGE_HYPOTHESIS_NOT_CANDIDATE_APPROVAL",
                    "review_disposition": "PROVISIONAL_EDGE_HYPOTHESIS",
                }
            )
            continue
        intervals = [
            _interval(item, "review_row.advertised_availability_intervals[]")
            for item in _as_list(
                candidate_row.get("advertised_availability_intervals"),
                "review_row.advertised_availability_intervals",
            )
        ]
        result.append(
            {
                "canonical_close_date": close_day.isoformat(),
                "candidate_open_date": candidate_row.get("session_date"),
                "status": candidate_row.get("advertised_status_hypothesis"),
                "intervals": intervals,
                "basis": sorted(_as_dict(candidate_row.get("basis"), "review_row.basis").values()),
                "origin": "FROZEN_CANDIDATE_ROW",
                "review_disposition": candidate_row.get("review_disposition"),
            }
        )
    return result


def _thirty_day_context(report_path: Path | None) -> tuple[dict[str, Any], FrozenJson | None]:
    if report_path is None:
        return {
            "status": "UNAVAILABLE",
            "reason": "No completed 30-day report path was supplied.",
        }, None
    frozen = _read_frozen_json(report_path, "30-day report")
    sidecar = _read_frozen_json(report_path.with_name("report.sha256.json"), "30-day sidecar")
    if sidecar.payload.get("report.json") != frozen.sha256:
        raise DiagnosticError("30-day report sidecar checksum mismatch")
    selection = _as_dict(frozen.payload.get("selection"), "30-day report.selection")
    selected: list[dict[str, Any]] = []
    for item in _as_list(selection.get("chunks"), "30-day report.selection.chunks"):
        chunk = _as_dict(item, "selection.chunks[]")
        chunk_id = _as_str(chunk.get("chunk_id"), "selection.chunk_id")
        if "/autumn_dst_reference_2024/2024-10-" in chunk_id:
            selected.append(
                {
                    "candidate_open_date": chunk_id.rsplit("/", 1)[-1],
                    "checkpoint_sha256": chunk.get("checkpoint_sha256"),
                    "raw_compressed_sha256": chunk.get("source_sha256"),
                    "raw_semantic_sha256": chunk.get("semantic_sha256"),
                    "rows": chunk.get("rows"),
                }
            )
    manifest_entries = []
    for item in _as_list(frozen.payload.get("clean_manifest"), "30-day clean_manifest"):
        entry = _as_dict(item, "clean_manifest[]")
        path = _as_str(entry.get("path"), "clean_manifest.path")
        if "/2024/10/" in path:
            manifest_entries.append({"path": path, "sha256": entry.get("sha256")})
    return {
        "status": "VERIFIED_REPORT_MANIFEST_ONLY",
        "report_path": report_path.as_posix(),
        "report_sha256": frozen.sha256,
        "reproducibility_status": frozen.payload.get("reproducibility_status"),
        "selected_candidate_open_date_chunks": selected,
        "utc_2024_10_clean_manifest_entries": manifest_entries,
        "p1_clean_quality_flag_breakdown": {
            "status": "UNAVAILABLE_FOR_EXACT_REFERENCE_MONTH_SCOPE",
            "reason": (
                "The report exposes P1 flags only as a 30-date multi-year aggregate; the "
                "2024/10 path is a UTC partition, not a canonical NY-close-date month."
            ),
        },
    }, frozen


def build_diagnostic(
    *,
    candidate_path: Path,
    probe_root: Path,
    thirty_day_report: Path | None,
    generated_at: datetime,
) -> dict[str, Any]:
    """Build an in-memory diagnostic after freezing and re-verifying every input."""

    if generated_at.tzinfo is None or generated_at.utcoffset() != timedelta(0):
        raise DiagnosticError("generated_at must be UTC")
    script_path = Path(__file__).resolve(strict=True)
    script_sha256 = _sha256_path(script_path)
    candidate_frozen = _read_frozen_json(candidate_path, "calendar candidate")
    month_text, instruments, rows = _validate_candidate(candidate_frozen.payload)
    candidate_observations, parents, lineage = _freeze_candidate_observations(rows, probe_root)
    first_month_day = _month_days(month_text)[0]
    supplements = _freeze_boundary_supplements(
        instruments=instruments,
        parent_by_instrument=parents,
        probe_root=probe_root,
        boundary_open_date=first_month_day - timedelta(days=1),
        lineage=lineage,
    )
    all_frozen = candidate_observations + supplements
    if len({item.checkpoint_path for item in all_frozen}) != len(all_frozen):
        raise DiagnosticError("duplicate frozen checkpoint reference")
    scanned = [_scan_raw(item) for item in all_frozen]
    thirty_day, thirty_day_frozen = _thirty_day_context(thirty_day_report)

    instrument_reports: list[dict[str, Any]] = []
    for instrument in instruments:
        instrument_scans = [item for item in scanned if item.frozen.instrument == instrument]
        acquisition = _minutes(
            [
                (item.frozen.canonical_start_minute, item.frozen.canonical_end_minute)
                for item in instrument_scans
            ]
        )
        observed: set[int] = set()
        for item in instrument_scans:
            observed.update(item.active_minutes)
        hypothesis_rows = _hypothesis_rows(month_text=month_text, instrument=instrument, rows=rows)
        target_days = _month_days(month_text)
        month_horizon_start, _ = _canonical_bounds_for_close_date(target_days[0])
        _, month_horizon_end = _canonical_bounds_for_close_date(target_days[-1])
        month_horizon = set(range(month_horizon_start, month_horizon_end))
        expected: set[int] = set()
        for row in hypothesis_rows:
            expected.update(_minutes(row["intervals"]))
        candidate_rows = [row for row in rows if row.get("instrument") == instrument]
        candidate_expected: set[int] = set()
        candidate_close_dates: list[str] = []
        candidate_starts: list[int] = []
        candidate_ends: list[int] = []
        for row in candidate_rows:
            canonical_start, canonical_end = _interval(
                row.get("canonical_session"), "review_row.canonical_session"
            )
            candidate_starts.append(canonical_start)
            candidate_ends.append(canonical_end)
            candidate_close_dates.append(_close_date(canonical_end).isoformat())
            candidate_expected.update(
                _minutes(
                    [
                        _interval(item, "review_row.advertised_availability_intervals[]")
                        for item in _as_list(
                            row.get("advertised_availability_intervals"),
                            "review_row.advertised_availability_intervals",
                        )
                    ]
                )
            )
        candidate_horizon = set(range(min(candidate_starts), max(candidate_ends)))
        scenarios: list[dict[str, Any]] = []
        candidate_window_scenarios: list[dict[str, Any]] = []
        for scenario_id, offset, description in OFFSET_SCENARIOS:
            shifted_acquisition = _shift(acquisition, offset)
            shifted_observed = _shift(observed, offset)
            session_rows: list[dict[str, Any]] = []
            for hypothesis in hypothesis_rows:
                session_expected = _minutes(hypothesis["intervals"])
                session_rows.append(
                    {
                        "canonical_close_date": hypothesis["canonical_close_date"],
                        "candidate_open_date": hypothesis["candidate_open_date"],
                        "advertised_status_hypothesis": hypothesis["status"],
                        "origin": hypothesis["origin"],
                        "review_disposition": hypothesis["review_disposition"],
                        "metrics": _minute_metrics(
                            session_expected, shifted_acquisition, shifted_observed
                        ),
                    }
                )
            scenario_metrics = _minute_metrics(expected, shifted_acquisition, shifted_observed)
            scenario_metrics["observed_active_minutes_outside_expected_within_month"] = len(
                (shifted_observed & month_horizon) - expected
            )
            scenario_metrics["observed_active_minutes_outside_month_horizon"] = len(
                shifted_observed - month_horizon
            )
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "offset_minutes_applied_to_recorded_epoch": offset,
                    "description": description,
                    "status": "DIAGNOSTIC_ONLY_NO_BEST_FIT_SELECTION",
                    "aggregate": scenario_metrics,
                    "sessions": session_rows,
                }
            )
            candidate_metrics = _minute_metrics(
                candidate_expected, shifted_acquisition, shifted_observed
            )
            candidate_metrics[
                "observed_active_minutes_outside_expected_within_candidate_window"
            ] = len((shifted_observed & candidate_horizon) - candidate_expected)
            candidate_metrics["observed_active_minutes_outside_candidate_window"] = len(
                shifted_observed - candidate_horizon
            )
            candidate_window_scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "offset_minutes_applied_to_recorded_epoch": offset,
                    "aggregate": candidate_metrics,
                }
            )
        instrument_reports.append(
            {
                "instrument": instrument,
                "primary_date_view": "CANONICAL_NY_CLOSE_DATE_MONTH",
                "canonical_close_date_month": month_text,
                "advertised_hypothesis_minutes": len(expected),
                "hypothesis_rows": [
                    {
                        **{key: value for key, value in row.items() if key != "intervals"},
                        "advertised_intervals": [
                            [_minute_iso(start), _minute_iso(end)]
                            for start, end in row["intervals"]
                        ],
                    }
                    for row in hypothesis_rows
                ],
                "offset_scenarios": scenarios,
                "candidate_open_date_window": {
                    "classification": "SECONDARY_FIXED_CANDIDATE_SCOPE_NOT_SPEC_SESSION_DATE",
                    "candidate_open_date_month": month_text,
                    "canonical_close_date_range": [
                        min(candidate_close_dates),
                        max(candidate_close_dates),
                    ],
                    "advertised_hypothesis_minutes": len(candidate_expected),
                    "offset_scenarios": candidate_window_scenarios,
                },
                "source_checkpoint_flag_breakdown": _source_flag_summary(
                    instrument_scans, month_text
                ),
            }
        )

    for observation in all_frozen:
        _verify_observation_unchanged(observation)
    _ensure_unchanged(candidate_frozen, "calendar candidate")
    if thirty_day_frozen is not None:
        _ensure_unchanged(thirty_day_frozen, "30-day report")
    if _sha256_path(script_path) != script_sha256:
        raise DiagnosticError("diagnostic implementation changed during the run")

    candidate_available_at = _as_dict(
        candidate_frozen.payload.get("knowledge_policy"), "candidate.knowledge_policy"
    ).get("available_at_utc")
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at_utc": generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "gate_approved": False,
        "acceptance_status": "INDETERMINATE",
        "provisional_controls": {
            "calendar_approved": False,
            "candidate_loadable": False,
            "timestamp_interpretation_policy": "UNRESOLVED",
            "liquid_hours_definition": "UNRESOLVED",
            "quality_flag_budget_policy": "UNRESOLVED",
            "human_review": "NOT_PERFORMED_BY_THIS_DIAGNOSTIC",
            "ci_approval": "NOT_PERFORMED_BY_THIS_DIAGNOSTIC",
        },
        "scope": {
            "reference_month": month_text,
            "primary_month_key": "New York calendar date of canonical UTC close",
            "candidate_row_key": "candidate chunk/canonical-interval open date",
            "candidate_available_at_utc_preserved": candidate_available_at,
            "candidate_rows": len(rows),
            "candidate_observations": len(candidate_observations),
            "supplemental_boundary_observations": len(supplements),
            "availability_is_not_liquidity": True,
            "canonical_timestamps_mutated": False,
        },
        "inputs": {
            "implementation": {
                "path": script_path.as_posix(),
                "sha256": script_sha256,
                "runtime": {
                    "python": sys.version.split()[0],
                    "implementation": platform.python_implementation(),
                    "platform": platform.platform(),
                },
            },
            "calendar_candidate": {
                "path": candidate_path.as_posix(),
                "sha256": candidate_frozen.sha256,
                "approval_status": _as_dict(
                    candidate_frozen.payload.get("approval"), "candidate.approval"
                ).get("status"),
            },
            "frozen_probe_observations": {
                "status": "VERIFIED_BEFORE_AND_AFTER_STREAM",
                "lineage": lineage,
                "count": len(all_frozen),
                "total_tick_rows": sum(item.tick_count for item in all_frozen),
                "artifacts": [
                    {
                        "instrument": item.instrument,
                        "candidate_open_date": item.candidate_open_date.isoformat(),
                        "provenance": item.provenance,
                        "schema_version": item.checkpoint.get("schema_version"),
                        "probe_version": item.checkpoint.get("probe_version"),
                        "plan_hash": item.checkpoint.get("plan_hash"),
                        "window_id": _as_dict(item.checkpoint.get("chunk"), "checkpoint.chunk").get(
                            "window_id"
                        ),
                        "run_id": item.checkpoint.get("run_id"),
                        "environment_sha256": item.checkpoint.get("environment_sha256"),
                        "checkpoint_path": item.checkpoint_relative_path,
                        "checkpoint_sha256": item.checkpoint_sha256,
                        "raw_path": item.raw_relative_path,
                        "raw_compressed_sha256": item.raw_sha256,
                        "raw_semantic_sha256": item.raw_semantic_sha256,
                        "tick_rows": item.tick_count,
                    }
                    for item in sorted(
                        all_frozen,
                        key=lambda value: (value.instrument, value.candidate_open_date),
                    )
                ],
            },
            "completed_30_day_evidence": thirty_day,
        },
        "hypothesis": {
            "name": "archived_FBS_generic_advertised_availability",
            "classification": "PROVISIONAL_ADVERTISED_SESSION_NOT_EXPECTED_LIQUIDITY",
            "source_claims": candidate_frozen.payload.get("source_claims"),
            "archived_primary_source_verification": (
                "CANDIDATE_METADATA_ONLY_SOURCE_BYTES_NOT_REVERIFIED_BY_THIS_HELPER"
            ),
            "baseline_scenario": "canonical_epoch_utc",
            "baseline_basis": "MT5 copy_ticks_range documents UTC tick epochs and UTC requests.",
            "counterfactual_scenarios": [
                "server_gmt_plus_2_interpretation",
                "server_gmt_plus_3_interpretation",
            ],
            "offsets_are_sensitivity_only": True,
            "no_winning_or_best_fit_offset_selected": True,
        },
        "instruments": instrument_reports,
        "unresolved": [
            "FBS-Demo symbol-specific historical expected-liquid intervals remain unavailable.",
            "The correct historical timestamp interpretation has not been approved.",
            "A gate-counted P1 flag taxonomy/budget for the reference month has not been approved.",
            (
                "Zero-tick minutes challenge an advertised-availability hypothesis but do not "
                "prove a market outage."
            ),
            (
                "Hypothesized CLOSED rows without checkpoints do not prove absence outside "
                "advertised sessions."
            ),
        ],
    }


def _write_exclusive(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise DiagnosticError(f"refusing to overwrite diagnostic artifact: {path}") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Write a new immutable diagnostic report and checksum sidecar."""

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_dir.mkdir()
    except FileExistsError as exc:
        raise DiagnosticError(f"refusing to reuse diagnostic directory {output_dir}") from exc
    report_path = output_dir / "report.json"
    sidecar_path = output_dir / "report.sha256.json"
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_exclusive(report_path, encoded)
    sidecar = {
        "report.json": {
            "bytes": len(encoded),
            "sha256": _sha256_bytes(encoded),
        }
    }
    _write_exclusive(
        sidecar_path,
        (json.dumps(sidecar, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return report_path, sidecar_path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("build/gate1/reference/fbs-demo-october-2024.calendar-candidate.json"),
    )
    parser.add_argument("--probe-root", type=Path, default=Path("build/fbs-tick-continuity-v1"))
    parser.add_argument(
        "--thirty-day-report",
        type=Path,
        default=Path("build/gate1/30day-stable-b102ecdd/report.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build/gate1/reference-diagnostics-v1"),
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    report = build_diagnostic(
        candidate_path=args.candidate,
        probe_root=args.probe_root,
        thirty_day_report=args.thirty_day_report,
        generated_at=datetime.now(UTC),
    )
    report_path, sidecar_path = write_report(report, args.output_dir)
    print(
        json.dumps(
            {
                "acceptance_status": report["acceptance_status"],
                "gate_approved": report["gate_approved"],
                "report": report_path.as_posix(),
                "sidecar": sidecar_path.as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
