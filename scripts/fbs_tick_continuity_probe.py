r"""Bounded, resumable FBS MT5 tick-continuity acquisition probe.

RUN ON WINDOWS with a running, manually logged-in FBS demo terminal::

    py scripts\fbs_tick_continuity_probe.py ^
      --terminal "C:\Program Files\MetaTrader 5\terminal64.exe" ^
      --plan configs\probes\fbs_tick_continuity_v1.json ^
      --work-dir build\fbs-tick-continuity-v1 ^
      --output docs\reports\fbs-tick-continuity-probe.json

This is deliberately a source-viability probe, not the SPEC 4.2 Parquet raw layer
and not Gate-1 evidence.  Each one-session response is preserved source-faithfully
as deterministic, gzipped canonical rows under the ignored ``build/`` directory.
An atomic checkpoint makes the run resumable.  The same range is fetched twice;
changes are preserved and reported rather than silently overwritten.

No credentials or account identifiers are requested, read into the report, or
stored.  ``initialize(path=...)`` attaches to the existing terminal session and the
run refuses any disconnected, non-demo or non-FBS account.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException
from functools import partial
from pathlib import Path
from typing import Any, NoReturn, cast

import MetaTrader5 as mt5  # type: ignore[import-not-found]  # Windows-only wheel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tradebot.data.acquisition_probe import (
    CANONICAL_TICK_HEADER,
    AcquisitionPlan,
    ChunkEvidence,
    ChunkMetrics,
    ChunkRequest,
    RepeatFetchComparison,
    SourceTick,
    analyse_chunk,
    canonical_tick_lines,
    compare_repeat_fetches,
    encode_source_tick,
    parse_plan,
    summarise_dataset,
)

PROBE_VERSION = "fbs-tick-continuity-v1"
CHECKPOINT_SCHEMA_VERSION = 1
MT5_SUCCESS = 1
CALL_TIMEOUT = timedelta(seconds=90)
RUN_TIMEOUT = timedelta(hours=12)
TIMEOUT_EXIT_CODE = 75
REQUIRED_TICK_FIELDS = (
    "time",
    "bid",
    "ask",
    "last",
    "volume",
    "time_msc",
    "flags",
    "volume_real",
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_MODULE_PATH = REPOSITORY_ROOT / "src" / "tradebot" / "data" / "acquisition_probe.py"

_run_deadline: float | None = None


@dataclass(frozen=True, slots=True)
class Mt5Call:
    """One native result plus the worker-thread error snapshot and elapsed time."""

    value: Any
    error: tuple[int, str]
    elapsed_seconds: float


class ProbeTimeout(RuntimeError):
    """A native call outlived a bound and poisoned the process's MT5 session."""

    def __init__(self, label: str, waited_seconds: float) -> None:
        self.label = label
        self.waited_seconds = waited_seconds
        super().__init__(f"{label}: probe stopped waiting after {waited_seconds:.0f}s")


def _fail(message: str, error: tuple[int, str] | None = None) -> NoReturn:
    suffix = f": {error[0]}: {error[1]}" if error is not None else ""
    raise SystemExit(message + suffix)


def _remaining_wait() -> float:
    limits = [CALL_TIMEOUT.total_seconds()]
    if _run_deadline is not None:
        limits.append(max(0.0, _run_deadline - time.monotonic()))
    return min(limits)


def _bounded(label: str, call: Callable[[], Any]) -> Mt5Call:
    """Bound how long the probe waits for one uncancellable native MT5 call.

    The worker is a daemon.  A timeout poisons the session: callers must write a
    partial report and hard-exit without making another MT5 call, including shutdown.
    """

    outcome: list[Mt5Call] = []
    failure: list[BaseException] = []
    completed = threading.Event()
    started = time.monotonic()

    def _run() -> None:
        try:
            value = call()
            error = mt5.last_error()
            outcome.append(
                Mt5Call(
                    value=value,
                    error=(int(error[0]), str(error[1])),
                    elapsed_seconds=time.monotonic() - started,
                )
            )
        except BaseException as exc:
            failure.append(exc)
        finally:
            completed.set()

    wait_seconds = _remaining_wait()
    if wait_seconds <= 0:
        raise ProbeTimeout(label, 0.0)
    worker = threading.Thread(target=_run, daemon=True, name=label)
    try:
        worker.start()
        worker.join(wait_seconds)
        if not completed.is_set():
            raise ProbeTimeout(label, wait_seconds)
    except ProbeTimeout:
        raise
    except BaseException as exc:
        if not completed.is_set():
            raise ProbeTimeout(label, time.monotonic() - started) from exc
        raise
    if failure:
        raise failure[0]
    if not outcome:
        raise RuntimeError(f"{label} ended without a result or exception")
    return outcome[0]


def _hard_exit(code: int) -> NoReturn:
    os._exit(code)


def _partial_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}.partial{output.suffix}")


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_raw_artifact(path: Path) -> tuple[list[SourceTick], str, int]:
    """Parse canonical rows while hashing the exact uncompressed artifact."""

    digest = hashlib.sha256()
    ticks: list[SourceTick] = []
    uncompressed_bytes = 0
    with gzip.open(path, "rb") as stream:
        header = stream.readline()
        if header != CANONICAL_TICK_HEADER:
            raise ValueError(f"raw artifact {path} has an invalid canonical header")
        digest.update(header)
        uncompressed_bytes += len(header)
        for line_number, line in enumerate(stream, start=2):
            digest.update(line)
            uncompressed_bytes += len(line)
            try:
                fields = line.removesuffix(b"\n").decode("ascii").split("\t")
                if len(fields) != len(REQUIRED_TICK_FIELDS) or not line.endswith(b"\n"):
                    raise ValueError("wrong field count or line ending")
                tick = SourceTick(
                    time=int(fields[0]),
                    time_msc=int(fields[1]),
                    bid=Decimal(fields[2]),
                    ask=Decimal(fields[3]),
                    last=Decimal(fields[4]),
                    volume=int(fields[5]),
                    flags=int(fields[6]),
                    volume_real=Decimal(fields[7]),
                )
            except (DecimalException, UnicodeError, ValueError) as exc:
                raise ValueError(f"invalid canonical raw row {line_number} in {path}") from exc
            if encode_source_tick(tick) != line:
                raise ValueError(f"non-canonical raw row {line_number} in {path}")
            ticks.append(tick)
    return ticks, digest.hexdigest(), uncompressed_bytes


