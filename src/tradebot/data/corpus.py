"""Build an immutable, deterministic P1 corpus from completed FBS probe chunks.

Discovery snapshots only atomically published checkpoints, so this reader never
touches the probe's in-progress temporary files.  Raw conversion, quality cleaning,
external sorting and bar construction all stream in bounded batches.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from tradebot.core.clock import SimClock
from tradebot.core.time_rules import NEW_YORK, fx_session_bounds
from tradebot.core.timestamps import require_utc
from tradebot.core.types import Bar, Tick
from tradebot.data.acquisition_probe import (
    CANONICAL_TICK_HEADER,
    AcquisitionPlan,
    ChunkRequest,
    SourceTick,
    encode_source_tick,
    parse_plan,
)
from tradebot.data.bars import BarBoundary, BarBuilder, FixedInterval
from tradebot.data.quality import (
    CleanTickRecord,
    LiquidityCalendarLike,
    QualityInput,
    QualitySummary,
    QualityThresholds,
    TickQualityPipeline,
)
from tradebot.data.storage import (
    CLEAN_BAR_SCHEMA,
    CLEAN_TICK_SCHEMA,
    RAW_TICK_SCHEMA,
    FileDigest,
    ImmutableParquetWriter,
    clean_bar_path,
    clean_tick_path,
    dataset_id,
    external_sort_rows,
    file_manifest,
    raw_tick_path,
    sha256_path,
)

_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SEQ_STRIDE = 1 << 40
_CORPUS_DOMAIN = b"tradebot.fbs-clean-corpus.v1\n"
_CORPUS_IMPLEMENTATION_VERSION = 5


class ProbeArtifactError(RuntimeError):
    """Raised when a completed probe checkpoint or source artifact is inconsistent."""


class _CachedBoundary:
    """Cache one canonical interval while preserving UTC validation per call."""

    __slots__ = ("_boundary", "_interval")

    def __init__(self, boundary: BarBoundary) -> None:
        self._boundary = boundary
        self._interval: tuple[datetime, datetime] | None = None

    def __call__(self, ts: datetime) -> tuple[datetime, datetime] | None:
        moment = require_utc(ts, field="boundary instant")
        cached = self._interval
        if cached is not None and cached[0] <= moment < cached[1]:
            return cached
        resolved = self._boundary(moment)
        self._interval = resolved
        return resolved


def _load_plan(path: Path) -> AcquisitionPlan:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot parse acquisition plan {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("acquisition plan must be a JSON object")
    return parse_plan(cast(Mapping[str, object], payload))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProbeArtifact:
    """A checksum-validated, atomically completed source chunk."""

    request: ChunkRequest
    ordinal: int
    plan_hash: str
    source: str
    run_id: str
    completed_at: datetime
    raw_path: Path
    checkpoint_path: Path
    semantic_sha256: str
    compressed_sha256: str
    expected_rows: int
    artifact_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RawImportResult:
    artifact: ProbeArtifact
    files: tuple[Path, ...]
    rows: int


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanBuildResult:
    instrument: str
    files: tuple[Path, ...]
    summary: QualitySummary


@dataclass(frozen=True, slots=True, kw_only=True)
class BarBuildResult:
    instrument: str
    files: tuple[Path, ...]
    rows_by_timeframe: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanCorpusResult:
    """One deterministic clean rebuild from an already immutable raw snapshot."""

    corpus_id: str
    dataset_id: str
    source_rows: int
    clean_tick_files: tuple[Path, ...]
    clean_bar_files: tuple[Path, ...]
    clean_manifest: tuple[FileDigest, ...]
    quality: tuple[QualitySummary, ...]
    bar_rows_by_timeframe: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CorpusBuildResult:
    """Files and identities produced from one frozen completed-checkpoint snapshot."""

    corpus_id: str
    dataset_id: str
    plan_hash: str
    plan_complete: bool
    completed_chunks: int
    expected_chunks: int
    source_rows: int
    raw_files: tuple[Path, ...]
    clean_tick_files: tuple[Path, ...]
    clean_bar_files: tuple[Path, ...]
    clean_manifest: tuple[FileDigest, ...]
    quality: tuple[QualitySummary, ...]
    bar_rows_by_timeframe: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class _CanonicalRow:
    time: int
    time_msc: int
    bid_text: str
    ask_text: str
    last_text: str
    volume: int
    flags: int
    volume_real_text: str


def _json_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ProbeArtifactError(f"{context} must be an object with string keys")
    return cast(Mapping[str, object], value)


def _sha(value: object, context: str) -> str:
    if not isinstance(value, str) or not _HEX_SHA256.fullmatch(value):
        raise ProbeArtifactError(f"{context} must be a lowercase SHA-256")
    return value


def _parse_utc(value: object, context: str) -> datetime:
    if not isinstance(value, str):
        raise ProbeArtifactError(f"{context} must be an ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProbeArtifactError(f"{context} must be an ISO UTC timestamp") from exc
    return require_utc(parsed, field=context)


def _inside(path: Path, root: Path, context: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ProbeArtifactError(f"{context} escapes the probe work root") from exc
    return resolved


def _artifact_identity(*, plan_hash: str, request: ChunkRequest, semantic_sha256: str) -> str:
    payload = {
        "plan_hash": plan_hash,
        "chunk_id": request.chunk_id,
        "semantic_sha256": semantic_sha256,
    }
    return _json_sha256(payload)


def load_probe_artifact(
    checkpoint_path: Path,
    *,
    work_root: Path,
    plan: AcquisitionPlan,
    request: ChunkRequest,
    ordinal: int,
) -> ProbeArtifact:
    """Validate a completed checkpoint and its compressed source bytes."""
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeArtifactError(f"cannot parse checkpoint {checkpoint_path}") from exc
    root = _mapping(payload, "checkpoint")
    integrity = _mapping(root.get("integrity"), "checkpoint integrity")
    unsigned = {key: value for key, value in root.items() if key != "integrity"}
    if integrity.get("algorithm") != "sha256" or integrity.get("payload_sha256") != _json_sha256(
        unsigned
    ):
        raise ProbeArtifactError(f"checkpoint checksum mismatch: {checkpoint_path}")
    if root.get("plan_hash") != plan.plan_hash or root.get("source") != plan.source:
        raise ProbeArtifactError(f"checkpoint plan/source mismatch: {checkpoint_path}")

    chunk = _mapping(root.get("chunk"), "checkpoint chunk")
    expected = {
        "chunk_id": request.chunk_id,
        "logical_symbol": request.logical_symbol,
        "broker_symbol": request.broker_symbol,
        "window_id": request.window_id,
        "session_date": request.session_date.isoformat(),
        "start_utc": request.start.isoformat().replace("+00:00", "Z"),
        "end_utc": request.end.isoformat().replace("+00:00", "Z"),
    }
    if any(chunk.get(key) != value for key, value in expected.items()):
        raise ProbeArtifactError(f"checkpoint chunk identity mismatch: {checkpoint_path}")
    semantic = _sha(chunk.get("semantic_sha256"), "chunk semantic_sha256")
    metrics = _mapping(chunk.get("metrics"), "chunk metrics")
    expected_rows = metrics.get("tick_count")
    if type(expected_rows) is not int or expected_rows < 0:
        raise ProbeArtifactError("checkpoint tick_count must be a non-negative int")

    raw = _mapping(root.get("raw"), "checkpoint raw reference")
    if raw.get("format") != "tradebot-source-ticks-semantic-v1-tsv-gzip":
        raise ProbeArtifactError("checkpoint raw format is unsupported")
    raw_reference = raw.get("path")
    if not isinstance(raw_reference, str):
        raise ProbeArtifactError("checkpoint raw path must be str")
    raw_path = _inside(work_root / raw_reference, work_root, "checkpoint raw path")
    if not raw_path.is_file():
        raise ProbeArtifactError(f"checkpoint source artifact is missing: {raw_path}")
    compressed = _sha(raw.get("compressed_sha256"), "raw compressed_sha256")
    compressed_bytes = raw.get("compressed_bytes")
    if type(compressed_bytes) is not int or raw_path.stat().st_size != compressed_bytes:
        raise ProbeArtifactError(f"source artifact byte count mismatch: {raw_path}")
    if sha256_path(raw_path) != compressed:
        raise ProbeArtifactError(f"source artifact compressed checksum mismatch: {raw_path}")
    if raw.get("semantic_sha256") != semantic:
        raise ProbeArtifactError(f"source/checkpoint semantic hash mismatch: {raw_path}")

    comparisons = root.get("repeat_comparisons")
    if not isinstance(comparisons, list) or any(
        not isinstance(item, Mapping) or item.get("identical") is not True for item in comparisons
    ):
        raise ProbeArtifactError(
            f"repeat fetches are absent or non-identical for completed chunk {request.chunk_id}"
        )
    run_id = root.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ProbeArtifactError("checkpoint run_id must be non-empty")
    completed_at = _parse_utc(root.get("completed_at_utc"), "completed_at_utc")
    return ProbeArtifact(
        request=request,
        ordinal=ordinal,
        plan_hash=plan.plan_hash,
        source=plan.source,
        run_id=run_id,
        completed_at=completed_at,
        raw_path=raw_path,
        checkpoint_path=checkpoint_path.resolve(),
        semantic_sha256=semantic,
        compressed_sha256=compressed,
        expected_rows=expected_rows,
        artifact_id=_artifact_identity(
            plan_hash=plan.plan_hash,
            request=request,
            semantic_sha256=semantic,
        ),
    )


def discover_probe_artifacts(
    plan: AcquisitionPlan,
    *,
    work_root: Path,
) -> tuple[ProbeArtifact, ...]:
    """Snapshot all currently completed chunks in stable plan order."""
    artifacts: list[ProbeArtifact] = []
    ordinal_by_instrument: defaultdict[str, int] = defaultdict(int)
    for request in plan.chunks:
        ordinal = ordinal_by_instrument[request.logical_symbol]
        ordinal_by_instrument[request.logical_symbol] += 1
        raw_path = (
            work_root
            / plan.plan_hash
            / request.logical_symbol
            / request.window_id
            / f"{request.session_date.isoformat()}.source-ticks.tsv.gz"
        )
        checkpoint = raw_path.with_suffix(".checkpoint.json")
        if checkpoint.is_file():
            artifacts.append(
                load_probe_artifact(
                    checkpoint,
                    work_root=work_root,
                    plan=plan,
                    request=request,
                    ordinal=ordinal,
                )
            )
    return tuple(artifacts)


def _canonical_rows(artifact: ProbeArtifact) -> Iterator[_CanonicalRow]:
    digest = hashlib.sha256()
    with gzip.open(artifact.raw_path, "rb") as stream:
        header = stream.readline()
        if header != CANONICAL_TICK_HEADER:
            raise ProbeArtifactError(f"invalid canonical header: {artifact.raw_path}")
        digest.update(header)
        for line_number, line in enumerate(stream, start=2):
            digest.update(line)
            try:
                if not line.endswith(b"\n"):
                    raise ValueError("line ending")
                fields = line.removesuffix(b"\n").decode("ascii").split("\t")
                if len(fields) != 8:
                    raise ValueError("field count")
                source_tick = SourceTick(
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
                raise ProbeArtifactError(
                    f"invalid canonical source row {line_number}: {artifact.raw_path}"
                ) from exc
            if encode_source_tick(source_tick) != line:
                raise ProbeArtifactError(
                    f"non-canonical source row {line_number}: {artifact.raw_path}"
                )
            yield _CanonicalRow(
                time=source_tick.time,
                time_msc=source_tick.time_msc,
                bid_text=fields[2],
                ask_text=fields[3],
                last_text=fields[4],
                volume=source_tick.volume,
                flags=source_tick.flags,
                volume_real_text=fields[7],
            )
    if digest.hexdigest() != artifact.semantic_sha256:
        raise ProbeArtifactError(f"source artifact semantic checksum mismatch: {artifact.raw_path}")


def _from_milliseconds(value: int) -> datetime:
    seconds, milliseconds = divmod(value, 1000)
    return _EPOCH + timedelta(seconds=seconds, milliseconds=milliseconds)


def _stable_seq(request: ChunkRequest, source_row: int) -> int:
    """Derive a source-position sequence independent of acquisition-plan ordering."""
    session_namespace = (request.start.date() - _EPOCH.date()).days
    if session_namespace < 0:
        raise ProbeArtifactError("pre-1970 sessions require a new stable sequence namespace")
    return session_namespace * _SEQ_STRIDE + source_row


def import_raw_artifact(
    artifact: ProbeArtifact,
    *,
    data_root: Path,
    batch_size: int = 65_536,
) -> RawImportResult:
    """Stream one completed source artifact into immutable monthly raw partitions."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    writers: dict[tuple[int, int], ImmutableParquetWriter] = {}
    buffers: dict[tuple[int, int], list[Mapping[str, object]]] = defaultdict(list)
    paths: set[Path] = set()
    rows = 0
    with ExitStack() as stack:
        for source_row, item in enumerate(_canonical_rows(artifact)):
            if source_row >= _SEQ_STRIDE:
                raise ProbeArtifactError("one source chunk exceeds the stable sequence stride")
            ts_event = _from_milliseconds(item.time_msc)
            if not artifact.request.start <= ts_event < artifact.request.end:
                raise ProbeArtifactError(
                    f"source row lies outside checkpoint interval: {artifact.request.chunk_id}"
                )
            key = (ts_event.year, ts_event.month)
            if key not in writers:
                path = raw_tick_path(
                    data_root,
                    source=artifact.source,
                    instrument=artifact.request.logical_symbol,
                    month=date(ts_event.year, ts_event.month, 1),
                    artifact_id=artifact.artifact_id,
                )
                identity = {
                    "tradebot.kind": "raw-tick",
                    "tradebot.artifact_id": artifact.artifact_id,
                    "tradebot.chunk_id": artifact.request.chunk_id,
                    "tradebot.plan_hash": artifact.plan_hash,
                    "tradebot.source_semantic_sha256": artifact.semantic_sha256,
                    "tradebot.month": f"{ts_event.year:04d}-{ts_event.month:02d}",
                }
                writers[key] = stack.enter_context(
                    ImmutableParquetWriter(path, RAW_TICK_SCHEMA, identity=identity)
                )
                paths.add(path)
            buffers[key].append(
                {
                    "time": item.time,
                    "time_msc": item.time_msc,
                    "bid_text": item.bid_text,
                    "ask_text": item.ask_text,
                    "last_text": item.last_text,
                    "volume": item.volume,
                    "flags": item.flags,
                    "volume_real_text": item.volume_real_text,
                    "source": artifact.source,
                    "instrument": artifact.request.logical_symbol,
                    "broker_symbol": artifact.request.broker_symbol,
                    "seq": _stable_seq(artifact.request, source_row),
                    "source_row": source_row,
                    "run_id": artifact.run_id,
                    "ingested_at": artifact.completed_at,
                    "source_artifact_sha256": artifact.semantic_sha256,
                }
            )
            rows += 1
            if len(buffers[key]) == batch_size:
                writers[key].write_rows(buffers[key])
                buffers[key] = []
        if rows != artifact.expected_rows:
            raise ProbeArtifactError(
                f"source row count differs from checkpoint for {artifact.request.chunk_id}: "
                f"{rows} != {artifact.expected_rows}"
            )
        for key, pending in buffers.items():
            writers[key].write_rows(pending)
    return RawImportResult(artifact=artifact, files=tuple(sorted(paths)), rows=rows)


