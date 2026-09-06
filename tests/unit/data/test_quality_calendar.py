from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from tradebot.core.time_rules import fx_session_bounds, local_time_utc
from tradebot.data.calendar import ExpectedLiquidityCalendar, LiquidityDay, LiquidityStatus
from tradebot.data.quality import (
    CleanTickRecord,
    DataQualityFlag,
    QualityCheckStatus,
    QualityInput,
    QualityThresholds,
    TickQualityPipeline,
)
from tradebot.data.reference_acceptance import _canonical_close_date

_KNOWN_AT = datetime(2024, 1, 1, tzinfo=UTC)


class RecordingCalendar(ExpectedLiquidityCalendar):
    def __init__(self, entries: list[LiquidityDay]) -> None:
        super().__init__(entries)
        self.lookups: set[tuple[str, date]] = set()

    def lookup(self, instrument: str, day: date, *, known_at: datetime) -> LiquidityDay | None:
        self.lookups.add((instrument, day))
        return super().lookup(instrument, day, known_at=known_at)


def _ny(day: date, hour: int = 17) -> datetime:
    return local_time_utc(day, time(hour), "America/New_York")


def _day(day: date, *, closed: bool = False) -> LiquidityDay:
    return LiquidityDay(
        instrument="FBS-Demo/EURUSD",
        session_date=day,
        status=LiquidityStatus.CLOSED if closed else LiquidityStatus.FULL,
        source="test-fixture",
        source_citation="synthetic calendar, not acceptance evidence",
        effective_at=_KNOWN_AT,
        available_at=_KNOWN_AT,
        valid_until=datetime(2025, 1, 1, tzinfo=UTC),
        expected_intervals=() if closed else ((_ny(day - timedelta(days=1)), _ny(day)),),
    )


def _pipeline(calendar: RecordingCalendar) -> TickQualityPipeline:
    return TickQualityPipeline(
        instrument="EURUSD",
        source="FBS-Demo",
        session_boundary=fx_session_bounds,
        calendar=calendar,
        known_at=_KNOWN_AT,
        thresholds=QualityThresholds(
            gap_threshold=timedelta(seconds=2),
            minimum_history=2,
        ),
    )


def _run(pipeline: TickQualityPipeline, instants: list[datetime]) -> tuple[CleanTickRecord, ...]:
    rows: list[CleanTickRecord] = []
    for seq, instant in enumerate(instants):
        rows.extend(
            pipeline.process(
                QualityInput(
                    instrument="EURUSD",
                    source="FBS-Demo",
                    seq=seq,
                    ts_event=instant,
                    bid=Decimal("1.1"),
                    ask=Decimal("1.1002"),
                )
            )
        )
    rows.extend(pipeline.finish())
    return tuple(rows)


@pytest.mark.parametrize(
    ("instant", "close_day"),
    [
        (datetime(2024, 10, 20, 21, tzinfo=UTC), date(2024, 10, 21)),  # Sunday open
        (datetime(2024, 10, 21, 14, tzinfo=UTC), date(2024, 10, 21)),  # Normal Monday
        (datetime(2024, 9, 30, 21, tzinfo=UTC), date(2024, 10, 1)),  # Month boundary
        (datetime(2024, 10, 1, 0, tzinfo=UTC), date(2024, 10, 1)),  # Same session
        (datetime(2024, 10, 27, 21, tzinfo=UTC), date(2024, 10, 28)),  # EU DST only
        (datetime(2024, 11, 3, 22, tzinfo=UTC), date(2024, 11, 4)),  # US DST over
    ],
)
def test_calendar_close_key_agrees_with_reference_evaluator(
    instant: datetime, close_day: date
) -> None:
    calendar = RecordingCalendar([_day(close_day)])
    pipeline = _pipeline(calendar)
    instants = [instant + timedelta(milliseconds=offset) for offset in (0, 200, 400, 5000)]
    rows = _run(pipeline, instants)

    assert {_canonical_close_date(moment) for moment in instants} == {close_day}
    assert calendar.lookups == {("FBS-Demo/EURUSD", close_day)}
    assert DataQualityFlag.GAP in rows[-1].quality_flags
    assert all(DataQualityFlag.GAP_CALENDAR_UNKNOWN not in row.quality_flags for row in rows)
    assert pipeline.summary().calendar_status == QualityCheckStatus.PASSED
    assert pipeline.summary().calendar_days_missing == ()
    assert [row.ts_event for row in rows] == instants


def test_exact_session_boundary_moves_lookup_to_next_close_day() -> None:
    boundary = _ny(date(2024, 9, 30))
    calendar = RecordingCalendar([_day(date(2024, 9, 30)), _day(date(2024, 10, 1))])
    pipeline = _pipeline(calendar)
    rows = _run(
        pipeline,
        [boundary - timedelta(seconds=offset) for offset in (3, 2.8, 2.6)] + [boundary],
    )

    assert _canonical_close_date(rows[-2].ts_event) == date(2024, 9, 30)
    assert _canonical_close_date(rows[-1].ts_event) == date(2024, 10, 1)
    assert calendar.lookups == {
        ("FBS-Demo/EURUSD", date(2024, 9, 30)),
        ("FBS-Demo/EURUSD", date(2024, 10, 1)),
    }
    assert DataQualityFlag.GAP in rows[-1].quality_flags
    assert DataQualityFlag.GAP_CALENDAR_UNKNOWN not in rows[-1].quality_flags
    assert pipeline.summary().calendar_days_missing == ()


