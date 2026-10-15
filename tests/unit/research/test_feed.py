from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import tradebot.research.feed as feed_module
from tradebot.core.types import VolumeKind
from tradebot.data.storage import (
    CLEAN_BAR_SCHEMA,
    FileDigest,
    ImmutableDatasetError,
    clean_bar_path,
    dataset_id,
    file_manifest,
)
from tradebot.research.feed import ReplayBar, SnapshotBarFeed, SnapshotSpec, load_snapshot_spec

BASE = datetime(2026, 8, 3, 12, tzinfo=UTC)


def _row(minute: int = 0, *, instrument: str = "GBPUSD", seq: int | None = None) -> dict[str, Any]:
    opened = BASE + timedelta(minutes=minute)
    closed = opened + timedelta(minutes=1)
    return {
        "instrument": instrument,
        "ts_open": opened,
        "ts_close": closed,
        "ts_recv": closed,
        "available_at": closed,
        "open": Decimal("1.25000"),
        "high": Decimal("1.25030"),
        "low": Decimal("1.24980"),
        "close": Decimal("1.25010"),
        "volume": 10,
        "volume_kind": "TICK_COUNT",
        "n_ticks": 10,
        "spread_mean": Decimal("0.000123456789123456789123456789"),
        "spread_max": Decimal("0.00020"),
        "bid_close": Decimal("1.25000"),
        "ask_close": Decimal("1.25020"),
        "source": "fbs-mt5",
        "seq": minute + 1 if seq is None else seq,
        "quality_flags": ["TS_RECV_IMPUTED"],
    }


def _write(
    root: Path,
    rows: list[dict[str, Any]],
    *,
    identity: str = "a",
    instrument: str = "GBPUSD",
    schema: Any = CLEAN_BAR_SCHEMA,
    metadata: dict[bytes, bytes] | None = None,
) -> Path:
    path = clean_bar_path(
        root,
        venue="fbs",
        timeframe="1m",
        instrument=instrument,
        month=BASE.date(),
        corpus_id=identity * 64,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = {
        b"tradebot.kind": b"clean-bar",
        b"tradebot.corpus_id": (identity * 64).encode(),
        b"tradebot.venue": b"fbs",
        b"tradebot.timeframe": b"1m",
        b"tradebot.instrument": instrument.encode(),
        b"tradebot.source": b"fbs-mt5",
        b"tradebot.month": b"2026-08",
    }
    extra.update(schema.metadata or {})
    extra.update(metadata or {})
    pq.write_table(pa.Table.from_pylist(rows, schema=schema.with_metadata(extra)), path)
    return path


def _spec(root: Path, paths: list[Path]) -> SnapshotSpec:
    files = file_manifest(paths, relative_to=root)
    return SnapshotSpec("fbs", "1m", files, dataset_id(files))


def _manifest(spec: SnapshotSpec) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "venue": spec.venue,
        "timeframe": spec.timeframe,
        "files": [{"path": item.path, "sha256": item.sha256} for item in spec.files],
        "dataset_id": spec.dataset_id,
    }


def test_explicit_snapshot_preserves_boundary_values_and_provenance(tmp_path: Path) -> None:
    row = _row()
    path = _write(tmp_path, [row])
    spec = _spec(tmp_path, [path])
    manifest_path = tmp_path / "snapshot.json"
    manifest_path.write_text(json.dumps(_manifest(spec)), encoding="utf-8")
    assert load_snapshot_spec(manifest_path) == spec
    feed = SnapshotBarFeed(tmp_path, spec)
    assert feed.spec == spec
    (record,) = tuple(feed.records())
    assert record.source == "fbs-mt5"
    assert record.seq == 1
    assert record.key == (BASE + timedelta(minutes=1), "fbs-mt5", 1, "fbs/GBPUSD")
    assert record.bar.close == Decimal("1.25010")
    assert isinstance(record.bar.close, Decimal)
    assert record.bar.spread_mean == row["spread_mean"]
    assert record.bar.volume_kind is VolumeKind.TICK_COUNT
    assert record.bar.quality_flags == ("TS_RECV_IMPUTED",)
    assert tuple(feed.records()) == (record,)


def test_merge_uses_availability_and_explicit_instrument_tiebreak(tmp_path: Path) -> None:
    gbp = _write(tmp_path, [_row(0), _row(2)])
    eur = _write(
        tmp_path, [_row(0, instrument="EURUSD"), _row(1, instrument="EURUSD")], instrument="EURUSD"
    )
    records = tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [gbp, eur])).records())
    assert [(record.bar.instrument, record.seq) for record in records] == [
        ("fbs/EURUSD", 1),
        ("fbs/GBPUSD", 1),
        ("fbs/EURUSD", 2),
        ("fbs/GBPUSD", 3),
    ]
    assert [record.key for record in records] == sorted(record.key for record in records)


