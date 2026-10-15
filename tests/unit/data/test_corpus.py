from __future__ import annotations

import gzip
import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from tradebot.core.time_rules import fx_session_bounds as canonical_fx_session
from tradebot.data.acquisition_probe import (
    CANONICAL_TICK_HEADER,
    ChunkRequest,
    SourceTick,
    encode_source_tick,
    fx_session_bounds,
)
from tradebot.data.corpus import (
    ProbeArtifact,
    ProbeArtifactError,
    _CachedBoundary,
    corpus_identity,
    import_raw_artifact,
    rebuild_from_raw,
)
from tradebot.data.quality import DataQualityFlag, QualityThresholds
from tradebot.data.source_clock import SourceClockPolicy
from tradebot.data.storage import sha256_path


class _RecordingCalendar:
    def __init__(self) -> None:
        self.lookups: list[tuple[str, date, datetime]] = []

    def lookup(
        self,
        instrument: str,
        day: date,
        *,
        known_at: datetime,
    ) -> None:
        self.lookups.append((instrument, day, known_at))
        return None


def _clock_policy(*, hours: int = -3, instrument: str = "EURUSD") -> SourceClockPolicy:
    return SourceClockPolicy.from_dict(
        {
            "schema_version": 1,
            "policy_id": "fixture-explicit-clock-v1",
            "source": "FBS-Demo",
            "instrument": instrument,
            "status": "EXPERIMENTAL_ONLY",
            "label_start": "2024-10-01T00:00:00Z",
            "label_end": "2024-11-01T00:00:00Z",
            "intervals": [
                {
                    "label_start": "2024-10-01T00:00:00Z",
                    "label_end": "2024-11-01T00:00:00Z",
                    "offset_seconds_to_utc": hours * 3600,
                }
            ],
            "evidence_sha256": {"fixture": "a" * 64},
        }
    )


def _artifact(tmp_path: Path) -> ProbeArtifact:
    session_date = date(2024, 10, 21)
    start, end = fx_session_bounds(session_date)
    request = ChunkRequest(
        logical_symbol="EURUSD",
        broker_symbol="EURUSD",
        window_id="fixture",
        session_date=session_date,
        index_in_window=0,
        start=start,
        end=end,
    )
    quotes = (
        (start + timedelta(seconds=10), "1.1000", "1.1002"),
        # Invalid evidence stays in clean ticks and flags the interval, but its
        # crossed midpoint must not enter the bar.
        (start + timedelta(seconds=20), "1.2000", "1.1000"),
        (start + timedelta(seconds=30), "1.1002", "1.1004"),
        # Three valid spreads sum to 0.0007, whose exact mean repeats and must
        # survive Parquet without an implicit scale-18 rounding policy.
        (start + timedelta(seconds=40), "1.1004", "1.1007"),
        (start + timedelta(minutes=1, seconds=10), "1.1003", "1.1005"),
    )
    encoded = [CANONICAL_TICK_HEADER]
    for moment, bid, ask in quotes:
        encoded.append(
            encode_source_tick(
                SourceTick(
                    time=int(moment.timestamp()),
                    time_msc=int(moment.timestamp() * 1000),
                    bid=Decimal(bid),
                    ask=Decimal(ask),
                    last=Decimal("0"),
                    volume=0,
                    flags=6,
                    volume_real=Decimal("0"),
                )
            )
        )
    semantic = b"".join(encoded)
    source_path = tmp_path / "source.tsv.gz"
    with gzip.open(source_path, "wb") as stream:
        stream.write(semantic)
    return ProbeArtifact(
        request=request,
        ordinal=17,
        plan_hash=hashlib.sha256(b"plan").hexdigest(),
        source="FBS-Demo",
        run_id="fixture-run",
        completed_at=datetime(2026, 9, 4, tzinfo=UTC),
        raw_path=source_path,
        checkpoint_path=tmp_path / "source.checkpoint.json",
        semantic_sha256=hashlib.sha256(semantic).hexdigest(),
        compressed_sha256=sha256_path(source_path),
        expected_rows=len(quotes),
        artifact_id=hashlib.sha256(b"artifact").hexdigest(),
    )


