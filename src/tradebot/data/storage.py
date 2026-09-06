"""Deterministic, immutable Parquet primitives for the Phase-1 data corpus.

The raw acquisition artifacts are already immutable gzip streams.  This module
provides the second half of that boundary: copy-once Parquet publication, bounded
external sorting, canonical file manifests and the venue-partitioned paths required
by SPEC 4.2.  It deliberately owns no source-specific parsing or quality policy.
"""

from __future__ import annotations

import hashlib
import heapq
import os
import re
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_DATASET_DOMAIN = b"tradebot.clean-dataset.v1\n"


class ImmutableDatasetError(RuntimeError):
    """Raised when publication would replace an existing immutable artifact."""


def safe_segment(value: str, *, field: str) -> str:
    """Return a path-safe identity segment, rejecting ambiguous or nested paths."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be str")
    if not _SEGMENT.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{field} must be a non-empty path-safe identifier segment")
    return value


def _part_name(identity: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ValueError("artifact identity must be a lowercase SHA-256 digest")
    return f"part-{identity}.parquet"


def raw_tick_path(
    root: Path,
    *,
    source: str,
    instrument: str,
    month: date,
    artifact_id: str,
) -> Path:
    """Return the SPEC 4.2 raw partition path for one immutable source artifact."""
    return (
        root
        / "raw"
        / safe_segment(source, field="source")
        / safe_segment(instrument, field="instrument")
        / f"{month.year:04d}"
        / f"{month.month:02d}"
        / _part_name(artifact_id)
    )


def clean_tick_path(
    root: Path,
    *,
    venue: str,
    instrument: str,
    month: date,
    corpus_id: str,
) -> Path:
    """Return a clean-tick path whose venue is part of the series key."""
    return (
        root
        / "clean"
        / "ticks"
        / safe_segment(venue, field="venue")
        / safe_segment(instrument, field="instrument")
        / f"{month.year:04d}"
        / f"{month.month:02d}"
        / _part_name(corpus_id)
    )


def clean_bar_path(
    root: Path,
    *,
    venue: str,
    timeframe: str,
    instrument: str,
    month: date,
    corpus_id: str,
) -> Path:
    """Return a clean-bar path keyed by venue, timeframe and instrument."""
    return (
        root
        / "clean"
        / "bars"
        / safe_segment(venue, field="venue")
        / safe_segment(timeframe, field="timeframe")
        / safe_segment(instrument, field="instrument")
        / f"{month.year:04d}"
        / f"{month.month:02d}"
        / _part_name(corpus_id)
    )


RAW_TICK_SCHEMA = pa.schema(
    [
        pa.field("time", pa.int64(), nullable=False),
        pa.field("time_msc", pa.int64(), nullable=False),
        # Canonical source decimal text is retained byte-for-byte.  The clean layer
        # parses these into exact Decimal128 values.
        pa.field("bid_text", pa.string(), nullable=False),
        pa.field("ask_text", pa.string(), nullable=False),
        pa.field("last_text", pa.string(), nullable=False),
        pa.field("volume", pa.int64(), nullable=False),
        pa.field("flags", pa.int64(), nullable=False),
        pa.field("volume_real_text", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("instrument", pa.string(), nullable=False),
        pa.field("broker_symbol", pa.string(), nullable=False),
        pa.field("seq", pa.int64(), nullable=False),
        pa.field("source_row", pa.int64(), nullable=False),
        pa.field("run_id", pa.string(), nullable=False),
        pa.field("ingested_at", pa.timestamp("ns", tz="UTC"), nullable=False),
        pa.field("source_artifact_sha256", pa.string(), nullable=False),
    ],
    metadata={b"tradebot.schema": b"raw-tick-v1"},
)

_PRICE = pa.decimal128(38, 18)
# Aggregated means are Decimal divisions under the frozen Python context and can
# have a non-terminating expansion (for example, three constituent spreads whose
# sum is one). Decimal256 stores every context-produced digit without rounding.
_AGGREGATED_PRICE = pa.decimal256(76, 38)

CLEAN_TICK_SCHEMA = pa.schema(
    [
        pa.field("instrument", pa.string(), nullable=False),
        pa.field("ts_event", pa.timestamp("ns", tz="UTC"), nullable=False),
        pa.field("ts_recv", pa.timestamp("ns", tz="UTC"), nullable=False),
        pa.field("available_at", pa.timestamp("ns", tz="UTC"), nullable=False),
        pa.field("bid", _PRICE, nullable=False),
        pa.field("ask", _PRICE, nullable=False),
        pa.field("bid_size", pa.int64()),
        pa.field("ask_size", pa.int64()),
        pa.field("source", pa.string(), nullable=False),
        pa.field("seq", pa.int64(), nullable=False),
        pa.field("source_flags", pa.int64(), nullable=False),
        pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        # These annotations require future observations and therefore must never
        # enter a causal event or bar flag at the earlier market timestamp.
        pa.field("retrospective_flags", pa.list_(pa.string()), nullable=False),
        pa.field("eligible_for_bars", pa.bool_(), nullable=False),
    ],
    metadata={b"tradebot.schema": b"clean-tick-v2"},
)

CLEAN_BAR_SCHEMA = pa.schema(
    [
        pa.field("instrument", pa.string(), nullable=False),
        pa.field("ts_open", pa.timestamp("ns", tz="UTC"), nullable=False),
        pa.field("ts_close", pa.timestamp("ns", tz="UTC"), nullable=False),
        pa.field("ts_recv", pa.timestamp("ns", tz="UTC"), nullable=False),
        pa.field("available_at", pa.timestamp("ns", tz="UTC"), nullable=False),
        pa.field("open", _PRICE, nullable=False),
        pa.field("high", _PRICE, nullable=False),
        pa.field("low", _PRICE, nullable=False),
        pa.field("close", _PRICE, nullable=False),
        pa.field("volume", pa.int64()),
        pa.field("volume_kind", pa.string()),
        pa.field("n_ticks", pa.int64()),
        pa.field("spread_mean", _AGGREGATED_PRICE),
        pa.field("spread_max", _PRICE),
        pa.field("bid_close", _PRICE),
        pa.field("ask_close", _PRICE),
        pa.field("source", pa.string(), nullable=False),
        pa.field("seq", pa.int64(), nullable=False),
        pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
    ],
    metadata={b"tradebot.schema": b"clean-bar-v2"},
)


def sha256_path(path: Path, *, block_size: int = 1024 * 1024) -> str:
    """Hash a file without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _metadata_bytes(metadata: Mapping[str, str]) -> dict[bytes, bytes]:
    encoded: dict[bytes, bytes] = {}
    for key, value in sorted(metadata.items()):
        if not key.startswith("tradebot."):
            raise ValueError("Parquet identity metadata keys must start with 'tradebot.'")
        encoded[key.encode("ascii")] = value.encode("utf-8")
    return encoded