def _raw_quality_inputs(imports: Sequence[RawImportResult]) -> Iterator[QualityInput]:
    previous_seq: int | None = None
    for imported in imports:
        for path in imported.files:
            parquet = pq.ParquetFile(path)
            for batch in parquet.iter_batches(batch_size=65_536):
                columns = batch.to_pydict()
                for index in range(batch.num_rows):
                    seq = cast(int, columns["seq"][index])
                    if previous_seq is not None and seq <= previous_seq:
                        raise ProbeArtifactError("raw partitions are not in stable source sequence")
                    previous_seq = seq
                    fields = (
                        str(columns["time"][index]),
                        str(columns["time_msc"][index]),
                        cast(str, columns["bid_text"][index]),
                        cast(str, columns["ask_text"][index]),
                        cast(str, columns["last_text"][index]),
                        str(columns["volume"][index]),
                        str(columns["flags"][index]),
                        cast(str, columns["volume_real_text"][index]),
                    )
                    yield QualityInput(
                        instrument=cast(str, columns["instrument"][index]),
                        source=cast(str, columns["source"][index]),
                        seq=seq,
                        ts_event=_from_milliseconds(cast(int, columns["time_msc"][index])),
                        bid=Decimal(fields[2]),
                        ask=Decimal(fields[3]),
                        source_flags=cast(int, columns["flags"][index]),
                        raw_identity=fields,
                    )


