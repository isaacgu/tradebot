"""Point-in-time fields and expectations cannot borrow knowledge from the future."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tradebot.core.bus import EventBus
from tradebot.core.clock import SimClock
from tradebot.core.errors import LookAheadError
from tradebot.data.calendar import (
    CalendarFieldVintage,
    CalendarStore,
    ExpectedLiquidityCalendar,
    LiquidityDay,
    LiquidityStatus,
    historical_field,
)

T = datetime(2024, 3, 8, 13, 30, tzinfo=UTC)


def field(name: str, vintage: int, offset: int, value: str) -> CalendarFieldVintage:
    return CalendarFieldVintage(
        source="official-release",
        record_id="payrolls-2024-02",
        field=name,
        vintage=str(vintage),
        seq=vintage,
        value=value,
        ts_event=T + timedelta(seconds=offset),
        ts_recv=T + timedelta(seconds=offset),
        available_at=T + timedelta(seconds=offset),
        source_citation="https://example.org/archive/release-vintages",
    )


def test_known_at_preserves_first_print_until_revision_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "calendar.sqlite3"
    consensus = field("consensus", 1, -3600, "200000")
    first = field("actual", 2, 0, "275000")
    revision = field("actual", 3, 3600, "236000")
    with CalendarStore(path) as store:
        store.append(revision)
        store.append(consensus)
        store.append(first)
        assert store.known_at(T - timedelta(microseconds=1)) == (consensus,)
        assert store.known_at(T) == (consensus, first)
        assert store.known_at(T + timedelta(hours=2)) == (consensus, revision)
        assert store.history() == (consensus, first, revision)
    with CalendarStore(path) as reopened:
        assert reopened.known_at(T) == (consensus, first)


def test_duplicate_identity_is_idempotent_but_cannot_rewrite_a_vintage(tmp_path: Path) -> None:
    first = field("actual", 1, 0, "275000")
    with CalendarStore(tmp_path / "calendar.sqlite3") as store:
        assert store.append(first)
        assert not store.append(first)
        with pytest.raises(ValueError, match="immutable"):
            store.append(replace(first, value="999999"))
        assert store.history() == (first,)


def test_source_identity_and_stable_order_are_kept(tmp_path: Path) -> None:
    first = field("actual", 1, 0, "275000")
    other = replace(first, source="second-source", value="274999")
    with CalendarStore(tmp_path / "calendar.sqlite3") as store:
        store.append(other)
        store.append(first)
        assert store.known_at(T) == (first, other)
        with pytest.raises(ValueError, match="sequence"):
            store.append(replace(first, record_id="another-record"))


def test_scheduled_future_release_is_a_value_not_knowledge_event_time(tmp_path: Path) -> None:
    schedule = replace(field("scheduled_at", 1, -3600, ""), value=T)
    with CalendarStore(tmp_path / "calendar.sqlite3") as store:
        store.append(schedule)
        assert store.known_at(T - timedelta(minutes=30)) == (schedule,)


def test_unverified_historical_as_of_is_not_invented() -> None:
    retrieved = T + timedelta(days=365)
    observation = historical_field(
        source="current-webpage",
        record_id="payrolls",
        field="actual",
        vintage="latest",
        seq=1,
        value=Decimal("275000"),
        ts_event=T,
        retrieved_at=retrieved,
        source_citation="https://example.org/current",
        archived_available_at=None,
    )
    assert observation.available_at == retrieved
    assert observation.ts_recv == retrieved
    assert "AS_OF_UNVERIFIED" in observation.quality_flags
    with pytest.raises(ValueError, match="archive citation"):
        historical_field(
            source="archive",
            record_id="payrolls",
            field="actual",
            vintage="first",
            seq=1,
            value="275000",
            ts_event=T,
            retrieved_at=retrieved,
            source_citation="https://example.org/current",
            archived_available_at=T,
        )
    archived = historical_field(
        source="archive",
        record_id="payrolls",
        field="actual",
        vintage="first",
        seq=1,
        value="275000",
        ts_event=T,
        retrieved_at=retrieved,
        source_citation="https://example.org/current",
        archived_available_at=T,
        archive_citation="https://example.org/immutable-vintage/1",
    )
    assert archived.available_at == T
    assert "TS_RECV_IMPUTED" in archived.quality_flags
    assert archived.ingested_at == retrieved


@pytest.mark.parametrize("value", [float("nan"), 2.0, Decimal("NaN")])
def test_noncanonical_calendar_values_are_rejected(value: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        replace(field("actual", 1, 0, "275000"), value=value)  # type: ignore[arg-type]


def test_field_availability_cannot_understate_either_timestamp() -> None:
    with pytest.raises(ValueError, match="available_at"):
        replace(field("actual", 1, 0, "275000"), ts_recv=T + timedelta(seconds=1))


def day(status: LiquidityStatus = LiquidityStatus.FULL) -> LiquidityDay:
    return LiquidityDay(
        instrument="FBS-Demo/EURUSD",
        session_date=date(2024, 3, 8),
        status=status,
        source="broker-notice",
        source_citation="https://example.org/notice/1",
        effective_at=T - timedelta(days=1),
        available_at=T - timedelta(days=2),
        valid_until=T + timedelta(days=3),
        expected_intervals=()
        if status == LiquidityStatus.CLOSED
        else ((T, T + timedelta(hours=1)),),
    )


def test_unknown_future_and_expired_liquidity_are_indeterminate() -> None:
    calendar = ExpectedLiquidityCalendar((day(),))
    assert calendar.lookup("FBS-Demo/EURUSD", date(2024, 3, 8), known_at=T) == day()
    assert calendar.lookup("FBS-Demo/EURUSD", date(2024, 3, 9), known_at=T) is None
    assert calendar.lookup("other/EURUSD", date(2024, 3, 8), known_at=T) is None
    assert (
        calendar.lookup("FBS-Demo/EURUSD", date(2024, 3, 8), known_at=T - timedelta(days=3)) is None
    )
    assert (
        calendar.lookup("FBS-Demo/EURUSD", date(2024, 3, 8), known_at=T + timedelta(days=3)) is None
    )


def test_partial_and_closed_are_explicit_and_revisions_are_causal() -> None:
    full = day()
    closed = replace(day(LiquidityStatus.CLOSED), available_at=T + timedelta(minutes=5))
    calendar = ExpectedLiquidityCalendar((closed, full))
    assert calendar.lookup(full.instrument, full.session_date, known_at=T) == full
    assert (
        calendar.lookup(full.instrument, full.session_date, known_at=T + timedelta(minutes=5))
        == closed
    )
    partial = day(LiquidityStatus.PARTIAL)
    assert partial.expected_seconds == 3600
    assert closed.expected_seconds == 0
    assert full.expects(T)
    assert not full.expects(T + timedelta(hours=1))


def test_ambiguous_calendar_revision_and_invalid_intervals_are_rejected() -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        ExpectedLiquidityCalendar((day(), day(LiquidityStatus.CLOSED)))
    with pytest.raises(ValueError, match="CLOSED"):
        replace(day(), status=LiquidityStatus.CLOSED)
    with pytest.raises(ValueError, match="interval"):
        replace(day(), expected_intervals=())
    with pytest.raises(ValueError, match="overlap"):
        replace(
            day(), expected_intervals=((T, T + timedelta(hours=1)), (T, T + timedelta(hours=2)))
        )


def test_liquidity_json_roundtrip_keeps_vintages_and_source(tmp_path: Path) -> None:
    partial = replace(day(LiquidityStatus.PARTIAL), available_at=T + timedelta(minutes=5))
    calendar = ExpectedLiquidityCalendar((day(), partial))
    path = tmp_path / "liquidity.json"
    calendar.write(path)
    assert ExpectedLiquidityCalendar.read(path).entries == calendar.entries
    with pytest.raises(FileExistsError):
        calendar.write(path)


def test_field_vintages_publish_individually_through_the_bus(tmp_path: Path) -> None:
    clock = SimClock(T)
    bus = EventBus(clock)
    seen: list[CalendarFieldVintage] = []
    bus.subscribe(CalendarFieldVintage, seen.append)
    first = field("actual", 1, 0, "275000")
    revision = field("actual", 2, 3600, "236000")
    with CalendarStore(tmp_path / "calendar.sqlite3") as store:
        store.append(revision)
        store.append(first)
        for event in store.history(known_at=clock.now()):
            bus.publish(event)
    assert seen == [first]
    with pytest.raises(LookAheadError):
        bus.publish(revision)


@pytest.mark.parametrize("value", [Decimal("2.0500"), T, 275000, True, None])
def test_storage_reopens_without_value_type_or_precision_loss(
    tmp_path: Path,
    value: Decimal | datetime | int | bool | None,
) -> None:
    path = tmp_path / "calendar.sqlite3"
    event = replace(field("actual", 1, 0, ""), value=value)
    with CalendarStore(path) as store:
        store.append(event)
    with CalendarStore(path) as store:
        loaded = store.known_at(T)[0]
        assert loaded == event
        assert type(loaded.value) is type(value)
        assert str(loaded.value) == str(value)


def test_expired_latest_calendar_does_not_resurrect_older_expectations() -> None:
    full = day()
    expired = replace(
        day(LiquidityStatus.CLOSED), available_at=T, valid_until=T + timedelta(minutes=1)
    )
    calendar = ExpectedLiquidityCalendar((full, expired))
    assert (
        calendar.lookup(full.instrument, full.session_date, known_at=T + timedelta(minutes=2))
        is None
    )


def test_invalid_calendar_snapshot_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version": 2, "entries": []}')
    with pytest.raises(ValueError, match="schema"):
        ExpectedLiquidityCalendar.read(path)