def test_late_receipt_is_never_backdated(tmp_path: Path) -> None:
    row = _row()
    row["ts_recv"] = row["available_at"] = datetime(2026, 9, 4, 15, tzinfo=UTC)
    path = _write(tmp_path, [row])
    (record,) = tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())
    assert record.bar.ts_close == BASE + timedelta(minutes=1)
    assert record.key[0] == datetime(2026, 9, 4, 15, tzinfo=UTC)


@pytest.mark.parametrize("field", ["ts_open", "ts_close", "ts_recv", "available_at"])
def test_nanoseconds_cannot_be_silently_truncated(tmp_path: Path, field: str) -> None:
    row = _row()
    exact_ns = pa.scalar(row[field], type=pa.timestamp("ns", tz="UTC")).value
    row[field] = exact_ns + 1
    path = _write(tmp_path, [row])
    with pytest.raises(ValueError, match="microsecond"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())


@pytest.mark.parametrize("delta", [-1, 1])
def test_persisted_availability_must_match_bar(tmp_path: Path, delta: int) -> None:
    row = _row()
    row["available_at"] += timedelta(seconds=delta)
    path = _write(tmp_path, [row])
    with pytest.raises(ValueError, match="available_at"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())


@pytest.mark.parametrize("field,value", [("source", ""), ("seq", True), ("seq", -1)])
def test_replay_bar_validates_source_and_sequence(
    tmp_path: Path, field: str, value: object
) -> None:
    path = _write(tmp_path, [_row()])
    (record,) = tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())
    args: dict[str, Any] = {"bar": record.bar, "source": record.source, "seq": record.seq}
    args[field] = value
    with pytest.raises((TypeError, ValueError)):
        ReplayBar(**args)


@pytest.mark.parametrize(
    "relative",
    [
        "../escape.parquet",
        "/absolute.parquet",
        "C:/absolute.parquet",
        "clean/bars/fbs/1m/GBPUSD/2026/08/../escape.parquet",
        "clean//bars/fbs/1m/GBPUSD/2026/08/part-" + "a" * 64 + ".parquet",
        "clean\\bars\\fbs\\1m\\GBPUSD\\2026\\08\\part-" + "a" * 64 + ".parquet",
        "clean/bars/other/1m/GBPUSD/2026/08/part-" + "a" * 64 + ".parquet",
        "clean/bars/fbs/5m/GBPUSD/2026/08/part-" + "a" * 64 + ".parquet",
        "clean/bars/fbs/1m/US500/2026/08/part-" + "a" * 64 + ".parquet",
    ],
)
def test_rejects_noncanonical_and_out_of_scope_paths(relative: str) -> None:
    files = (FileDigest(relative, "b" * 64),)
    with pytest.raises(ValueError):
        SnapshotSpec("fbs", "1m", files, dataset_id(files))


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, [_row()])
    spec = _spec(tmp_path, [path])
    outside = tmp_path / "outside.parquet"
    path.rename(outside)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match=r"canonical|symlink"):
        tuple(SnapshotBarFeed(tmp_path, spec).records())


def test_every_hash_checked_before_first_record(tmp_path: Path) -> None:
    first = _write(tmp_path, [_row()])
    second = _write(tmp_path, [_row(1)], identity="b")
    feed = SnapshotBarFeed(tmp_path, _spec(tmp_path, [first, second]))
    second.write_bytes(second.read_bytes() + b"tampered")
    with pytest.raises(ImmutableDatasetError):
        next(feed.records())


def test_mutation_during_replay_prevents_success(tmp_path: Path) -> None:
    path = _write(tmp_path, [_row(0), _row(1)])
    records = SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records()
    next(records)
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ImmutableDatasetError):
        tuple(records)


@pytest.mark.parametrize(
    "metadata",
    [
        {b"tradebot.schema": b"clean-bar-v1"},
        {b"tradebot.venue": b"other"},
        {b"tradebot.timeframe": b"5m"},
        {b"tradebot.instrument": b"EURUSD"},
        {b"tradebot.source": b"other"},
        {b"tradebot.month": b"2026-07"},
        {b"tradebot.kind": b"clean-tick"},
        {b"tradebot.corpus_id": b"b" * 64},
    ],
)
def test_identity_metadata_must_match(tmp_path: Path, metadata: dict[bytes, bytes]) -> None:
    path = _write(tmp_path, [_row()], metadata=metadata)
    with pytest.raises(ValueError, match="metadata"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())