def _threshold_payload(thresholds: QualityThresholds) -> Mapping[str, object]:
    return {
        "spread_multiplier": str(thresholds.spread_multiplier),
        "price_sigma": str(thresholds.price_sigma),
        "price_reversion_ticks": thresholds.price_reversion_ticks,
        "gap_seconds": str(thresholds.gap_threshold.total_seconds()),
        "fast_market_median_seconds": str(thresholds.fast_market_median.total_seconds()),
        "rolling_horizon_seconds": str(thresholds.rolling_horizon.total_seconds()),
        "minimum_history": thresholds.minimum_history,
    }


def corpus_identity(
    artifacts: Sequence[ProbeArtifact],
    *,
    thresholds: QualityThresholds,
    calendar_id: str | None,
    known_at: datetime | None,
    timeframes: Sequence[str],
    seal_latency: timedelta,
    calendar_instrument: str | None = None,
) -> str:
    """Identify source bytes plus every input that can alter clean output."""

    instruments = sorted({item.request.logical_symbol for item in artifacts})
    sources = {item.source for item in artifacts}
    if len(sources) != 1:
        raise ValueError("one corpus identity must use exactly one source")
    if calendar_instrument is not None:
        if not calendar_instrument.strip():
            raise ValueError("calendar_instrument must be non-empty")
        if len(instruments) != 1:
            raise ValueError(
                "an explicit calendar_instrument requires exactly one source instrument"
            )
    source = next(iter(sources))
    calendar_instruments = {
        instrument: calendar_instrument or f"{source}/{instrument}" for instrument in instruments
    }
    payload = {
        "artifacts": [
            {
                "chunk_id": item.request.chunk_id,
                "artifact_id": item.artifact_id,
                "ordinal": item.ordinal,
            }
            for item in artifacts
        ],
        "thresholds": _threshold_payload(thresholds),
        "calendar_id": calendar_id or "MISSING",
        "calendar_known_at": known_at.isoformat() if known_at is not None else None,
        "calendar_instruments": calendar_instruments,
        "schemas": ["raw-tick-v1", "clean-tick-v2", "clean-bar-v2"],
        "timeframes": sorted(timeframes),
        "seal_latency": [
            seal_latency.days,
            seal_latency.seconds,
            seal_latency.microseconds,
        ],
        "implementation_version": _CORPUS_IMPLEMENTATION_VERSION,
    }
    digest = hashlib.sha256(_CORPUS_DOMAIN)
    digest.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()


