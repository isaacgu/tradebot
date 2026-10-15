"""Read only explicitly named, immutable clean-bar snapshots for decision replay.

No discovery or live-feed fallback is performed. Receipt times remain the actual
stored times, even when a historical bar only became available much later.
"""

from __future__ import annotations

import heapq
import json
import re
from collections.abc import Generator, Iterator, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from tradebot.core.types import Bar, VolumeKind
from tradebot.data.storage import (
    CLEAN_BAR_SCHEMA,
    FileDigest,
    ImmutableDatasetError,
    dataset_id,
    safe_segment,
    sha256_path,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PART = re.compile(r"part-([0-9a-f]{64})\.parquet\Z")
_TIMESTAMPS = ("ts_open", "ts_close", "ts_recv", "available_at")
# A fixed batch bounds working memory per input file; it is not a research parameter.
_BATCH_SIZE = 8192
type ReplayKey = tuple[datetime, str, int, str]
type FileState = tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class ReplayBar:
    """Closed bar plus immutable storage provenance; timestamps are UTC."""

    bar: Bar
    source: str
    seq: int

    def __post_init__(self) -> None:
        if not isinstance(self.bar, Bar):
            raise TypeError("bar must be Bar")
        safe_segment(self.source, field="source")
        if type(self.seq) is not int:
            raise TypeError("seq must be int")
        if self.seq < 0:
            raise ValueError("seq cannot be negative")
        if self.bar.available_at < max(self.bar.ts_close, self.bar.ts_recv):
            raise ValueError("bar availability precedes its event or receipt time")

    @property
    def key(self) -> ReplayKey:
        """Order by availability/source/sequence, then instrument for scoped-seq ties."""
        return self.bar.available_at, self.source, self.seq, self.bar.instrument


def _path_parts(relative: str, *, venue: str, timeframe: str) -> tuple[str, ...]:
    if not isinstance(relative, str):
        raise TypeError("snapshot path must be str")
    parts = PurePosixPath(relative).parts
    if (
        "\\" in relative
        or ":" in relative
        or len(parts) != 8
        or "/".join(parts) != relative
        or parts[:4] != ("clean", "bars", venue, timeframe)
        or parts[4] not in {"GBPUSD", "EURUSD"}
        or re.fullmatch(r"[0-9]{4}", parts[5]) is None
        or re.fullmatch(r"[0-9]{2}", parts[6]) is None
        or not 1 <= int(parts[5]) <= 9999
        or not 1 <= int(parts[6]) <= 12
        or _PART.fullmatch(parts[7]) is None
    ):
        raise ValueError("snapshot path must be a canonical clean-bar FX partition")
    return parts


@dataclass(frozen=True, slots=True)
class SnapshotSpec:
    """Version-1 manifest of a single venue/timeframe; dataset_id hashes selected files."""

    venue: str
    timeframe: str
    files: tuple[FileDigest, ...]
    dataset_id: str
    schema_version: ClassVar[int] = 1

    def __post_init__(self) -> None:
        safe_segment(self.venue, field="venue")
        safe_segment(self.timeframe, field="timeframe")
        if type(self.files) is not tuple or any(
            not isinstance(item, FileDigest) for item in self.files
        ):
            raise TypeError("files must be tuple[FileDigest, ...]")
        if not self.files:
            raise ValueError("snapshot manifest cannot be empty")
        for item in self.files:
            _path_parts(item.path, venue=self.venue, timeframe=self.timeframe)
            if not isinstance(item.sha256, str) or _SHA256.fullmatch(item.sha256) is None:
                raise ValueError("file sha256 must be a lowercase SHA-256 digest")
        expected = dataset_id(self.files)
        if not isinstance(self.dataset_id, str) or self.dataset_id != expected:
            raise ValueError("dataset_id does not match the canonical selected-file manifest")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _manifest_object(value: object, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} must contain exactly: {', '.join(sorted(fields))}")
    return value


def load_snapshot_spec(path: Path) -> SnapshotSpec:
    """Load a strict version-1 JSON snapshot, rejecting duplicates and unknown fields."""
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    row = _manifest_object(
        value,
        {"schema_version", "venue", "timeframe", "files", "dataset_id"},
        label="snapshot",
    )
    if type(row["schema_version"]) is not int or row["schema_version"] != 1:
        raise ValueError("snapshot schema_version must be integer 1")
    if not isinstance(row["files"], list):
        raise TypeError("snapshot files must be a JSON array")
    files: list[FileDigest] = []
    for item in row["files"]:
        record = _manifest_object(item, {"path", "sha256"}, label="snapshot file")
        files.append(FileDigest(record["path"], record["sha256"]))
    return SnapshotSpec(row["venue"], row["timeframe"], tuple(files), row["dataset_id"])


def _timestamp(value: object, *, field: str) -> datetime:
    if type(value) is not int:
        raise ValueError(f"{field} must be a non-null integer nanosecond timestamp")
    if value % 1000:
        raise ValueError(f"{field} has sub-microsecond precision not representable by Bar")
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value // 1000)


def _replay_bar(
    row: Mapping[str, Any], *, venue: str, instrument: str, source: bytes | None
) -> ReplayBar:
    if row["instrument"] != instrument:
        raise ValueError("row instrument differs from the canonical partition")
    if source is not None and row["source"].encode("utf-8") != source:
        raise ValueError("row source differs from Parquet identity metadata")
    stamps = {name: _timestamp(row[name], field=name) for name in _TIMESTAMPS}
    if stamps["available_at"] != stamps["ts_recv"]:
        raise ValueError("persisted available_at must equal Bar.ts_recv exactly")
    bar = Bar(
        instrument=f"{venue}/{instrument}",
        ts_open=stamps["ts_open"],
        ts_event=stamps["ts_close"],
        ts_recv=stamps["ts_recv"],
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        volume_kind=VolumeKind(row["volume_kind"]) if row["volume_kind"] is not None else None,
        n_ticks=row["n_ticks"],
        spread_mean=row["spread_mean"],
        quality_flags=tuple(row["quality_flags"]),
    )
    return ReplayBar(bar, row["source"], row["seq"])


class SnapshotBarFeed:
    """Bounded Parquet merge with complete preflight and completion hash verification."""

    def __init__(self, root: Path, spec: SnapshotSpec) -> None:
        if not isinstance(spec, SnapshotSpec):
            raise TypeError("spec must be SnapshotSpec")
        self._root = root.resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError("snapshot root must be a directory")
        self._spec = spec

    @property
    def spec(self) -> SnapshotSpec:
        """Return the immutable selected-file manifest."""
        return self._spec

    def _verify_file(self, item: FileDigest) -> tuple[Path, FileState]:
        path = self._root.joinpath(*PurePosixPath(item.path).parts)
        resolved = path.resolve(strict=True)
        if resolved != path or not resolved.is_relative_to(self._root):
            raise ValueError("snapshot path has a symlink or noncanonical resolution")
        if not path.is_file():
            raise ValueError("snapshot path must name a regular file")
        before = path.stat()
        digest = sha256_path(path)
        after = path.stat()
        state = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        current = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if digest != item.sha256 or state != current or path.resolve(strict=True) != path:
            raise ImmutableDatasetError(f"snapshot file changed or hash differs: {item.path}")
        return path, state

    def _file_records(self, path: Path, item: FileDigest) -> Generator[ReplayBar]:
        parts = PurePosixPath(item.path).parts
        with path.open("rb") as stream:
            parquet = pq.ParquetFile(stream)
            schema = parquet.schema_arrow
            if not schema.remove_metadata().equals(CLEAN_BAR_SCHEMA.remove_metadata()):
                raise ValueError(f"incompatible clean-bar schema: {item.path}")
            metadata = schema.metadata or {}
            if metadata.get(b"tradebot.schema") != b"clean-bar-v2":
                raise ValueError(f"missing or incompatible tradebot.schema metadata: {item.path}")
            expected = {
                b"tradebot.kind": b"clean-bar",
                b"tradebot.venue": self.spec.venue.encode("utf-8"),
                b"tradebot.timeframe": self.spec.timeframe.encode("utf-8"),
                b"tradebot.instrument": parts[4].encode("utf-8"),
                b"tradebot.month": f"{parts[5]}-{parts[6]}".encode("ascii"),
                b"tradebot.corpus_id": parts[7][5:-8].encode("ascii"),
            }
            if any(
                name in metadata and metadata[name] != value for name, value in expected.items()
            ):
                raise ValueError(f"Parquet identity metadata differs from snapshot: {item.path}")
            if parquet.metadata.num_rows == 0:
                raise ValueError(f"snapshot contains an empty bar file: {item.path}")
            previous: ReplayKey | None = None
            for batch in parquet.iter_batches(batch_size=_BATCH_SIZE):
                columns: dict[str, Any] = {}
                for index, field in enumerate(schema):
                    column = batch.column(index)
                    if not field.nullable and column.null_count:
                        raise ValueError(f"non-nullable field {field.name} contains nulls")
                    # Casting BEFORE Python conversion avoids pyarrow's ns-to-datetime
                    # truncation (and never requires pandas Timestamp as an intermediary).
                    if field.name in _TIMESTAMPS:
                        column = column.cast(pa.int64())
                    columns[field.name] = column.to_pylist()
                for index in range(batch.num_rows):
                    row = {name: values[index] for name, values in columns.items()}
                    record = _replay_bar(
                        row,
                        venue=self.spec.venue,
                        instrument=parts[4],
                        source=metadata.get(b"tradebot.source"),
                    )
                    if (record.bar.ts_close.year, record.bar.ts_close.month) != (
                        int(parts[5]),
                        int(parts[6]),
                    ):
                        raise ValueError("bar close month differs from the canonical partition")
                    if previous is not None and record.key <= previous:
                        raise ValueError(
                            f"bar file order regressed or contains duplicates: {item.path}"
                        )
                    previous = record.key
                    yield record

    def records(self) -> Iterator[ReplayBar]:
        """Yield UTC-availability ordered records; success requires exhausting the iterator.

        All named bytes are hashed before the first bar and after the last bar.
        Equal primary keys use instrument as an explicit deterministic tie-break;
        duplicate full keys and non-increasing per-source/instrument sequences fail.
        """
        verified = [(item, *self._verify_file(item)) for item in self.spec.files]
        previous: ReplayKey | None = None
        sequences: dict[tuple[str, str], int] = {}
        with ExitStack() as stack:
            heap: list[tuple[ReplayKey, int, ReplayBar, Generator[ReplayBar]]] = []
            for index, (item, path, _) in enumerate(verified):
                iterator = self._file_records(path, item)
                stack.callback(iterator.close)
                record = next(iterator)
                heapq.heappush(heap, (record.key, index, record, iterator))
            while heap:
                key, index, record, iterator = heapq.heappop(heap)
                if previous is not None and key <= previous:
                    raise ValueError("merged bar order regressed or contains duplicate keys")
                series = (record.source, record.bar.instrument)
                if series in sequences and record.seq <= sequences[series]:
                    raise ValueError("bar sequence must increase per source and instrument")
                previous = key
                sequences[series] = record.seq
                yield record
                following = next(iterator, None)
                if following is not None:
                    heapq.heappush(heap, (following.key, index, following, iterator))
        for item, _, state in verified:
            _, current = self._verify_file(item)
            if current != state:
                raise ImmutableDatasetError(
                    f"snapshot file identity changed during replay: {item.path}"
                )