def test_schema_is_required_in_full(tmp_path: Path) -> None:
    incomplete = CLEAN_BAR_SCHEMA.remove(CLEAN_BAR_SCHEMA.get_field_index("bid_close"))
    path = _write(tmp_path, [_row()], schema=incomplete)
    with pytest.raises(ValueError, match="schema"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())


def test_row_cannot_change_instrument_partition(tmp_path: Path) -> None:
    path = _write(tmp_path, [_row(instrument="EURUSD")])
    with pytest.raises(ValueError, match="instrument"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())


@pytest.mark.parametrize("rows", [[_row(1), _row(0)], [_row(), _row()]])
def test_file_order_must_be_strict(tmp_path: Path, rows: list[dict[str, Any]]) -> None:
    path = _write(tmp_path, rows)
    with pytest.raises(ValueError, match=r"order|duplicate"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())


def test_duplicate_records_across_files_fail(tmp_path: Path) -> None:
    first = _write(tmp_path, [_row()])
    second = _write(tmp_path, [_row()], identity="b")
    with pytest.raises(ValueError, match="duplicate"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [first, second])).records())


def test_sequence_regression_at_later_time_fails(tmp_path: Path) -> None:
    first = _write(tmp_path, [_row(seq=20)])
    second = _write(tmp_path, [_row(1, seq=19)], identity="b")
    with pytest.raises(ValueError, match="sequence"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [first, second])).records())


def test_empty_manifest_and_empty_data_fail(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        SnapshotSpec("fbs", "1m", (), dataset_id(()))
    path = _write(tmp_path, [])
    with pytest.raises(ValueError, match="empty"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"schema_version": True},
        {"schema_version": 2},
        {"dataset_id": "0" * 64},
        {"files": []},
        {"venue": "../fbs"},
    ],
)
def test_manifest_schema_is_strict(tmp_path: Path, change: dict[str, Any]) -> None:
    path = _write(tmp_path, [_row()])
    value = _manifest(_spec(tmp_path, [path]))
    value.update(change)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises((TypeError, ValueError)):
        load_snapshot_spec(manifest_path)


def test_duplicate_json_keys_fail(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_snapshot_spec(path)


def test_manifest_file_order_and_duplicate_paths_fail(tmp_path: Path) -> None:
    first = _write(tmp_path, [_row()])
    second = _write(tmp_path, [_row(1)], identity="b")
    files = _spec(tmp_path, [first, second]).files
    for invalid in (files[::-1], (files[0], files[0])):
        with pytest.raises(ValueError, match=r"order|unique|duplicate"):
            SnapshotSpec("fbs", "1m", invalid, "0" * 64)


def test_missing_schema_marker_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, [_row()], schema=CLEAN_BAR_SCHEMA.remove_metadata())
    with pytest.raises(ValueError, match="metadata"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())


def test_bare_schema_still_preserves_named_venue(tmp_path: Path) -> None:
    path = _write(tmp_path, [_row()])
    pq.write_table(pa.Table.from_pylist([_row()], schema=CLEAN_BAR_SCHEMA), path)
    (record,) = tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())
    assert record.bar.instrument == "fbs/GBPUSD"


def test_bar_close_must_match_partition_month(tmp_path: Path) -> None:
    row = _row()
    for field in ("ts_open", "ts_close", "ts_recv", "available_at"):
        row[field] = row[field].replace(month=7)
    path = _write(tmp_path, [row])
    with pytest.raises(ValueError, match="month"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())


def test_order_validation_spans_bounded_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(feed_module, "_BATCH_SIZE", 2)
    path = _write(tmp_path, [_row(0), _row(1), _row(2), _row(1)])
    with pytest.raises(ValueError, match="order"):
        tuple(SnapshotBarFeed(tmp_path, _spec(tmp_path, [path])).records())


def test_unnamed_files_are_never_discovered(tmp_path: Path) -> None:
    named = _write(tmp_path, [_row()])
    spec = _spec(tmp_path, [named])
    _write(tmp_path, [_row(1)], identity="b")
    assert len(tuple(SnapshotBarFeed(tmp_path, spec).records())) == 1


@pytest.mark.parametrize("changes", [{"sha256": "A" * 64}, {"extra": "x"}, {"path": None}])
def test_manifest_file_schema_is_strict(tmp_path: Path, changes: dict[str, Any]) -> None:
    path = _write(tmp_path, [_row()])
    manifest = _manifest(_spec(tmp_path, [path]))
    manifest["files"][0].update(changes)
    manifest_path = tmp_path / "snapshot.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((TypeError, ValueError)):
        load_snapshot_spec(manifest_path)