def build_clean_ticks(
    imports: Sequence[RawImportResult],
    *,
    data_root: Path,
    venue: str,
    corpus_id: str,
    thresholds: QualityThresholds,
    calendar: LiquidityCalendarLike | None,
    known_at: datetime | None,
    calendar_instrument: str | None = None,
    session_boundary: BarBoundary = fx_session_bounds,
    sort_run_rows: int = 65_536,
    output_batch_rows: int = 65_536,
) -> CleanBuildResult:
    """Derive stable, availability-sorted clean ticks from immutable raw Parquet."""
    if not imports:
        raise ValueError("at least one raw import is required")
    instrument = imports[0].artifact.request.logical_symbol
    source = imports[0].artifact.source
    if any(
        item.artifact.request.logical_symbol != instrument or item.artifact.source != source
        for item in imports
    ):
        raise ValueError("clean build accepts exactly one source/instrument stream")
    pipeline = TickQualityPipeline(
        instrument=instrument,
        source=source,
        session_boundary=session_boundary,
        thresholds=thresholds,
        calendar=calendar,
        calendar_instrument=calendar_instrument or f"{source}/{instrument}",
        known_at=known_at,
    )
    for imported in imports:
        pipeline.require_calendar_day(imported.artifact.request.end.astimezone(NEW_YORK).date())

    def quality_rows() -> Iterator[Mapping[str, object]]:
        for item in _raw_quality_inputs(imports):
            for clean in pipeline.process(item):
                yield clean.as_mapping()
        for clean in pipeline.finish():
            yield clean.as_mapping()

    writers: dict[tuple[int, int], ImmutableParquetWriter] = {}
    paths: set[Path] = set()
    with ExitStack() as stack:
        batches = external_sort_rows(
            quality_rows(),
            schema=CLEAN_TICK_SCHEMA,
            keys=("available_at", "source", "seq"),
            temporary_root=data_root / ".scratch",
            run_rows=sort_run_rows,
            output_rows=output_batch_rows,
        )
        for batch in batches:
            grouped: defaultdict[tuple[int, int], list[Mapping[str, object]]] = defaultdict(list)
            for row in batch.to_pylist():
                moment = cast(datetime, row["ts_event"])
                grouped[(moment.year, moment.month)].append(row)
            for key, rows in grouped.items():
                if key not in writers:
                    path = clean_tick_path(
                        data_root,
                        venue=venue,
                        instrument=instrument,
                        month=date(key[0], key[1], 1),
                        corpus_id=corpus_id,
                    )
                    writers[key] = stack.enter_context(
                        ImmutableParquetWriter(
                            path,
                            CLEAN_TICK_SCHEMA,
                            identity={
                                "tradebot.kind": "clean-tick",
                                "tradebot.corpus_id": corpus_id,
                                "tradebot.source": source,
                                "tradebot.instrument": instrument,
                                "tradebot.venue": venue,
                                "tradebot.month": f"{key[0]:04d}-{key[1]:02d}",
                            },
                        )
                    )
                    paths.add(path)
                writers[key].write_rows(rows)
    return CleanBuildResult(
        instrument=instrument,
        files=tuple(sorted(paths)),
        summary=pipeline.summary(),
    )


