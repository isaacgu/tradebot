from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from tradebot.data.storage import (
    FileDigest,
    ImmutableDatasetError,
    ImmutableParquetWriter,
    clean_bar_path,
    clean_tick_path,
    dataset_id,
    external_sort_rows,
    file_manifest,
    parquet_metadata,
    raw_tick_path,
    sha256_path,
)

_DIGEST = "a" * 64


def test_partition_paths_keep_venue_in_the_clean_series_key(tmp_path: Path) -> None:
    month = date(2024, 10, 1)
    raw = raw_tick_path(
        tmp_path,
        source="FBS-Demo",
        instrument="GBPUSD",
        month=month,
        artifact_id=_DIGEST,
    )
    ticks = clean_tick_path(
        tmp_path,
        venue="FBS-Demo",
        instrument="GBPUSD",
        month=month,
        corpus_id=_DIGEST,
    )
    bars = clean_bar_path(
        tmp_path,
        venue="FBS-Demo",
        timeframe="1m",
        instrument="GBPUSD",
        month=month,
        corpus_id=_DIGEST,
    )
    assert raw.relative_to(tmp_path).parts[:5] == ("raw", "FBS-Demo", "GBPUSD", "2024", "10")
    assert ticks.relative_to(tmp_path).parts[:6] == (
        "clean",
        "ticks",
        "FBS-Demo",
        "GBPUSD",
        "2024",
        "10",
    )
    assert bars.relative_to(tmp_path).parts[:7] == (
        "clean",
        "bars",
        "FBS-Demo",
        "1m",
        "GBPUSD",
        "2024",
        "10",
    )


@pytest.mark.parametrize("bad", ["", "../GBPUSD", "a/b", ".", "..", "GBP USD"])
def test_partition_paths_reject_ambiguous_segments(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        clean_tick_path(
            tmp_path,
            venue="FBS-Demo",
            instrument=bad,
            month=date(2024, 1, 1),
            corpus_id=_DIGEST,
        )


def test_immutable_writer_is_copy_once_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = pa.schema(
        [
            pa.field("ts", pa.timestamp("ns", tz="UTC"), nullable=False),
            pa.field("value", pa.int64(), nullable=False),
        ],
        metadata={b"tradebot.schema": b"test-v1"},
    )
    path = tmp_path / "immutable.parquet"
    rows = [{"ts": datetime(2024, 1, 1, tzinfo=UTC), "value": 7}]
    real_fsync = os.fsync

    def windows_compatible_fsync(descriptor: int) -> None:
        # A zero-byte write is content-neutral but fails with EBADF when the
        # descriptor is read-only, reproducing Windows' fsync requirement on CI.
        assert os.write(descriptor, b"") == 0
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", windows_compatible_fsync)
    with ImmutableParquetWriter(path, schema, identity={"tradebot.identity": "same"}) as writer:
        writer.write_rows(rows)
    assert writer.created
    first_hash = sha256_path(path)
    assert pq.read_table(path).to_pylist() == rows
    assert parquet_metadata(path)["tradebot.identity"] == "same"

    with ImmutableParquetWriter(path, schema, identity={"tradebot.identity": "same"}) as repeated:
        repeated.write_rows([{"ts": datetime(2025, 1, 1, tzinfo=UTC), "value": 99}])
    assert not repeated.created
    assert sha256_path(path) == first_hash
    assert pq.read_table(path).to_pylist() == rows

    with pytest.raises(ImmutableDatasetError, match="identity differs"):
        ImmutableParquetWriter(path, schema, identity={"tradebot.identity": "different"})


def test_external_sort_is_bounded_by_runs_and_uses_unique_replay_keys(tmp_path: Path) -> None:
    schema = pa.schema(
        [
            pa.field("available_at", pa.timestamp("ns", tz="UTC"), nullable=False),
            pa.field("source", pa.string(), nullable=False),
            pa.field("seq", pa.int64(), nullable=False),
        ]
    )
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = [
        {"available_at": start, "source": "b", "seq": 2},
        {"available_at": start, "source": "a", "seq": 3},
        {"available_at": start, "source": "a", "seq": 1},
        {"available_at": start, "source": "b", "seq": 0},
        {"available_at": start, "source": "c", "seq": 5},
        {"available_at": start, "source": "c", "seq": 4},
    ]
    batches = external_sort_rows(
        rows,
        schema=schema,
        keys=("available_at", "source", "seq"),
        temporary_root=tmp_path,
        run_rows=1,
        output_rows=3,
        merge_fan_in=2,
    )
    observed = [row for batch in batches for row in batch.to_pylist()]
    assert [(row["source"], row["seq"]) for row in observed] == [
        ("a", 1),
        ("a", 3),
        ("b", 0),
        ("b", 2),
        ("c", 4),
        ("c", 5),
    ]
    assert not list(tmp_path.glob(".tradebot-sort-*"))


def test_dataset_manifest_binds_relative_names_and_bytes(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    manifest = file_manifest((second, first), relative_to=tmp_path)
    assert [item.path for item in manifest] == ["a", "b"]
    identity = dataset_id(manifest)
    assert identity == dataset_id(tuple(manifest))
    assert identity != dataset_id((FileDigest("a", manifest[1].sha256), manifest[1]))
    with pytest.raises(ValueError, match="relative-path order"):
        dataset_id(tuple(reversed(manifest)))