def _json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_raw_atomic(path: Path, ticks: Sequence[SourceTick]) -> dict[str, Any]:
    """Write deterministic canonical source rows and return both content hashes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    semantic = hashlib.sha256()
    uncompressed_bytes = 0
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                for line in canonical_tick_lines(ticks):
                    semantic.update(line)
                    uncompressed_bytes += len(line)
                    compressed.write(line)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "format": "tradebot-source-ticks-semantic-v1-tsv-gzip",
        "path": path.name,
        "semantic_sha256": semantic.hexdigest(),
        "compressed_sha256": _sha256_path(path),
        "compressed_bytes": path.stat().st_size,
        "uncompressed_bytes": uncompressed_bytes,
    }


def _epoch_milliseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return ((delta.days * 86_400 + delta.seconds) * 1000) + delta.microseconds // 1000


def _source_ticks(raw_ticks: Any, request: ChunkRequest) -> tuple[list[SourceTick], dict[str, Any]]:
    names = tuple(str(name) for name in (getattr(raw_ticks.dtype, "names", None) or ()))
    missing = sorted(set(REQUIRED_TICK_FIELDS) - set(names))
    unexpected = sorted(set(names) - set(REQUIRED_TICK_FIELDS))
    if missing or unexpected:
        raise ValueError(
            f"MT5 tick response schema differs from the canonical source schema; "
            f"missing={missing}, unexpected={unexpected}"
        )

    start_msc = _epoch_milliseconds(request.start)
    end_msc = _epoch_milliseconds(request.end)
    before = 0
    exactly_at_end = 0
    after_end = 0
    ticks: list[SourceTick] = []
    for row in raw_ticks:
        stamp = int(row["time_msc"])
        if stamp < start_msc:
            before += 1
            continue
        if stamp == end_msc:
            exactly_at_end += 1
            continue
        if stamp > end_msc:
            after_end += 1
            continue
        ticks.append(
            SourceTick(
                time=int(row["time"]),
                time_msc=stamp,
                bid=Decimal(str(row["bid"])),
                ask=Decimal(str(row["ask"])),
                last=Decimal(str(row["last"])),
                volume=int(row["volume"]),
                flags=int(row["flags"]),
                volume_real=Decimal(str(row["volume_real"])),
            )
        )
    if before or after_end:
        raise ValueError(
            f"MT5 returned out-of-range rows for {request.chunk_id}: "
            f"before_start={before}, after_end={after_end}"
        )
    return ticks, {
        "dtype_names": list(names),
        "returned_rows": len(raw_ticks),
        "discarded_before_start": before,
        "discarded_exactly_at_end": exactly_at_end,
        "discarded_after_end": after_end,
    }


def _fetch_ticks(request: ChunkRequest) -> tuple[list[SourceTick], dict[str, Any]]:
    response = _bounded(
        f"ticks:{request.chunk_id}",
        lambda: mt5.copy_ticks_range(
            request.broker_symbol,
            request.start,
            request.end,
            mt5.COPY_TICKS_ALL,
        ),
    )
    if response.value is None or response.error[0] != MT5_SUCCESS:
        _fail(f"copy_ticks_range failed for {request.chunk_id}", response.error)
    ticks, shape = _source_ticks(response.value, request)
    shape["elapsed_seconds"] = response.elapsed_seconds
    shape["rows_per_second"] = (
        len(ticks) / response.elapsed_seconds if response.elapsed_seconds > 0 else None
    )
    shape["mt5_error_snapshot"] = {"code": response.error[0], "text": response.error[1]}
    return ticks, shape


def _metrics_payload(metrics: ChunkMetrics) -> dict[str, Any]:
    payload = asdict(metrics)
    payload["flag_counts"] = [list(item) for item in metrics.flag_counts]
    payload["positive_spread_counts"] = [list(item) for item in metrics.positive_spread_counts]
    return payload


def _metrics_from_payload(payload: Mapping[str, Any]) -> ChunkMetrics:
    values = dict(payload)
    values["flag_counts"] = tuple(
        (int(item[0]), int(item[1]))
        for item in cast(Sequence[Sequence[Any]], values["flag_counts"])
    )
    values["positive_spread_counts"] = tuple(
        (str(item[0]), int(item[1]))
        for item in cast(Sequence[Sequence[Any]], values["positive_spread_counts"])
    )
    return ChunkMetrics(**values)


def _comparison_payload(comparison: RepeatFetchComparison) -> dict[str, Any]:
    return asdict(comparison)


def _chunk_payload(evidence: ChunkEvidence) -> dict[str, Any]:
    request = evidence.request
    return {
        "chunk_id": evidence.chunk_id,
        "logical_symbol": request.logical_symbol,
        "broker_symbol": request.broker_symbol,
        "window_id": request.window_id,
        "session_date": request.session_date.isoformat(),
        "start_utc": request.start.isoformat().replace("+00:00", "Z"),
        "end_utc": request.end.isoformat().replace("+00:00", "Z"),
        "semantic_sha256": evidence.semantic_sha256,
        "metrics": _metrics_payload(evidence.metrics),
    }


def _raw_path(work_root: Path, plan: AcquisitionPlan, request: ChunkRequest) -> Path:
    return (
        work_root
        / plan.plan_hash
        / request.logical_symbol
        / request.window_id
        / f"{request.session_date.isoformat()}.source-ticks.tsv.gz"
    )


def _repeat_raw_path(
    work_root: Path, plan: AcquisitionPlan, request: ChunkRequest, repeat: int
) -> Path:
    return _raw_path(work_root, plan, request).with_name(
        f"{request.session_date.isoformat()}.repeat-{repeat}.source-ticks.tsv.gz"
    )


def _checkpoint_path(work_root: Path, plan: AcquisitionPlan, request: ChunkRequest) -> Path:
    return _raw_path(work_root, plan, request).with_suffix(".checkpoint.json")


def _relative_artifact(path: Path, work_root: Path) -> str:
    return path.relative_to(work_root).as_posix()


def _acquire_chunk(
    plan: AcquisitionPlan,
    request: ChunkRequest,
    work_root: Path,
    *,
    run_id: str,
    environment: Mapping[str, Any],
) -> tuple[ChunkEvidence, dict[str, Any]]:
    fetches: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    first_ticks, first_shape = _fetch_ticks(request)
    first_evidence = analyse_chunk(
        request,
        first_ticks,
        bid_flag_mask=int(mt5.TICK_FLAG_BID),
        ask_flag_mask=int(mt5.TICK_FLAG_ASK),
    )
    raw_path = _raw_path(work_root, plan, request)
    raw = _write_raw_atomic(raw_path, first_ticks)
    if raw["semantic_sha256"] != first_evidence.semantic_sha256:
        raise RuntimeError(f"raw semantic hash mismatch while writing {request.chunk_id}")
    raw["path"] = _relative_artifact(raw_path, work_root)
    fetches.append(
        {
            "repeat": 1,
            "run_id": run_id,
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "shape": first_shape,
            "semantic_sha256": first_evidence.semantic_sha256,
            "tick_count": first_evidence.metrics.tick_count,
            "metrics": _metrics_payload(first_evidence.metrics),
        }
    )

    for repeat in range(2, plan.repeat_fetches + 1):
        repeated_ticks, repeated_shape = _fetch_ticks(request)
        comparison = compare_repeat_fetches(request, first_ticks, repeated_ticks)
        comparisons.append(_comparison_payload(comparison))
        repeated_evidence = analyse_chunk(
            request,
            repeated_ticks,
            bid_flag_mask=int(mt5.TICK_FLAG_BID),
            ask_flag_mask=int(mt5.TICK_FLAG_ASK),
        )
        fetch_record: dict[str, Any] = {
            "repeat": repeat,
            "run_id": run_id,
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "shape": repeated_shape,
            "semantic_sha256": repeated_evidence.semantic_sha256,
            "tick_count": repeated_evidence.metrics.tick_count,
            "metrics": _metrics_payload(repeated_evidence.metrics),
        }
        if not comparison.identical:
            repeated_path = _repeat_raw_path(work_root, plan, request, repeat)
            repeated_raw = _write_raw_atomic(repeated_path, repeated_ticks)
            repeated_raw["path"] = _relative_artifact(repeated_path, work_root)
            fetch_record["preserved_raw"] = repeated_raw
        fetches.append(fetch_record)

    checkpoint: dict[str, Any] = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "probe_version": PROBE_VERSION,
        "plan_hash": plan.plan_hash,
        "source": plan.source,
        "environment": dict(environment),
        "environment_sha256": _json_sha256(environment),
        "run_id": run_id,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "chunk": _chunk_payload(first_evidence),
        "raw": raw,
        "fetches": fetches,
        "repeat_comparisons": comparisons,
    }
    checkpoint["integrity"] = {
        "algorithm": "sha256",
        "payload_sha256": _json_sha256(checkpoint),
    }
    _write_json_atomic(_checkpoint_path(work_root, plan, request), checkpoint)
    return first_evidence, checkpoint


def _required_mapping(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be an object with string keys")
    return cast(dict[str, Any], value)


def _required_list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list")
    return value


def _validate_raw_reference(
    reference: Mapping[str, Any],
    *,
    expected_path: Path,
    work_root: Path,
    semantic_sha256: object,
    context: str,
) -> list[SourceTick]:
    if reference.get("format") != "tradebot-source-ticks-semantic-v1-tsv-gzip":
        raise ValueError(f"{context} format mismatch")
    if reference.get("path") != _relative_artifact(expected_path, work_root):
        raise ValueError(f"{context} artifact path mismatch")
    if not expected_path.is_file():
        raise ValueError(f"{context} artifact is missing")
    if _sha256_path(expected_path) != reference.get("compressed_sha256"):
        raise ValueError(f"{context} compressed checksum mismatch")
    if expected_path.stat().st_size != reference.get("compressed_bytes"):
        raise ValueError(f"{context} compressed size mismatch")
    ticks, semantic, uncompressed_bytes = _read_raw_artifact(expected_path)
    if semantic != reference.get("semantic_sha256") or semantic != semantic_sha256:
        raise ValueError(f"{context} semantic checksum mismatch")
    if uncompressed_bytes != reference.get("uncompressed_bytes"):
        raise ValueError(f"{context} uncompressed size mismatch")
    return ticks


def _load_checkpoint(
    plan: AcquisitionPlan,
    request: ChunkRequest,
    work_root: Path,
    environment: Mapping[str, Any],
) -> tuple[ChunkEvidence, dict[str, Any]] | None:
    checkpoint_path = _checkpoint_path(work_root, plan, request)
    if not checkpoint_path.is_file():
        return None
    payload = _required_mapping(
        json.loads(checkpoint_path.read_text(encoding="utf-8")),
        f"checkpoint for {request.chunk_id}",
    )
    expected_keys = {
        "schema_version",
        "probe_version",
        "plan_hash",
        "source",
        "environment",
        "environment_sha256",
        "run_id",
        "completed_at_utc",
        "chunk",
        "raw",
        "fetches",
        "repeat_comparisons",
        "integrity",
    }
    if set(payload) != expected_keys:
        raise ValueError(f"checkpoint fields mismatch for {request.chunk_id}")
    integrity = _required_mapping(payload.get("integrity"), "checkpoint integrity")
    if set(integrity) != {"algorithm", "payload_sha256"}:
        raise ValueError(f"checkpoint integrity fields mismatch for {request.chunk_id}")
    unsigned = {key: value for key, value in payload.items() if key != "integrity"}
    if integrity.get("algorithm") != "sha256" or integrity.get("payload_sha256") != _json_sha256(
        unsigned
    ):
        raise ValueError(f"checkpoint payload checksum mismatch for {request.chunk_id}")
    if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError(f"checkpoint schema mismatch for {request.chunk_id}")
    if (
        payload.get("probe_version") != PROBE_VERSION
        or payload.get("plan_hash") != plan.plan_hash
        or payload.get("source") != plan.source
    ):
        raise ValueError(f"checkpoint identity mismatch for {request.chunk_id}")
    checkpoint_environment = _required_mapping(payload.get("environment"), "checkpoint environment")
    if checkpoint_environment != environment or payload.get("environment_sha256") != _json_sha256(
        environment
    ):
        raise ValueError(f"checkpoint environment mismatch for {request.chunk_id}")
    chunk = _required_mapping(payload.get("chunk"), "checkpoint chunk")
    expected_chunk_fields = {
        "chunk_id": request.chunk_id,
        "logical_symbol": request.logical_symbol,
        "broker_symbol": request.broker_symbol,
        "window_id": request.window_id,
        "session_date": request.session_date.isoformat(),
        "start_utc": request.start.isoformat().replace("+00:00", "Z"),
        "end_utc": request.end.isoformat().replace("+00:00", "Z"),
    }
    if any(chunk.get(key) != value for key, value in expected_chunk_fields.items()):
        raise ValueError(f"checkpoint chunk mismatch for {request.chunk_id}")
    raw = _required_mapping(payload.get("raw"), "checkpoint raw reference")
    expected_raw = _raw_path(work_root, plan, request)
    primary_ticks = _validate_raw_reference(
        raw,
        expected_path=expected_raw,
        work_root=work_root,
        semantic_sha256=chunk.get("semantic_sha256"),
        context=f"primary raw for {request.chunk_id}",
    )
    fetches = _required_list(payload.get("fetches"), "checkpoint fetches")
    comparisons = _required_list(payload.get("repeat_comparisons"), "checkpoint repeat comparisons")
    if len(fetches) != plan.repeat_fetches or len(comparisons) != plan.repeat_fetches - 1:
        raise ValueError(f"checkpoint repeat count mismatch for {request.chunk_id}")
    primary_fetch = _required_mapping(fetches[0], "checkpoint primary fetch")
    if primary_fetch.get("repeat") != 1 or primary_fetch.get("semantic_sha256") != chunk.get(
        "semantic_sha256"
    ):
        raise ValueError(f"checkpoint primary fetch mismatch for {request.chunk_id}")
    primary_metrics = _metrics_from_payload(
        _required_mapping(primary_fetch.get("metrics"), "checkpoint primary fetch metrics")
    )
    primary_evidence = analyse_chunk(
        request,
        primary_ticks,
        bid_flag_mask=int(mt5.TICK_FLAG_BID),
        ask_flag_mask=int(mt5.TICK_FLAG_ASK),
    )
    if (
        primary_evidence.semantic_sha256 != chunk.get("semantic_sha256")
        or primary_evidence.metrics != primary_metrics
        or primary_evidence.metrics
        != _metrics_from_payload(
            _required_mapping(chunk.get("metrics"), "checkpoint chunk metrics")
        )
        or primary_evidence.metrics.tick_count != primary_fetch.get("tick_count")
    ):
        raise ValueError(f"checkpoint primary metrics mismatch for {request.chunk_id}")
    for repeat, (raw_fetch, raw_comparison) in enumerate(
        zip(fetches[1:], comparisons, strict=True), start=2
    ):
        fetch = _required_mapping(raw_fetch, f"checkpoint fetch {repeat}")
        comparison = _required_mapping(raw_comparison, f"checkpoint comparison {repeat - 1}")
        identical = comparison.get("identical")
        if type(identical) is not bool:
            raise ValueError(f"checkpoint comparison verdict invalid for {request.chunk_id}")
        if (
            fetch.get("repeat") != repeat
            or comparison.get("chunk_id") != request.chunk_id
            or comparison.get("first_sha256") != chunk.get("semantic_sha256")
            or comparison.get("second_sha256") != fetch.get("semantic_sha256")
            or comparison.get("first_count") != primary_fetch.get("tick_count")
            or comparison.get("second_count") != fetch.get("tick_count")
        ):
            raise ValueError(f"checkpoint comparison mismatch for {request.chunk_id}")
        repeat_metrics = _metrics_from_payload(
            _required_mapping(fetch.get("metrics"), f"checkpoint fetch {repeat} metrics")
        )
        preserved = fetch.get("preserved_raw")
        if identical:
            if preserved is not None:
                raise ValueError(f"identical repeat unexpectedly preserved for {request.chunk_id}")
            repeated_ticks = primary_ticks
        else:
            preserved_reference = _required_mapping(
                preserved, f"checkpoint preserved repeat {repeat}"
            )
            repeated_ticks = _validate_raw_reference(
                preserved_reference,
                expected_path=_repeat_raw_path(work_root, plan, request, repeat),
                work_root=work_root,
                semantic_sha256=fetch.get("semantic_sha256"),
                context=f"repeat {repeat} raw for {request.chunk_id}",
            )
        repeated_evidence = analyse_chunk(
            request,
            repeated_ticks,
            bid_flag_mask=int(mt5.TICK_FLAG_BID),
            ask_flag_mask=int(mt5.TICK_FLAG_ASK),
        )
        recomputed_comparison = _comparison_payload(
            compare_repeat_fetches(request, primary_ticks, repeated_ticks)
        )
        if (
            repeated_evidence.semantic_sha256 != fetch.get("semantic_sha256")
            or repeated_evidence.metrics != repeat_metrics
            or repeated_evidence.metrics.tick_count != fetch.get("tick_count")
            or comparison != recomputed_comparison
        ):
            raise ValueError(f"checkpoint repeat evidence mismatch for {request.chunk_id}")
    return primary_evidence, payload


def _is_fbs_demo(account: Any) -> bool:
    identities = (
        str(getattr(account, "company", "")).strip().casefold(),
        str(getattr(account, "server", "")).strip().casefold(),
    )
    return any(value == "fbs" or value.startswith(("fbs ", "fbs-")) for value in identities)


def _git_sha() -> str:
    executable = shutil.which("git")
    if executable is None:
        return "UNKNOWN"
    try:
        # No shell and no user-controlled arguments: the resolved executable receives
        # one fixed read-only Git command.
        result = subprocess.run(  # noqa: S603
            [executable, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=REPOSITORY_ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    return result.stdout.strip() or "UNKNOWN"


def _spec_sha256() -> str | None:
    path = REPOSITORY_ROOT / "docs" / "SPEC.md"
    return _sha256_path(path) if path.is_file() else None


def _plan_payload(plan: AcquisitionPlan, config_name: str) -> dict[str, Any]:
    return {
        "config_name": config_name,
        "plan_hash": plan.plan_hash,
        "probe_id": plan.probe_id,
        "source": plan.source,
        "purpose": plan.purpose,
        "repeat_fetches": plan.repeat_fetches,
        "chunk_sessions": plan.chunk_sessions,
        "session_boundary": "17:00 America/New_York",
        "expected_chunks": len(plan.chunks),
        "symbols": {item.logical: item.broker for item in plan.symbols},
        "windows": [
            {
                "id": item.id,
                "purpose": item.purpose,
                "start_session_date": item.start_session_date.isoformat(),
                "end_session_date_exclusive": item.end_session_date_exclusive.isoformat(),
                "expected_sessions_per_symbol": len(item.iter_session_dates()),
            }
            for item in plan.windows
        ],
    }


def _summary_payload(plan: AcquisitionPlan, evidence: Sequence[ChunkEvidence]) -> dict[str, Any]:
    summary = summarise_dataset(plan, evidence)
    payload = asdict(summary)
    payload["flag_counts"] = [list(item) for item in summary.flag_counts]
    payload["positive_spread_counts"] = [list(item) for item in summary.positive_spread_counts]
    return payload


def _window_summaries(
    plan: AcquisitionPlan,
    evidence: Sequence[ChunkEvidence],
    checkpoints: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[ChunkEvidence]] = defaultdict(list)
    for item in evidence:
        grouped[(item.request.logical_symbol, item.request.window_id)].append(item)
    result: dict[str, Any] = {}
    for symbol in plan.symbols:
        symbol_payload: dict[str, Any] = {}
        for window in plan.windows:
            items = sorted(
                grouped.get((symbol.logical, window.id), []),
                key=lambda item: item.request.session_date,
            )
            metrics = summarise_dataset(plan, items)
            total = sum(item.metrics.tick_count for item in items)
            repeat_mismatches = sum(
                not bool(comparison["identical"])
                for item in items
                for comparison in cast(
                    Sequence[Mapping[str, Any]],
                    checkpoints[item.chunk_id].get("repeat_comparisons", []),
                )
            )
            symbol_payload[window.id] = {
                "expected_sessions": len(window.iter_session_dates()),
                "observed_sessions": len(items),
                "empty_sessions": [
                    item.request.session_date.isoformat()
                    for item in items
                    if item.metrics.tick_count == 0
                ],
                "tick_count": total,
                "active_minutes": metrics.active_minutes,
                "both_sides_positive_fraction": (
                    sum(item.metrics.both_sides_positive for item in items) / total
                    if total
                    else None
                ),
                "crossed_or_locked_count": sum(
                    item.metrics.crossed_quotes + item.metrics.locked_quotes for item in items
                ),
                "timestamp_regressions": sum(item.metrics.timestamp_regressions for item in items),
                "time_field_mismatches": sum(item.metrics.time_field_mismatches for item in items),
                "repeat_fetch_mismatches": repeat_mismatches,
                "max_intrasession_intertick_gap_milliseconds": max(
                    (
                        item.metrics.max_intertick_gap_milliseconds
                        for item in items
                        if item.metrics.max_intertick_gap_milliseconds is not None
                    ),
                    default=None,
                ),
                "positive_spread_quotes": metrics.positive_spread_quotes,
                "positive_spread_min": metrics.positive_spread_min,
                "positive_spread_p50": metrics.positive_spread_p50,
                "positive_spread_p95": metrics.positive_spread_p95,
                "positive_spread_p99": metrics.positive_spread_p99,
                "positive_spread_max": metrics.positive_spread_max,
            }
        result[symbol.logical] = symbol_payload
    return result


def _response_shape_summary(checkpoints: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    totals = {
        "native_responses": 0,
        "returned_rows": 0,
        "retained_half_open_rows": 0,
        "discarded_before_start": 0,
        "discarded_exactly_at_end": 0,
        "discarded_after_end": 0,
    }
    for checkpoint in checkpoints.values():
        for raw_fetch in cast(Sequence[object], checkpoint.get("fetches", [])):
            if not isinstance(raw_fetch, Mapping):
                continue
            shape = raw_fetch.get("shape")
            if not isinstance(shape, Mapping):
                continue
            totals["native_responses"] += 1
            totals["returned_rows"] += int(shape.get("returned_rows", 0))
            totals["discarded_before_start"] += int(shape.get("discarded_before_start", 0))
            totals["discarded_exactly_at_end"] += int(shape.get("discarded_exactly_at_end", 0))
            totals["discarded_after_end"] += int(shape.get("discarded_after_end", 0))
            totals["retained_half_open_rows"] += int(raw_fetch.get("tick_count", 0))
    return totals


def _all_fetch_metric_totals(
    checkpoints: Mapping[str, Mapping[str, Any]], fields: Sequence[str]
) -> dict[str, int]:
    totals = {field: 0 for field in fields}
    for checkpoint in checkpoints.values():
        for raw_fetch in cast(Sequence[object], checkpoint.get("fetches", [])):
            if not isinstance(raw_fetch, Mapping):
                continue
            metrics = raw_fetch.get("metrics")
            if not isinstance(metrics, Mapping):
                continue
            for field in fields:
                totals[field] += int(metrics.get(field, 0))
    return totals


def _populate_progress_report(
    report: dict[str, Any],
    plan: AcquisitionPlan,
    evidence: Mapping[str, ChunkEvidence],
    checkpoints: Mapping[str, Mapping[str, Any]],
    *,
    resumed: int,
    acquired: int,
) -> list[ChunkEvidence]:
    ordered = [evidence[item.chunk_id] for item in plan.chunks if item.chunk_id in evidence]
    report["resume"] = {
        "checkpoint_chunks_reused": resumed,
        "new_chunks": acquired,
        "resume_exercised": resumed > 0 and acquired > 0,
    }
    report["dataset"] = _summary_payload(plan, ordered)
    report["windows"] = _window_summaries(plan, ordered, checkpoints)
    report["response_shapes"] = _response_shape_summary(checkpoints)
    report["chunks"] = {
        chunk_id: {
            "evidence": checkpoint["chunk"],
            "raw": checkpoint["raw"],
            "fetches": checkpoint["fetches"],
            "repeat_comparisons": checkpoint["repeat_comparisons"],
        }
        for chunk_id, checkpoint in sorted(checkpoints.items())
    }
    return ordered


def _acquisition_order(plan: AcquisitionPlan) -> tuple[ChunkRequest, ...]:
    """Run one oldest canary per symbol, then work newest-to-oldest."""

    canaries: list[ChunkRequest] = []
    for symbol in plan.symbols:
        canaries.append(next(item for item in plan.chunks if item.logical_symbol == symbol.logical))
    canary_ids = {item.chunk_id for item in canaries}
    remainder = sorted(
        (item for item in plan.chunks if item.chunk_id not in canary_ids),
        key=lambda item: (item.start, item.logical_symbol, item.window_id),
        reverse=True,
    )
    return tuple(canaries + remainder)


def _base_report(plan: AcquisitionPlan, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": "RUNNING",
        "probe_version": PROBE_VERSION,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "run_id": uuid.uuid4().hex,
        "git_sha": _git_sha(),
        "spec_sha256": _spec_sha256(),
        "package_version": str(mt5.__version__),
        "code_sha256": {
            "runner": _sha256_path(Path(__file__).resolve()),
            "analysis_module": _sha256_path(ANALYSIS_MODULE_PATH),
        },
        "plan": _plan_payload(plan, Path(args.plan).name),
        "terminal": {},
        "account": {},
        "resume": {},
        "limitations": [
            "This is source-viability evidence, not the SPEC 4.2 Parquet raw/clean layer and "
            "not Gate-1 evidence.",
            "Raw diagnostic chunks live under ignored build/ storage; the committed report "
            "contains aggregate metrics and hashes, not licensed raw ticks.",
            "No expected-liquidity calendar or Principal-signed data-quality thresholds are "
            "supplied, so quality remains INDETERMINATE even when structural checks are clean.",
            "A repeated broker fetch can legitimately revise; both hashes and the differing "
            "response are preserved rather than calling vendor-sync behavior a rebuild failure.",
            "A completed bounded corpus never proves continuity outside the selected sessions.",
            "Cross-source agreement is NOT_EVALUABLE until an authorised second source exists.",
            "Historical MT5 rows have no local receipt timestamp; the future clean layer must "
            "impute ts_recv=ts_event and flag TS_RECV_IMPUTED.",
            "Resume checkpoints are bound to broker server, terminal/package build, Git/spec "
            "revision and runner hashes; a changed environment requires a new probe plan or "
            "work directory rather than an in-place refresh.",
            "Native range responses are partitioned into half-open sessions. Boundary rows at "
            "the exclusive end are counted and excluded from the current chunk; unexpected rows "
            "before the start or after the end fail the acquisition.",
        ],
    }


def _evidence_environment(report: Mapping[str, Any]) -> dict[str, Any]:
    terminal = _required_mapping(report.get("terminal"), "report terminal")
    account = _required_mapping(report.get("account"), "report account")
    code = _required_mapping(report.get("code_sha256"), "report code hashes")
    return {
        "probe_version": report["probe_version"],
        "git_sha": report["git_sha"],
        "spec_sha256": report["spec_sha256"],
        "mt5_package_version": report["package_version"],
        "runner_sha256": code["runner"],
        "analysis_module_sha256": code["analysis_module"],
        "terminal_build": terminal["build"],
        "terminal_name": terminal["name"],
        "terminal_company": terminal["company"],
        "account_server": account["server"],
        "account_company": account["company"],
        "account_is_demo": account["is_demo"],
    }


def _partial_report(
    report: dict[str, Any],
    output: Path,
    *,
    kind: str,
    detail: Mapping[str, Any],
) -> None:
    report["status"] = "PARTIAL"
    report["completed_at_utc"] = datetime.now(UTC).isoformat()
    report["failure"] = {"kind": kind} | dict(detail)
    report["canonical_output_preserved"] = output.name
    _write_json_atomic(_partial_path(output), report)


def _poisoned_exit(report: dict[str, Any], output: Path, timeout: ProbeTimeout) -> NoReturn:
    try:
        _partial_report(
            report,
            output,
            kind="TIMEOUT",
            detail={
                "call": timeout.label,
                "error": str(timeout),
                "mt5_session_poisoned": True,
            },
        )
    finally:
        _hard_exit(TIMEOUT_EXIT_CODE)


def _load_plan(path: Path) -> AcquisitionPlan:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("plan root must be an object")
    return parse_plan(cast(Mapping[str, object], payload))


def _repository_path(path: Path) -> Path:
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _validate_work_dir(path: Path, *, allow_external: bool) -> None:
    resolved = path.resolve(strict=False)
    ignored_build = (REPOSITORY_ROOT / "build").resolve(strict=False)
    if not allow_external and resolved != ignored_build and ignored_build not in resolved.parents:
        raise ValueError(
            "work-dir must resolve under the repository's ignored build/ directory; "
            "use --allow-external-work-dir only for an intentional external location"
        )


@contextmanager
def _work_dir_lock(work_root: Path) -> Iterator[None]:
    """Hold an OS-released lock across checkpoint and report publication."""

    work_root.mkdir(parents=True, exist_ok=True)
    lock_path = work_root / ".fbs-tick-continuity-probe.lock"
    stream = lock_path.open("a+b")
    if stream.seek(0, os.SEEK_END) == 0:
        stream.write(b"\0")
        stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            msvcrt: Any = importlib.import_module("msvcrt")
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl: Any = importlib.import_module("fcntl")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise SystemExit(f"another continuity probe holds {lock_path}") from exc
    try:
        stream.seek(0)
        stream.truncate()
        stream.write(f"pid={os.getpid()}\n".encode("ascii"))
        stream.flush()
        yield
    finally:
        try:
            stream.seek(0)
            if os.name == "nt":
                msvcrt = importlib.import_module("msvcrt")
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal", required=True, help="path to terminal64.exe")
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path("configs/probes/fbs_tick_continuity_v1.json"),
    )
    parser.add_argument("--work-dir", type=Path, default=Path("build/fbs-tick-continuity-v1"))
    parser.add_argument(
        "--allow-external-work-dir",
        action="store_true",
        help="explicitly permit raw diagnostics outside this repository's ignored build/ path",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("docs/reports/fbs-tick-continuity-probe.json")
    )
    parser.add_argument(
        "--max-new-chunks",
        type=int,
        help="intentional resumability stop after this many newly completed chunks",
    )
    return parser.parse_args()


def _run_main(args: argparse.Namespace) -> None:
    global _run_deadline

    terminal_path = Path(args.terminal)
    plan_path = _repository_path(Path(args.plan))
    work_root = _repository_path(Path(args.work_dir))
    output = _repository_path(Path(args.output))
    if not terminal_path.is_file():
        raise SystemExit(f"terminal not found at {terminal_path}")
    if not plan_path.is_file():
        raise SystemExit(f"plan not found at {plan_path}")
    _validate_work_dir(work_root, allow_external=bool(args.allow_external_work_dir))
    if args.max_new_chunks is not None and args.max_new_chunks < 1:
        raise SystemExit("--max-new-chunks must be at least 1")

    plan = _load_plan(plan_path)
    if not plan.source.casefold().startswith("fbs"):
        raise SystemExit(f"refusing to label an FBS acquisition as source {plan.source!r}")
    report = _base_report(plan, args)
    initialized = False
    identity_validated = False
    evidence: dict[str, ChunkEvidence] = {}
    checkpoints: dict[str, dict[str, Any]] = {}
    resumed = 0
    acquired = 0
    _run_deadline = time.monotonic() + RUN_TIMEOUT.total_seconds()
    try:
        initialized_call = _bounded("initialize", lambda: mt5.initialize(path=str(terminal_path)))
        if not initialized_call.value:
            _fail(
                "initialize() failed — is the terminal running and signed in?",
                initialized_call.error,
            )
        initialized = True
        terminal_call = _bounded("terminal-info", mt5.terminal_info)
        account_call = _bounded("account-info", mt5.account_info)
        terminal, account = terminal_call.value, account_call.value
        if terminal is None:
            _fail("terminal_info() returned nothing", terminal_call.error)
        if account is None:
            _fail("account_info() returned nothing", account_call.error)
        if not bool(terminal.connected):
            _fail("refusing to run: terminal is disconnected")
        if int(account.trade_mode) != int(mt5.ACCOUNT_TRADE_MODE_DEMO):
            _fail(
                f"refusing to run: attached account is not a demo (trade_mode={account.trade_mode})"
            )
        if not _is_fbs_demo(account):
            _fail(
                "refusing to run: attached demo does not identify as FBS "
                f"(server={account.server!r}, company={account.company!r})"
            )
        identity_validated = True
        report["terminal"] = {
            "build": int(terminal.build),
            "name": str(terminal.name),
            "company": str(terminal.company),
            "connected": bool(terminal.connected),
            "trade_allowed": bool(terminal.trade_allowed),
        }
        report["account"] = {
            "server": str(account.server),
            "currency": str(account.currency),
            "company": str(account.company),
            "is_demo": True,
        }

        for symbol in plan.symbols:
            broker_symbol = symbol.broker
            selected = _bounded(
                f"symbol-select:{broker_symbol}",
                partial(mt5.symbol_select, broker_symbol, True),
            )
            if not selected.value:
                _fail(f"could not select exact broker symbol {symbol.broker}", selected.error)

        environment = _evidence_environment(report)
        report["evidence_environment_sha256"] = _json_sha256(environment)
        for request in plan.chunks:
            loaded = _load_checkpoint(plan, request, work_root, environment)
            if loaded is not None:
                item, checkpoint = loaded
                evidence[item.chunk_id] = item
                checkpoints[item.chunk_id] = checkpoint
                resumed += 1

        for request in _acquisition_order(plan):
            if request.chunk_id in evidence:
                continue
            print(
                f"fetch {request.logical_symbol:6} {request.window_id:27} "
                f"{request.session_date.isoformat()}",
                flush=True,
            )
            item, checkpoint = _acquire_chunk(
                plan,
                request,
                work_root,
                run_id=str(report["run_id"]),
                environment=environment,
            )
            evidence[item.chunk_id] = item
            checkpoints[item.chunk_id] = checkpoint
            acquired += 1
            if args.max_new_chunks is not None and acquired >= args.max_new_chunks:
                break

        shutdown = _bounded("shutdown", mt5.shutdown)
        initialized = False
        if shutdown.value is False:
            _fail("mt5.shutdown() reported failure", shutdown.error)
    except ProbeTimeout as timeout:
        if identity_validated:
            try:
                _populate_progress_report(
                    report,
                    plan,
                    evidence,
                    checkpoints,
                    resumed=resumed,
                    acquired=acquired,
                )
            except Exception as progress_error:
                report["progress_snapshot_error"] = (
                    f"{type(progress_error).__name__}: {progress_error}"
                )
        _poisoned_exit(report, output, timeout)
    except BaseException as exc:
        if initialized:
            try:
                _bounded("shutdown-after-error", mt5.shutdown)
                initialized = False
            except ProbeTimeout as timeout:
                _poisoned_exit(report, output, timeout)
        if identity_validated:
            try:
                _populate_progress_report(
                    report,
                    plan,
                    evidence,
                    checkpoints,
                    resumed=resumed,
                    acquired=acquired,
                )
            except Exception as progress_error:
                report["progress_snapshot_error"] = (
                    f"{type(progress_error).__name__}: {progress_error}"
                )
            _partial_report(
                report,
                output,
                kind="ERROR",
                detail={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "mt5_session_poisoned": False,
                },
            )
        raise
    finally:
        _run_deadline = None

    ordered_evidence = _populate_progress_report(
        report,
        plan,
        evidence,
        checkpoints,
        resumed=resumed,
        acquired=acquired,
    )

    if len(evidence) != len(plan.chunks):
        report["retrieval_status"] = "PARTIAL"
        report["quality_status"] = "INDETERMINATE"
        _partial_report(
            report,
            output,
            kind="PLANNED_STOP",
            detail={
                "mt5_session_poisoned": False,
                "remaining_chunks": len(plan.chunks) - len(evidence),
            },
        )
        print(
            f"planned stop after {acquired} new chunk(s); resume with the same command",
            flush=True,
        )
        return

    summary = summarise_dataset(plan, ordered_evidence)
    structural_fields = (
        "timestamp_regressions",
        "time_field_mismatches",
        "bid_nonpositive",
        "ask_nonpositive",
        "crossed_quotes",
        "negative_volume",
        "negative_volume_real",
    )
    structural_failures = _all_fetch_metric_totals(checkpoints, structural_fields)
    # The per-fetch metrics see order only within one native response.  Add any
    # regression observed between adjacent primary chunks in the same symbol/window.
    primary_within_chunk_regressions = sum(
        item.metrics.timestamp_regressions for item in ordered_evidence
    )
    structural_failures["timestamp_regressions"] += (
        summary.timestamp_regressions - primary_within_chunk_regressions
    )
    structural_failures["rows_returned_before_requested_start"] = report["response_shapes"][
        "discarded_before_start"
    ]
    structural_failures["rows_returned_after_requested_end"] = report["response_shapes"][
        "discarded_after_end"
    ]
    repeat_mismatches = sum(
        not bool(comparison["identical"])
        for checkpoint in checkpoints.values()
        for comparison in cast(
            Sequence[Mapping[str, Any]], checkpoint.get("repeat_comparisons", [])
        )
    )
    report["status"] = "COMPLETE"
    report["retrieval_status"] = "COMPLETE"
    report["structural_status"] = "FAILED" if any(structural_failures.values()) else "PASSED"
    report["structural_failures"] = structural_failures
    report["repeat_fetch_status"] = "REVISION_OBSERVED" if repeat_mismatches else "IDENTICAL"
    report["repeat_fetch_mismatches"] = repeat_mismatches
    report["quality_status"] = "INDETERMINATE"
    report["cross_source_status"] = "NOT_EVALUABLE"
    report["completed_at_utc"] = datetime.now(UTC).isoformat()
    _write_json_atomic(output, report)
    digest = _sha256_path(output)
    _write_text_atomic(output.with_suffix(f"{output.suffix}.sha256"), f"{digest}  {output.name}\n")
    _partial_path(output).unlink(missing_ok=True)
    print(
        f"wrote {output} ({len(plan.chunks)} chunks, {summary.total_ticks} ticks, "
        f"dataset {summary.dataset_sha256})",
        flush=True,
    )


def main() -> None:
    args = _parse_args()
    work_root = _repository_path(Path(args.work_dir))
    _validate_work_dir(work_root, allow_external=bool(args.allow_external_work_dir))
    with _work_dir_lock(work_root):
        _run_main(args)


if __name__ == "__main__":
    main()