def fx_bar_boundaries(timeframes: Iterable[str]) -> Mapping[str, BarBoundary]:
    """Return the frozen baseline FX boundaries requested by name."""
    supported: Mapping[str, BarBoundary] = {
        "1m": FixedInterval(timedelta(minutes=1)),
        "5m": FixedInterval(timedelta(minutes=5)),
        "15m": FixedInterval(timedelta(minutes=15)),
        "1h": FixedInterval(timedelta(hours=1)),
        "4h": FixedInterval(timedelta(hours=4)),
        "1d": fx_session_bounds,
    }
    requested = tuple(timeframes)
    unknown = sorted(set(requested) - set(supported))
    if unknown:
        raise ValueError(f"unsupported FX timeframes: {unknown}")
    if len(set(requested)) != len(requested):
        raise ValueError("timeframes must be unique")
    return {name: supported[name] for name in requested}


@dataclass(slots=True)
class _QuoteStats:
    ts_open: datetime
    ts_close: datetime
    spread_max: Decimal
    bid_close: Decimal
    ask_close: Decimal
    last_seq: int


@dataclass(slots=True)
class _IntervalEvidence:
    ts_open: datetime
    ts_close: datetime
    flags: set[str]


def _clean_tick_records(paths: Sequence[Path]) -> Iterator[CleanTickRecord]:
    previous: tuple[datetime, str, int] | None = None
    for path in paths:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=65_536):
            for row in batch.to_pylist():
                record = CleanTickRecord(
                    instrument=cast(str, row["instrument"]),
                    ts_event=cast(datetime, row["ts_event"]),
                    ts_recv=cast(datetime, row["ts_recv"]),
                    available_at=cast(datetime, row["available_at"]),
                    bid=cast(Decimal, row["bid"]),
                    ask=cast(Decimal, row["ask"]),
                    bid_size=cast(int | None, row["bid_size"]),
                    ask_size=cast(int | None, row["ask_size"]),
                    source=cast(str, row["source"]),
                    seq=cast(int, row["seq"]),
                    source_flags=cast(int, row["source_flags"]),
                    quality_flags=tuple(cast(list[str], row["quality_flags"])),
                    retrospective_flags=tuple(cast(list[str], row["retrospective_flags"])),
                    eligible_for_bars=cast(bool, row["eligible_for_bars"]),
                )
                key = (record.available_at, record.source, record.seq)
                if previous is not None and key <= previous:
                    raise ValueError("clean tick files are not in strict replay order")
                previous = key
                yield record