def parquet_metadata(path: Path) -> Mapping[str, str]:
    """Return UTF-8 key/value metadata from a Parquet file."""
    raw = pq.ParquetFile(path).schema_arrow.metadata or {}
    return {key.decode("utf-8"): value.decode("utf-8") for key, value in raw.items()}


class ImmutableParquetWriter:
    """Write one deterministic Parquet file and publish it without replacement.

    If a file already exists with the same identity metadata, construction succeeds
    as an idempotent no-op.  A different identity fails before any input is consumed.
    """

    def __init__(
        self,
        path: Path,
        schema: pa.Schema,
        *,
        identity: Mapping[str, str],
    ) -> None:
        self.path = path
        self._identity = dict(identity)
        metadata = dict(schema.metadata or {})
        metadata.update(_metadata_bytes(identity))
        self.schema = schema.with_metadata(metadata)
        self.created = False
        self.rows_written = 0
        self._writer: pq.ParquetWriter | None = None
        self._temporary: Path | None = None
        self._existing = path.exists()
        if self._existing:
            self._validate_existing()

    def _validate_existing(self) -> None:
        try:
            parquet = pq.ParquetFile(self.path)
        except (OSError, pa.ArrowException) as exc:
            raise ImmutableDatasetError(
                f"existing Parquet artifact is unreadable: {self.path}"
            ) from exc
        actual_schema = parquet.schema_arrow.remove_metadata()
        if not actual_schema.equals(self.schema.remove_metadata()):
            raise ImmutableDatasetError(f"existing Parquet schema differs: {self.path}")
        actual = parquet_metadata(self.path)
        mismatched = {
            key: (actual.get(key), value)
            for key, value in self._identity.items()
            if actual.get(key) != value
        }
        if mismatched:
            raise ImmutableDatasetError(
                f"immutable Parquet identity differs for {self.path}: {mismatched}"
            )
        self.rows_written = parquet.metadata.num_rows

    def __enter__(self) -> ImmutableParquetWriter:
        if self._existing:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        os.close(descriptor)
        self._temporary = Path(name)
        self._writer = pq.ParquetWriter(
            self._temporary,
            self.schema,
            compression="zstd",
            compression_level=9,
            use_dictionary=False,
            write_statistics=True,
            data_page_version="1.0",
            version="2.6",
        )
        return self

    def write_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """Append a bounded row batch in the caller-provided deterministic order."""
        if not rows:
            return
        if self._existing:
            return
        if self._writer is None:
            raise RuntimeError("ImmutableParquetWriter must be entered before writing")
        table = pa.Table.from_pylist(list(rows), schema=self.schema)
        self._writer.write_table(table, row_group_size=len(rows))
        self.rows_written += len(rows)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, traceback
        if self._existing:
            return
        temporary = self._temporary
        writer = self._writer
        self._temporary = None
        self._writer = None
        if writer is not None:
            writer.close()
        if temporary is None:
            return
        try:
            if exc is not None:
                return
            # The file is complete before its name becomes visible.  Hard-link
            # publication is create-if-absent, unlike os.replace which can overwrite.
            # Windows rejects fsync on a read-only descriptor (EBADF). Open the
            # completed temp file read/write without mutating it so durability has
            # identical semantics on Windows and POSIX before atomic publication.
            with temporary.open("rb+") as stream:
                os.fsync(stream.fileno())
            try:
                os.link(temporary, self.path)
                self.created = True
            except FileExistsError:
                self._existing = True
                self._validate_existing()
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class FileDigest:
    """A stable relative path and SHA-256 pair used in dataset manifests."""

    path: str
    sha256: str