@pytest.mark.parametrize(
    ("friday", "closed_hours"),
    [(date(2024, 10, 18), 48), (date(2024, 10, 25), 48), (date(2024, 11, 1), 49)],
)
def test_weekend_gap_uses_close_keys_and_exact_expected_intervals(
    friday: date, closed_hours: int
) -> None:
    saturday, sunday, monday = [friday + timedelta(days=days) for days in (1, 2, 3)]
    close, reopen = _ny(friday), _ny(sunday)
    assert reopen - close == timedelta(hours=closed_hours)
    calendar = RecordingCalendar(
        [_day(friday), _day(saturday, closed=True), _day(sunday, closed=True), _day(monday)]
    )
    pipeline = _pipeline(calendar)
    rows = _run(
        pipeline,
        [close - timedelta(seconds=offset) for offset in (1, 0.8, 0.6)]
        + [reopen + timedelta(milliseconds=200)],
    )

    assert calendar.lookups == {
        ("FBS-Demo/EURUSD", day) for day in (friday, saturday, sunday, monday)
    }
    assert _canonical_close_date(rows[0].ts_event) == friday
    assert _canonical_close_date(rows[-1].ts_event) == monday
    assert DataQualityFlag.GAP not in rows[-1].quality_flags
    assert DataQualityFlag.GAP_CALENDAR_UNKNOWN not in rows[-1].quality_flags
    assert pipeline.summary().calendar_days_checked == 4
    assert pipeline.summary().calendar_days_missing == ()
    assert all(row.eligible_for_bars for row in rows)


def test_missing_weekend_calendar_days_remain_unknown_and_are_all_reported() -> None:
    friday, monday = date(2024, 10, 25), date(2024, 10, 28)
    close, reopen = _ny(friday), _ny(date(2024, 10, 27))
    calendar = RecordingCalendar([_day(friday), _day(monday)])
    pipeline = _pipeline(calendar)
    rows = _run(
        pipeline,
        [close - timedelta(seconds=offset) for offset in (1, 0.8, 0.6)] + [reopen],
    )

    assert DataQualityFlag.GAP_CALENDAR_UNKNOWN in rows[-1].quality_flags
    assert DataQualityFlag.GAP not in rows[-1].quality_flags
    assert pipeline.summary().calendar_days_missing == ("2024-10-26", "2024-10-27")
    assert pipeline.summary().calendar_status == QualityCheckStatus.INDETERMINATE


def test_generic_boundary_uses_explicit_calendar_label_zone() -> None:
    start = datetime(2024, 9, 30, 23, 30, tzinfo=UTC)
    end = start + timedelta(hours=1)
    calendar = RecordingCalendar([_day(date(2024, 10, 1))])
    pipeline = TickQualityPipeline(
        instrument="EURUSD",
        source="FBS-Demo",
        session_boundary=lambda _instant: (start, end),
        calendar=calendar,
        calendar_label_zone=UTC,
        known_at=_KNOWN_AT,
    )
    _run(pipeline, [start])
    assert calendar.lookups == {("FBS-Demo/EURUSD", date(2024, 10, 1))}
    assert pipeline.summary().calendar_days_missing == ()


def test_calendar_label_zone_must_be_a_timezone() -> None:
    with pytest.raises(TypeError, match="calendar_label_zone must be a timezone"):
        TickQualityPipeline(
            instrument="EURUSD",
            source="FBS-Demo",
            session_boundary=fx_session_bounds,
            calendar_label_zone="America/New_York",  # type: ignore[arg-type]
        )


def test_closed_calendar_never_redefines_market_boundary() -> None:
    instant = _ny(date(2024, 10, 20))
    rows_by_status: list[tuple[CleanTickRecord, ...]] = []
    for closed in (False, True):
        pipeline = _pipeline(RecordingCalendar([_day(date(2024, 10, 21), closed=closed)]))
        rows_by_status.append(_run(pipeline, [instant]))
    assert rows_by_status[0] == rows_by_status[1]
    assert rows_by_status[0][0].eligible_for_bars


def test_out_of_session_tick_stays_ineligible_without_fabricating_session() -> None:
    saturday = date(2024, 10, 26)
    calendar = RecordingCalendar([_day(saturday, closed=True)])
    pipeline = _pipeline(calendar)
    rows = _run(pipeline, [_ny(saturday, 12)])
    assert DataQualityFlag.OUT_OF_SESSION in rows[0].quality_flags
    assert not rows[0].eligible_for_bars
    assert calendar.lookups == {("FBS-Demo/EURUSD", saturday)}