def _bar_mapping(
    bar: Bar,
    stats: _QuoteStats,
    *,
    source: str,
    interval_flags: set[str],
) -> Mapping[str, object]:
    if (bar.ts_open, bar.ts_close) != (stats.ts_open, stats.ts_close):
        raise RuntimeError("bar/quote-stat intervals diverged")
    return {
        "instrument": bar.instrument,
        "ts_open": bar.ts_open,
        "ts_close": bar.ts_close,
        "ts_recv": bar.ts_recv,
        "available_at": bar.available_at,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "volume_kind": bar.volume_kind.value if bar.volume_kind is not None else None,
        "n_ticks": bar.n_ticks,
        "spread_mean": bar.spread_mean,
        "spread_max": stats.spread_max,
        "bid_close": stats.bid_close,
        "ask_close": stats.ask_close,
        "source": source,
        "seq": stats.last_seq,
        "quality_flags": sorted(set(bar.quality_flags) | interval_flags),
    }


def build_bars_from_clean_ticks(
    clean_files: Sequence[Path],
    *,
    data_root: Path,
    venue: str,
    instrument: str,
    source: str,
    corpus_id: str,
    boundaries: Mapping[str, BarBoundary],
    until: datetime,
    seal_latency: timedelta = timedelta(0),
    output_batch_rows: int = 65_536,
) -> BarBuildResult:
    """Drive the existing BarBuilder with timer-faithful historical clock advances."""
    if not boundaries:
        return BarBuildResult(instrument=instrument, files=(), rows_by_timeframe=())
    if output_batch_rows < 1:
        raise ValueError("output_batch_rows must be positive")
    final_instant = require_utc(until, field="until")
    records = _clean_tick_records(clean_files)
    first = next(records, None)
    if first is None:
        return BarBuildResult(
            instrument=instrument,
            files=(),
            rows_by_timeframe=tuple((name, 0) for name in sorted(boundaries)),
        )
    if first.instrument != instrument or first.source != source:
        raise ValueError("clean tick stream identity differs from bar request")
    clock = SimClock(first.available_at)
    builders = {
        name: BarBuilder(
            instrument=instrument,
            boundary=boundary,
            clock=clock,
            seal_latency=seal_latency,
        )
        for name, boundary in boundaries.items()
    }
    stats: dict[str, _QuoteStats | None] = {name: None for name in boundaries}
    evidence: dict[str, _IntervalEvidence | None] = {name: None for name in boundaries}
    writers: dict[tuple[str, int, int], ImmutableParquetWriter] = {}
    buffers: defaultdict[tuple[str, int, int], list[Mapping[str, object]]] = defaultdict(list)
    paths: set[Path] = set()
    counts: Counter[str] = Counter()

    def write_bar(name: str, bar: Bar) -> None:
        quote_stats = stats[name]
        if quote_stats is None:
            raise RuntimeError("bar emitted without constituent quote stats")
        interval_evidence = evidence[name]
        if interval_evidence is None or (
            interval_evidence.ts_open,
            interval_evidence.ts_close,
        ) != (bar.ts_open, bar.ts_close):
            raise RuntimeError("bar emitted without matching interval quality evidence")
        key = (name, bar.ts_close.year, bar.ts_close.month)
        buffers[key].append(
            _bar_mapping(
                bar,
                quote_stats,
                source=source,
                interval_flags=interval_evidence.flags,
            )
        )
        counts[name] += 1
        stats[name] = None
        evidence[name] = None
        if len(buffers[key]) == output_batch_rows:
            writers[key].write_rows(buffers[key])
            buffers[key] = []

    def flush_due(target: datetime) -> None:
        while True:
            due = [
                close
                for builder in builders.values()
                if (close := builder.forming_close) is not None and close <= target
            ]
            if not due:
                return
            instant = min(due)
            clock.advance_to(instant)
            for name in sorted(builders):
                if builders[name].forming_close == instant:
                    emitted = builders[name].flush(instant)
                    if len(emitted) != 1:
                        raise RuntimeError("a due forming bar did not seal exactly once")
                    write_bar(name, emitted[0])

    def process(record: CleanTickRecord) -> None:
        if record.instrument != instrument or record.source != source:
            raise ValueError("clean tick stream contains another source/instrument")
        flush_due(record.available_at)
        clock.advance_to(record.available_at)
        tick = (
            Tick(
                instrument=record.instrument,
                ts_event=record.ts_event,
                ts_recv=record.ts_recv,
                available_at=record.available_at,
                bid=record.bid,
                ask=record.ask,
                bid_size=record.bid_size,
                ask_size=record.ask_size,
                quality_flags=record.quality_flags,
            )
            if record.eligible_for_bars
            else None
        )
        for name in sorted(builders):
            boundary = boundaries[name]
            interval = boundary(record.ts_event)
            if interval is None:
                if record.eligible_for_bars:
                    raise RuntimeError("bar-eligible tick lies outside the requested boundary")
                continue
            start, end = interval
            current_evidence = evidence[name]
            if (
                current_evidence is None
                or (
                    current_evidence.ts_open,
                    current_evidence.ts_close,
                )
                != interval
            ):
                if current_evidence is not None and current_evidence.ts_open > start:
                    raise RuntimeError("quality evidence regressed across bar intervals")
                evidence[name] = _IntervalEvidence(start, end, set(record.quality_flags))
            else:
                current_evidence.flags.update(record.quality_flags)

            if tick is None:
                continue
            current = stats[name]
            spread = tick.ask - tick.bid
            if current is None:
                stats[name] = _QuoteStats(start, end, spread, tick.bid, tick.ask, record.seq)
            elif (current.ts_open, current.ts_close) == interval:
                current.spread_max = max(current.spread_max, spread)
                current.bid_close = tick.bid
                current.ask_close = tick.ask
                current.last_seq = record.seq
            else:
                raise RuntimeError("quote stats crossed an interval without a timer flush")
            emitted = builders[name].add(tick)
            if emitted:
                raise RuntimeError("timer-faithful runner left a bar for tick-triggered sealing")

    with ExitStack() as stack:
        # Writers are opened lazily by timeframe/month immediately before their
        # first buffer can flush.  Keep all contexts open so a later failure
        # publishes none of the new corpus files.
        def ensure_writers() -> None:
            for name, year, month in tuple(buffers):
                key = (name, year, month)
                if key in writers:
                    continue
                path = clean_bar_path(
                    data_root,
                    venue=venue,
                    timeframe=name,
                    instrument=instrument,
                    month=date(year, month, 1),
                    corpus_id=corpus_id,
                )
                writers[key] = stack.enter_context(
                    ImmutableParquetWriter(
                        path,
                        CLEAN_BAR_SCHEMA,
                        identity={
                            "tradebot.kind": "clean-bar",
                            "tradebot.corpus_id": corpus_id,
                            "tradebot.source": source,
                            "tradebot.instrument": instrument,
                            "tradebot.venue": venue,
                            "tradebot.timeframe": name,
                            "tradebot.month": f"{year:04d}-{month:02d}",
                        },
                    )
                )
                paths.add(path)

        # Open writers before a full buffer can call write_bar's fast path.
        original_write_bar = write_bar

        def write_bar_with_writer(name: str, bar: Bar) -> None:
            quote_stats = stats[name]
            if quote_stats is None:
                raise RuntimeError("bar emitted without constituent quote stats")
            key = (name, bar.ts_close.year, bar.ts_close.month)
            if key not in writers:
                buffers.setdefault(key, [])
                ensure_writers()
            original_write_bar(name, bar)

        write_bar = write_bar_with_writer
        process(first)
        for record in records:
            process(record)
        if final_instant < clock.now():
            raise ValueError("until cannot be earlier than the last clean tick")
        flush_due(final_instant)
        clock.advance_to(final_instant)
        ensure_writers()
        for key, pending in buffers.items():
            writers[key].write_rows(pending)

    return BarBuildResult(
        instrument=instrument,
        files=tuple(sorted(paths)),
        rows_by_timeframe=tuple(sorted(counts.items())),
    )