def file_manifest(paths: Iterable[Path], *, relative_to: Path) -> tuple[FileDigest, ...]:
    """Hash files in normalized relative-path order."""
    base = relative_to.resolve()
    rows: list[FileDigest] = []
    for path in paths:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(base).as_posix()
        except ValueError as exc:
            raise ValueError(f"artifact is outside manifest root: {path}") from exc
        rows.append(FileDigest(relative, sha256_path(resolved)))
    return tuple(sorted(rows, key=lambda item: item.path))


def dataset_id(manifest: Sequence[FileDigest]) -> str:
    """Hash a canonical ordered list of clean file hashes (SPEC 4.2, NN-10)."""
    if tuple(sorted(manifest, key=lambda item: item.path)) != tuple(manifest):
        raise ValueError("dataset manifest must be in relative-path order")
    if len({item.path for item in manifest}) != len(manifest):
        raise ValueError("dataset manifest paths must be unique")
    digest = hashlib.sha256(_DATASET_DOMAIN)
    for item in manifest:
        if not re.fullmatch(r"[0-9a-f]{64}", item.sha256):
            raise ValueError(f"invalid SHA-256 for {item.path}")
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\t")
        digest.update(item.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _write_sort_run(
    path: Path, schema: pa.Schema, rows: list[Mapping[str, Any]], keys: Sequence[str]
) -> None:
    table = pa.Table.from_pylist(rows, schema=schema)
    table = table.sort_by([(key, "ascending") for key in keys])
    pq.write_table(
        table,
        path,
        compression="zstd",
        compression_level=1,
        use_dictionary=False,
        write_statistics=False,
        row_group_size=len(rows),
        version="2.6",
    )


class _RunCursor:
    def __init__(self, path: Path, *, batch_size: int) -> None:
        self._parquet = pq.ParquetFile(path)
        self._batches = iter(self._parquet.iter_batches(batch_size=batch_size))
        self._batch: pa.RecordBatch | None = None
        self._index = 0
        self._advance_batch()

    @property
    def exhausted(self) -> bool:
        return self._batch is None

    def _advance_batch(self) -> None:
        try:
            self._batch = next(self._batches)
            self._index = 0
        except StopIteration:
            self._batch = None

    def key(self, columns: Sequence[str]) -> tuple[object, ...]:
        if self._batch is None:
            raise RuntimeError("sort cursor is exhausted")
        return tuple(
            self._batch.column(self._batch.schema.get_field_index(name))[self._index].as_py()
            for name in columns
        )

    def pop(self) -> dict[str, object]:
        if self._batch is None:
            raise RuntimeError("sort cursor is exhausted")
        row = {
            field.name: self._batch.column(index)[self._index].as_py()
            for index, field in enumerate(self._batch.schema)
        }
        self._index += 1
        if self._index == self._batch.num_rows:
            self._advance_batch()
        return row

    def close(self) -> None:
        """Release the run handle before a merged generation is removed."""
        self._parquet.close()


def _merge_run_rows(
    paths: Sequence[Path],
    *,
    keys: Sequence[str],
    read_batch_size: int = 256,
) -> Iterator[Mapping[str, object]]:
    """Merge a bounded set of sorted runs and reject duplicate sort keys."""
    cursors = [_RunCursor(path, batch_size=read_batch_size) for path in paths]
    heap: list[tuple[tuple[object, ...], int]] = []
    for index, cursor in enumerate(cursors):
        if not cursor.exhausted:
            heapq.heappush(heap, (cursor.key(keys), index))
    previous_key: tuple[object, ...] | None = None
    try:
        while heap:
            key, index = heapq.heappop(heap)
            if key == previous_key:
                raise ValueError(f"external-sort keys are not unique: {key}")
            previous_key = key
            cursor = cursors[index]
            yield cursor.pop()
            if not cursor.exhausted:
                heapq.heappush(heap, (cursor.key(keys), index))
    finally:
        for cursor in cursors:
            cursor.close()


def _write_merged_run(
    path: Path,
    paths: Sequence[Path],
    *,
    schema: pa.Schema,
    keys: Sequence[str],
    output_rows: int,
) -> None:
    """Materialize one bounded fan-in merge without loading the run into memory."""
    writer = pq.ParquetWriter(
        path,
        schema,
        compression="zstd",
        compression_level=1,
        use_dictionary=False,
        write_statistics=False,
        version="2.6",
    )
    pending: list[Mapping[str, object]] = []
    try:
        for row in _merge_run_rows(paths, keys=keys):
            pending.append(row)
            if len(pending) == output_rows:
                writer.write_table(
                    pa.Table.from_pylist(pending, schema=schema),
                    row_group_size=len(pending),
                )
                pending = []
        if pending:
            writer.write_table(
                pa.Table.from_pylist(pending, schema=schema),
                row_group_size=len(pending),
            )
    finally:
        writer.close()


def external_sort_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    schema: pa.Schema,
    keys: Sequence[str],
    temporary_root: Path,
    run_rows: int = 65_536,
    output_rows: int = 65_536,
    merge_fan_in: int = 32,
) -> Iterator[pa.RecordBatch]:
    """Stable-key sort arbitrary input with bounded in-memory row batches.

    Sort keys are required to identify rows uniquely; this makes the output order
    independent of the sort implementation's tie policy.
    """
    if run_rows < 1 or output_rows < 1:
        raise ValueError("sort batch sizes must be positive")
    if merge_fan_in < 2:
        raise ValueError("external-sort merge_fan_in must be at least 2")
    if not keys or any(schema.get_field_index(key) < 0 for key in keys):
        raise ValueError("every external-sort key must be present in the schema")
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temporary_root, prefix=".tradebot-sort-") as name:
        directory = Path(name)
        run_paths: list[Path] = []
        pending: list[Mapping[str, Any]] = []
        for row in rows:
            pending.append(row)
            if len(pending) == run_rows:
                path = directory / f"run-{len(run_paths):08d}.parquet"
                _write_sort_run(path, schema, pending, keys)
                run_paths.append(path)
                pending = []
        if pending:
            path = directory / f"run-{len(run_paths):08d}.parquet"
            _write_sort_run(path, schema, pending, keys)
            run_paths.append(path)
        if not run_paths:
            return

        generation = 0
        while len(run_paths) > merge_fan_in:
            merged_paths: list[Path] = []
            for offset in range(0, len(run_paths), merge_fan_in):
                group = run_paths[offset : offset + merge_fan_in]
                path = directory / (f"merge-{generation:04d}-{len(merged_paths):08d}.parquet")
                _write_merged_run(
                    path,
                    group,
                    schema=schema,
                    keys=keys,
                    output_rows=output_rows,
                )
                merged_paths.append(path)
            for path in run_paths:
                path.unlink()
            run_paths = merged_paths
            generation += 1

        output: list[Mapping[str, Any]] = []
        for row in _merge_run_rows(run_paths, keys=keys):
            output.append(row)
            if len(output) == output_rows:
                yield pa.RecordBatch.from_pylist(output, schema=schema)
                output = []
        if output:
            yield pa.RecordBatch.from_pylist(output, schema=schema)