def _empty_artifact(tmp_path: Path, session_date: date) -> ProbeArtifact:
    start, end = fx_session_bounds(session_date)
    request = ChunkRequest(
        logical_symbol="EURUSD",
        broker_symbol="EURUSD",
        window_id="reference-empty-target",
        session_date=session_date,
        index_in_window=0,
        start=start,
        end=end,
    )
    semantic = CANONICAL_TICK_HEADER
    source_path = tmp_path / "empty-target.tsv.gz"
    with gzip.open(source_path, "wb") as stream:
        stream.write(semantic)
    return ProbeArtifact(
        request=request,
        ordinal=18,
        plan_hash=hashlib.sha256(b"plan").hexdigest(),
        source="FBS-Demo",
        run_id="fixture-run",
        completed_at=datetime(2026, 9, 4, tzinfo=UTC),
        raw_path=source_path,
        checkpoint_path=tmp_path / "empty-target.checkpoint.json",
        semantic_sha256=hashlib.sha256(semantic).hexdigest(),
        compressed_sha256=sha256_path(source_path),
        expected_rows=0,
        artifact_id=hashlib.sha256(b"empty-target-artifact").hexdigest(),
    )


def test_same_raw_snapshot_rebuilds_byte_identical_clean_ticks_and_bars(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    imported = import_raw_artifact(artifact, data_root=tmp_path / "immutable-raw", batch_size=2)
    first = rebuild_from_raw(
        (imported,),
        data_root=tmp_path / "rebuild-a",
        venue="FBS-Demo",
        timeframes=("1m", "1d"),
        batch_size=2,
    )
    second = rebuild_from_raw(
        (imported,),
        data_root=tmp_path / "rebuild-b",
        venue="FBS-Demo",
        timeframes=("1m", "1d"),
        batch_size=2,
    )

    assert first.corpus_id == second.corpus_id
    assert first.dataset_id == second.dataset_id
    assert first.clean_manifest == second.clean_manifest
    assert first.quality == second.quality
    assert first.bar_rows_by_timeframe == (("1d", 1), ("1m", 2))

    clean_rows = pq.read_table(first.clean_tick_files[0]).to_pylist()
    assert len(clean_rows) == 5
    crossed = next(row for row in clean_rows if row["ask"] < row["bid"])
    assert DataQualityFlag.CROSSED_QUOTE in crossed["quality_flags"]
    assert not crossed["eligible_for_bars"]

    minute_rows = pq.read_table(
        next(path for path in first.clean_bar_files if "1m" in path.parts)
    ).to_pylist()
    assert minute_rows[0]["n_ticks"] == 3
    assert minute_rows[0]["spread_mean"] == Decimal("0.0007") / 3
    assert DataQualityFlag.CROSSED_QUOTE in minute_rows[0]["quality_flags"]


def test_source_position_sequence_and_corpus_settings_are_stable_and_complete(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    first = import_raw_artifact(artifact, data_root=tmp_path / "raw-a", batch_size=2)
    moved_in_plan = replace(artifact, ordinal=999)
    second = import_raw_artifact(moved_in_plan, data_root=tmp_path / "raw-b", batch_size=2)
    first_seq = pq.read_table(first.files[0], columns=["seq"])["seq"].to_pylist()
    second_seq = pq.read_table(second.files[0], columns=["seq"])["seq"].to_pylist()
    assert first_seq == second_seq

    immediate = corpus_identity(
        (artifact,),
        thresholds=QualityThresholds(),
        calendar_id=None,
        known_at=None,
        timeframes=("1m",),
        seal_latency=timedelta(0),
    )
    delayed = corpus_identity(
        (artifact,),
        thresholds=QualityThresholds(),
        calendar_id=None,
        known_at=None,
        timeframes=("1m",),
        seal_latency=timedelta(seconds=1),
    )
    assert immediate != delayed


def test_calendar_binding_changes_identity_and_defaults_to_source_instrument(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    thresholds = QualityThresholds()
    known_at = datetime(2026, 9, 5, tzinfo=UTC)
    default = corpus_identity(
        (artifact,),
        thresholds=thresholds,
        calendar_id="a" * 64,
        known_at=known_at,
        timeframes=("1m",),
        seal_latency=timedelta(0),
    )
    explicit_default = corpus_identity(
        (artifact,),
        thresholds=thresholds,
        calendar_id="a" * 64,
        known_at=known_at,
        timeframes=("1m",),
        seal_latency=timedelta(0),
        calendar_instrument="FBS-Demo/EURUSD",
    )
    alternate = corpus_identity(
        (artifact,),
        thresholds=thresholds,
        calendar_id="a" * 64,
        known_at=known_at,
        timeframes=("1m",),
        seal_latency=timedelta(0),
        calendar_instrument="approved/EURUSD",
    )

    assert default == explicit_default
    assert alternate != default


def test_rebuild_uses_source_scoped_calendar_key_and_session_close_date(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    imported = import_raw_artifact(
        artifact,
        data_root=tmp_path / "immutable-raw",
        batch_size=2,
    )
    known_at = datetime(2026, 9, 5, tzinfo=UTC)
    calendar = _RecordingCalendar()

    rebuilt = rebuild_from_raw(
        (imported,),
        data_root=tmp_path / "clean",
        venue="FBS",
        timeframes=("1m",),
        calendar=calendar,
        calendar_id="a" * 64,
        known_at=known_at,
        batch_size=2,
    )

    assert set(calendar.lookups) == {("FBS-Demo/EURUSD", date(2024, 10, 22), known_at)}
    assert rebuilt.quality[0].calendar_days_missing == ("2024-10-22",)


def test_zero_row_reference_target_survives_import_and_rebuild_identity(
    tmp_path: Path,
) -> None:
    populated = _artifact(tmp_path)
    empty_target = _empty_artifact(tmp_path, date(2024, 10, 22))
    populated_import = import_raw_artifact(
        populated,
        data_root=tmp_path / "immutable-raw",
        batch_size=2,
    )
    empty_import = import_raw_artifact(
        empty_target,
        data_root=tmp_path / "immutable-raw",
        batch_size=2,
    )
    assert empty_import.rows == 0
    assert empty_import.files == ()

    with_empty_target = rebuild_from_raw(
        (populated_import, empty_import),
        data_root=tmp_path / "clean-with-empty-target",
        venue="FBS",
        timeframes=("1m",),
        batch_size=2,
    )
    without_empty_target = rebuild_from_raw(
        (populated_import,),
        data_root=tmp_path / "clean-without-empty-target",
        venue="FBS",
        timeframes=("1m",),
        batch_size=2,
    )

    assert with_empty_target.source_rows == populated.expected_rows
    assert with_empty_target.corpus_id != without_empty_target.corpus_id
    assert with_empty_target.quality[0].calendar_days_missing == (
        "2024-10-22",
        "2024-10-23",
    )


def test_cached_boundary_matches_canonical_across_dst_weekends_and_backwards() -> None:
    moments = (
        datetime(2024, 3, 8, 21, 59, tzinfo=UTC),
        datetime(2024, 3, 8, 22, 0, tzinfo=UTC),
        datetime(2024, 3, 10, 20, 59, tzinfo=UTC),
        datetime(2024, 3, 10, 21, 0, tzinfo=UTC),
        datetime(2024, 3, 11, 12, 0, tzinfo=UTC),
        datetime(2024, 11, 1, 20, 59, tzinfo=UTC),
        datetime(2024, 11, 1, 21, 0, tzinfo=UTC),
        datetime(2024, 11, 3, 21, 59, tzinfo=UTC),
        datetime(2024, 11, 3, 22, 0, tzinfo=UTC),
        datetime(2024, 11, 4, 10, 0, tzinfo=UTC),
    )
    cached = _CachedBoundary(canonical_fx_session)
    for moment in (*moments, *reversed(moments)):
        assert cached(moment) == canonical_fx_session(moment)

    invalid = (
        datetime(2024, 10, 1),
        datetime(2024, 10, 1, tzinfo=UTC).astimezone(timezone(timedelta(hours=2))),
    )
    for moment in invalid:
        with pytest.raises(ValueError):
            canonical_fx_session(moment)
        with pytest.raises(ValueError):
            cached(moment)


def test_cached_boundary_delegates_only_once_inside_the_same_session() -> None:
    calls = 0

    def counted(ts: datetime) -> tuple[datetime, datetime] | None:
        nonlocal calls
        calls += 1
        return canonical_fx_session(ts)

    cached = _CachedBoundary(counted)
    start = datetime(2024, 10, 21, 21, tzinfo=UTC)
    for minute in range(1_000):
        assert cached(start + timedelta(minutes=minute)) is not None
    assert calls == 1


def test_explicit_clock_rebuild_is_separate_reproducible_and_preserves_raw(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    imported = import_raw_artifact(artifact, data_root=tmp_path / "raw", batch_size=2)
    hashes = {path: sha256_path(path) for path in (artifact.raw_path, *imported.files)}
    raw_rows = pq.ParquetFile(imported.files[0]).read().to_pylist()
    clock = _clock_policy()
    outputs = [
        rebuild_from_raw(
            (imported,),
            data_root=tmp_path / name,
            venue="FBS",
            timeframes=("1m", "1d"),
            source_clock=clock,
            batch_size=2,
        )
        for name in ("clock-a", "clock-b")
    ]
    baseline = rebuild_from_raw(
        (imported,), data_root=tmp_path / "baseline", venue="FBS", timeframes=("1m", "1d")
    )
    first, second = outputs
    assert first.corpus_id != baseline.corpus_id
    assert first.dataset_id == second.dataset_id
    assert first.clean_manifest == second.clean_manifest
    assert hashes == {path: sha256_path(path) for path in hashes}
    ticks = pq.ParquetFile(first.clean_tick_files[0]).read().to_pylist()
    assert len(ticks) == len(raw_rows)
    assert ticks[0]["ts_event"] == artifact.request.start - timedelta(hours=3) + timedelta(
        seconds=10
    )
    assert ticks[0]["ts_recv"] == ticks[0]["ts_event"] == ticks[0]["available_at"]
    assert [tick["seq"] for tick in ticks] == [row["seq"] for row in raw_rows]
    assert [tick["bid"] for tick in ticks] == [Decimal(row["bid_text"]) for row in raw_rows]
    assert [tick["ask"] for tick in ticks] == [Decimal(row["ask_text"]) for row in raw_rows]
    assert DataQualityFlag.CROSSED_QUOTE in ticks[1]["quality_flags"]
    assert "TS_RECV_IMPUTED" in ticks[0]["quality_flags"]
    metadata = pq.ParquetFile(first.clean_tick_files[0]).schema_arrow.metadata
    assert metadata is not None
    assert metadata[b"tradebot.source_clock_policy"] == clock.identity.encode()
    minute_file = next(path for path in first.clean_bar_files if "1m" in path.parts)
    bars = pq.ParquetFile(minute_file).read().to_pylist()
    assert bars[0]["ts_open"] == artifact.request.start - timedelta(hours=3)


def test_clock_scope_rejection_precedes_clean_publication_even_for_empty_chunk(
    tmp_path: Path,
) -> None:
    artifact = _empty_artifact(tmp_path, date(2024, 10, 22))
    imported = import_raw_artifact(artifact, data_root=tmp_path / "raw")
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        rebuild_from_raw(
            (imported,),
            data_root=output,
            venue="FBS",
            source_clock=_clock_policy(instrument="GBPUSD"),
        )
    assert not output.exists()


def test_clock_policy_changes_identity_without_modifying_default_identity(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)

    def identity(clock: SourceClockPolicy | None) -> str:
        return corpus_identity(
            (artifact,),
            thresholds=QualityThresholds(),
            calendar_id=None,
            known_at=None,
            timeframes=("1m",),
            seal_latency=timedelta(0),
            source_clock=clock,
        )

    assert identity(None) == corpus_identity(
        (artifact,),
        thresholds=QualityThresholds(),
        calendar_id=None,
        known_at=None,
        timeframes=("1m",),
        seal_latency=timedelta(0),
    )
    assert len({identity(None), identity(_clock_policy(hours=-2)), identity(_clock_policy())}) == 3


def test_empty_request_cannot_hide_an_internal_clock_policy_hole(tmp_path: Path) -> None:
    artifact = _empty_artifact(tmp_path, date(2024, 10, 22))
    imported = import_raw_artifact(artifact, data_root=tmp_path / "raw")
    definition = _clock_policy().to_dict()
    definition["intervals"] = [
        {
            "label_start": "2024-10-01T00:00:00Z",
            "label_end": "2024-10-23T02:00:00Z",
            "offset_seconds_to_utc": -10800,
        },
        {
            "label_start": "2024-10-23T03:00:00Z",
            "label_end": "2024-11-01T00:00:00Z",
            "offset_seconds_to_utc": -10800,
        },
    ]
    clock = SourceClockPolicy.from_dict(definition)
    output = tmp_path / "hole-rejected"
    with pytest.raises(ProbeArtifactError, match="uncovered"):
        rebuild_from_raw((imported,), data_root=output, venue="FBS", source_clock=clock)
    assert not output.exists()