def build_fbs_corpus(
    *,
    plan_path: Path,
    probe_work_root: Path,
    data_root: Path,
    venue: str,
    timeframes: Sequence[str] = ("1m", "1d"),
    selected_chunk_ids: frozenset[str] | None = None,
    thresholds: QualityThresholds | None = None,
    calendar: LiquidityCalendarLike | None = None,
    calendar_id: str | None = None,
    known_at: datetime | None = None,
    calendar_instrument: str | None = None,
    seal_latency: timedelta = timedelta(0),
    batch_size: int = 65_536,
) -> CorpusBuildResult:
    """Run the complete immutable raw -> clean ticks -> clean bars P1 slice.

    ``selected_chunk_ids`` is the interface used by the Gate-1 random 30-day
    rebuild runner. Sequence numbers derive from immutable source positions and
    never depend on a chunk's position in an acquisition plan.
    """
    plan = _load_plan(plan_path)
    discovered = discover_probe_artifacts(plan, work_root=probe_work_root)
    available_ids = {item.request.chunk_id for item in discovered}
    if selected_chunk_ids is not None:
        missing = sorted(selected_chunk_ids - available_ids)
        if missing:
            raise ValueError(f"selected chunks are not completed: {missing}")
        artifacts = tuple(
            item for item in discovered if item.request.chunk_id in selected_chunk_ids
        )
    else:
        artifacts = discovered
    if not artifacts:
        raise ValueError("the completed-checkpoint snapshot contains no selected chunks")
    raw_imports = tuple(
        import_raw_artifact(item, data_root=data_root, batch_size=batch_size) for item in artifacts
    )
    clean = rebuild_from_raw(
        raw_imports,
        data_root=data_root,
        venue=venue,
        timeframes=timeframes,
        thresholds=thresholds,
        calendar=calendar,
        calendar_id=calendar_id,
        known_at=known_at,
        calendar_instrument=calendar_instrument,
        seal_latency=seal_latency,
        batch_size=batch_size,
    )
    raw_files = tuple(sorted(path for item in raw_imports for path in item.files))
    return CorpusBuildResult(
        corpus_id=clean.corpus_id,
        dataset_id=clean.dataset_id,
        plan_hash=plan.plan_hash,
        plan_complete=len(discovered) == len(plan.chunks),
        completed_chunks=len(discovered),
        expected_chunks=len(plan.chunks),
        source_rows=clean.source_rows,
        raw_files=raw_files,
        clean_tick_files=clean.clean_tick_files,
        clean_bar_files=clean.clean_bar_files,
        clean_manifest=clean.clean_manifest,
        quality=clean.quality,
        bar_rows_by_timeframe=clean.bar_rows_by_timeframe,
    )


def rebuild_from_raw(
    raw_imports: Sequence[RawImportResult],
    *,
    data_root: Path,
    venue: str,
    timeframes: Sequence[str] = ("1m", "1d"),
    thresholds: QualityThresholds | None = None,
    calendar: LiquidityCalendarLike | None = None,
    calendar_id: str | None = None,
    known_at: datetime | None = None,
    calendar_instrument: str | None = None,
    seal_latency: timedelta = timedelta(0),
    batch_size: int = 65_536,
) -> CleanCorpusResult:
    """Rebuild clean ticks and bars from the same immutable raw imports.

    Gate-1 callers import completed source artifacts once, then pass the exact same
    ``RawImportResult`` snapshot to this function with two distinct ``data_root``
    directories. The returned manifests and dataset IDs provide the byte-for-byte
    comparison surface without rereading mutable acquisition state.
    """
    if not raw_imports:
        raise ValueError("at least one immutable raw import is required")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if seal_latency < timedelta(0):
        raise ValueError("seal_latency cannot be negative")
    effective_thresholds = thresholds or QualityThresholds()
    if calendar is not None and (calendar_id is None or known_at is None):
        raise ValueError("calendar_id and known_at are required with a liquidity calendar")
    if calendar is None and calendar_id is not None:
        raise ValueError("calendar_id requires a liquidity calendar")
    checked_known_at = require_utc(known_at, field="known_at") if known_at is not None else None
    base_boundaries = fx_bar_boundaries(timeframes)
    ordered_imports = tuple(
        sorted(
            raw_imports,
            key=lambda item: (
                item.artifact.source,
                item.artifact.request.logical_symbol,
                item.artifact.request.start,
                item.artifact.request.end,
                item.artifact.request.chunk_id,
            ),
        )
    )
    sources = {item.artifact.source for item in ordered_imports}
    if len(sources) != 1:
        raise ValueError("one clean corpus snapshot must use exactly one source")
    identity = corpus_identity(
        tuple(item.artifact for item in ordered_imports),
        thresholds=effective_thresholds,
        calendar_id=calendar_id,
        known_at=checked_known_at,
        timeframes=timeframes,
        seal_latency=seal_latency,
        calendar_instrument=calendar_instrument,
    )
    by_instrument: defaultdict[str, list[RawImportResult]] = defaultdict(list)
    for item in ordered_imports:
        by_instrument[item.artifact.request.logical_symbol].append(item)

    clean_results: list[CleanBuildResult] = []
    bar_results: list[BarBuildResult] = []
    for instrument in sorted(by_instrument):
        imports = tuple(by_instrument[instrument])
        session_boundary = _CachedBoundary(fx_session_bounds)
        boundaries = dict(base_boundaries)
        if "1d" in boundaries:
            boundaries["1d"] = session_boundary
        clean = build_clean_ticks(
            imports,
            data_root=data_root,
            venue=venue,
            corpus_id=identity,
            thresholds=effective_thresholds,
            calendar=calendar,
            known_at=checked_known_at,
            calendar_instrument=(
                calendar_instrument or f"{imports[0].artifact.source}/{instrument}"
            ),
            session_boundary=session_boundary,
            sort_run_rows=batch_size,
            output_batch_rows=batch_size,
        )
        clean_results.append(clean)
        bar_results.append(
            build_bars_from_clean_ticks(
                clean.files,
                data_root=data_root,
                venue=venue,
                instrument=instrument,
                source=imports[0].artifact.source,
                corpus_id=identity,
                boundaries=boundaries,
                until=max(item.artifact.request.end for item in imports),
                seal_latency=seal_latency,
                output_batch_rows=batch_size,
            )
        )

    tick_files = tuple(sorted(path for item in clean_results for path in item.files))
    bar_files = tuple(sorted(path for item in bar_results for path in item.files))
    clean_manifest = file_manifest((*tick_files, *bar_files), relative_to=data_root)
    totals: Counter[str] = Counter()
    for result in bar_results:
        totals.update(dict(result.rows_by_timeframe))
    return CleanCorpusResult(
        corpus_id=identity,
        dataset_id=dataset_id(clean_manifest),
        source_rows=sum(item.rows for item in ordered_imports),
        clean_tick_files=tick_files,
        clean_bar_files=bar_files,
        clean_manifest=clean_manifest,
        quality=tuple(item.summary for item in clean_results),
        bar_rows_by_timeframe=tuple(sorted(totals.items())),
    )
