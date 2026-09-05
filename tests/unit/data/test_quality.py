from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tradebot.core.time_rules import fx_session_bounds as classify_fx_session
from tradebot.data.acquisition_probe import fx_session_bounds as acquisition_session_bounds
from tradebot.data.bars import FixedInterval
from tradebot.data.calendar import ExpectedLiquidityCalendar, LiquidityDay, LiquidityStatus
from tradebot.data.quality import (
    CleanTickRecord,
    DataQualityFlag,
    QualityCheckStatus,
    QualityInput,
    QualityThresholds,
    TickQualityPipeline,
    _RollingMedian,
)

_START = datetime(2024, 10, 21, 10, tzinfo=UTC)


def _thresholds(**changes: object) -> QualityThresholds:
    values: dict[str, object] = {
        "spread_multiplier": Decimal("10"),
        "price_sigma": Decimal("20"),
        "price_reversion_ticks": 2,
        "gap_threshold": timedelta(seconds=2),
        "fast_market_median": timedelta(seconds=1),
        "rolling_horizon": timedelta(hours=1),
        "minimum_history": 2,
    }
    values.update(changes)
    return QualityThresholds(**values)  # type: ignore[arg-type]


def _input(
    seq: int,
    second: float,
    *,
    bid: str = "1.1000",
    ask: str = "1.1002",
    raw_identity: tuple[str, ...] | None = None,
) -> QualityInput:
    return QualityInput(
        instrument="GBPUSD",
        source="FBS-Demo",
        seq=seq,
        ts_event=_START + timedelta(seconds=second),
        bid=Decimal(bid),
        ask=Decimal(ask),
        raw_identity=raw_identity or (str(second), bid, ask),
    )


def _drain(
    pipeline: TickQualityPipeline, items: Iterable[QualityInput]
) -> tuple[CleanTickRecord, ...]:
    output: list[CleanTickRecord] = []
    for item in items:
        output.extend(pipeline.process(item))
    output.extend(pipeline.finish())
    return tuple(output)


def _pipeline(**changes: object) -> TickQualityPipeline:
    values: dict[str, object] = {
        "instrument": "GBPUSD",
        "source": "FBS-Demo",
        "session_boundary": FixedInterval(timedelta(minutes=1)),
        "thresholds": _thresholds(),
    }
    values.update(changes)
    return TickQualityPipeline(**values)  # type: ignore[arg-type]


def test_missing_receipt_is_imputed_by_the_shared_normalizer() -> None:
    pipeline = _pipeline()
    rows = _drain(pipeline, [_input(0, 0), _input(1, 0.1), _input(2, 0.2)])
    assert len(rows) == 3
    assert rows[0].ts_recv == rows[0].ts_event == rows[0].available_at
    assert "TS_RECV_IMPUTED" in rows[0].quality_flags
    assert DataQualityFlag.QUALITY_WARMUP in rows[0].quality_flags
    assert rows[0].eligible_for_bars
    assert pipeline.summary().calendar_status == QualityCheckStatus.INDETERMINATE


def test_identical_adjacent_source_rows_are_counted_but_never_value_deduplicated() -> None:
    same = ("canonical", "row")
    pipeline = _pipeline()
    rows = _drain(
        pipeline,
        [
            _input(0, 0, raw_identity=same),
            _input(1, 0, raw_identity=same),
            _input(2, 0.1, raw_identity=("another", "row")),
        ],
    )
    assert [row.seq for row in rows] == [0, 1, 2]
    assert pipeline.summary().duplicate_rows == 1


def test_single_source_cross_source_agreement_is_not_evaluable() -> None:
    pipeline = _pipeline()
    _drain(pipeline, [_input(0, 0)])
    assert pipeline.summary().cross_source_status is QualityCheckStatus.NOT_EVALUABLE


def test_locked_crossed_and_nonpositive_quotes_remain_clean_evidence() -> None:
    pipeline = _pipeline()
    rows = _drain(
        pipeline,
        [
            _input(0, 0, bid="1", ask="1"),
            _input(1, 1, bid="2", ask="1"),
            _input(2, 2, bid="-1", ask="1"),
            _input(3, 3, bid="-2", ask="-1"),
        ],
    )
    assert DataQualityFlag.LOCKED_QUOTE in rows[0].quality_flags
    assert DataQualityFlag.CROSSED_QUOTE in rows[1].quality_flags
    assert DataQualityFlag.NONPOSITIVE_BID in rows[2].quality_flags
    assert DataQualityFlag.NONPOSITIVE_ASK in rows[3].quality_flags
    assert DataQualityFlag.CROSSED_QUOTE not in rows[3].quality_flags
    assert all(not row.eligible_for_bars for row in rows)
    assert pipeline.summary().bar_excluded_rows == 4


def test_out_of_session_and_regressed_rows_are_retained_but_not_bar_eligible() -> None:
    def first_second_only(moment: datetime) -> tuple[datetime, datetime] | None:
        if moment < _START + timedelta(seconds=1):
            return _START, _START + timedelta(seconds=1)
        return None

    pipeline = _pipeline(session_boundary=first_second_only)
    rows = _drain(
        pipeline,
        [
            _input(0, 0.5),
            _input(1, 2),
            QualityInput(
                instrument="GBPUSD",
                source="FBS-Demo",
                seq=2,
                ts_event=_START + timedelta(seconds=0.25),
                bid=Decimal("1.1"),
                ask=Decimal("1.2"),
                raw_identity=("regressed",),
            ),
        ],
    )
    assert DataQualityFlag.OUT_OF_SESSION in rows[1].quality_flags
    assert DataQualityFlag.TIME_REGRESSION in rows[2].quality_flags
    assert not rows[1].eligible_for_bars
    assert not rows[2].eligible_for_bars


def test_spread_outlier_uses_prior_exact_rolling_median() -> None:
    pipeline = _pipeline(thresholds=_thresholds(spread_multiplier=Decimal("3")))
    rows = _drain(
        pipeline,
        [
            _input(0, 0, bid="1.0000", ask="1.0002"),
            _input(1, 0.1, bid="1.0001", ask="1.0003"),
            _input(2, 0.2, bid="1.0002", ask="1.0004"),
            _input(3, 0.3, bid="1.0000", ask="1.0100"),
        ],
    )
    assert DataQualityFlag.SPREAD_OUTLIER in rows[-1].quality_flags
    assert rows[-1].eligible_for_bars


def _mid_input(seq: int, second: float, mid: str) -> QualityInput:
    value = Decimal(mid)
    half_spread = Decimal("0.05")
    return _input(
        seq,
        second,
        bid=str(value - half_spread),
        ask=str(value + half_spread),
    )


def test_future_confirmed_jump_is_retrospective_and_never_rewrites_bar_eligibility() -> None:
    thresholds = _thresholds(price_sigma=Decimal("2"), price_reversion_ticks=2)
    reverting = _pipeline(thresholds=thresholds)
    rows = _drain(
        reverting,
        [
            _mid_input(0, 0, "100"),
            _mid_input(1, 0.1, "101"),
            _mid_input(2, 0.2, "100"),
            _mid_input(3, 0.3, "101"),
            _mid_input(4, 0.4, "150"),
            _mid_input(5, 0.5, "100.5"),
            _mid_input(6, 0.6, "100.6"),
        ],
    )
    jump = next(row for row in rows if row.seq == 4)
    assert DataQualityFlag.PRICE_OUTLIER not in jump.quality_flags
    assert DataQualityFlag.PRICE_OUTLIER in jump.retrospective_flags
    assert jump.eligible_for_bars
    assert reverting.summary().retrospective_flag_counts == (("PRICE_OUTLIER", 1),)

    sustained = _pipeline(thresholds=thresholds)
    sustained_rows = _drain(
        sustained,
        [
            _mid_input(0, 0, "100"),
            _mid_input(1, 0.1, "101"),
            _mid_input(2, 0.2, "100"),
            _mid_input(3, 0.3, "101"),
            _mid_input(4, 0.4, "150"),
            _mid_input(5, 0.5, "151"),
            _mid_input(6, 0.6, "150"),
        ],
    )
    sustained_jump = next(row for row in sustained_rows if row.seq == 4)
    assert DataQualityFlag.PRICE_OUTLIER not in sustained_jump.quality_flags
    assert DataQualityFlag.PRICE_OUTLIER not in sustained_jump.retrospective_flags
    assert sustained_jump.eligible_for_bars


def _calendar(status: LiquidityStatus) -> ExpectedLiquidityCalendar:
    intervals = () if status == LiquidityStatus.CLOSED else ((_START, _START + timedelta(hours=1)),)
    return ExpectedLiquidityCalendar(
        [
            LiquidityDay(
                instrument="FBS-Demo/GBPUSD",
                session_date=date(2024, 10, 21),
                status=status,
                source="broker-hours",
                source_citation="test fixture",
                effective_at=_START - timedelta(days=1),
                available_at=_START - timedelta(days=1),
                valid_until=_START + timedelta(days=1),
                expected_intervals=intervals,
            )
        ]
    )


def test_gap_requires_point_in_time_calendar_before_it_can_fail() -> None:
    inputs = [_input(0, 0), _input(1, 0.2), _input(2, 0.4), _input(3, 5)]
    missing = _pipeline()
    missing_rows = _drain(missing, inputs)
    assert DataQualityFlag.GAP_CALENDAR_UNKNOWN in missing_rows[-1].quality_flags
    assert missing.summary().calendar_status == QualityCheckStatus.INDETERMINATE

    full = _pipeline(
        calendar=_calendar(LiquidityStatus.FULL),
        known_at=_START,
        calendar_instrument="FBS-Demo/GBPUSD",
    )
    full_rows = _drain(full, inputs)
    assert DataQualityFlag.GAP in full_rows[-1].quality_flags
    assert full.summary().calendar_status == QualityCheckStatus.PASSED
    assert full.summary().quality_status == QualityCheckStatus.FAILED

    closed = _pipeline(
        calendar=_calendar(LiquidityStatus.CLOSED),
        known_at=_START,
        calendar_instrument="FBS-Demo/GBPUSD",
    )
    closed_rows = _drain(closed, inputs)
    assert DataQualityFlag.GAP not in closed_rows[-1].quality_flags
    assert DataQualityFlag.GAP_CALENDAR_UNKNOWN not in closed_rows[-1].quality_flags


def test_calendar_uses_session_key_when_expected_interval_crosses_utc_midnight() -> None:
    session_date = date(2024, 10, 21)
    start, end = acquisition_session_bounds(session_date)
    calendar = ExpectedLiquidityCalendar(
        [
            LiquidityDay(
                instrument="FBS-Demo/GBPUSD",
                session_date=session_date,
                status=LiquidityStatus.FULL,
                source="broker-hours",
                source_citation="test fixture",
                effective_at=start - timedelta(days=1),
                available_at=start - timedelta(days=1),
                valid_until=end + timedelta(days=1),
                expected_intervals=((start, end),),
            )
        ]
    )
    pipeline = _pipeline(
        session_boundary=classify_fx_session,
        calendar=calendar,
        known_at=start,
        calendar_instrument="FBS-Demo/GBPUSD",
    )

    def at(seq: int, offset: timedelta) -> QualityInput:
        return QualityInput(
            instrument="GBPUSD",
            source="FBS-Demo",
            seq=seq,
            ts_event=start + offset,
            bid=Decimal("1.1"),
            ask=Decimal("1.2"),
            raw_identity=(str(seq),),
        )

    rows = _drain(
        pipeline,
        [
            at(0, timedelta(0)),
            at(1, timedelta(milliseconds=200)),
            at(2, timedelta(milliseconds=400)),
            at(3, timedelta(hours=4)),
        ],
    )
    assert DataQualityFlag.GAP in rows[-1].quality_flags
    assert pipeline.summary().calendar_days_checked == 1
    assert pipeline.summary().calendar_days_missing == ()


def test_exact_rolling_median_compacts_expired_heap_entries() -> None:
    rolling = _RollingMedian(timedelta(seconds=1))
    for index in range(3_000):
        moment = _START + timedelta(seconds=index * 2)
        rolling.trim(moment)
        rolling.append(moment, Decimal(index))
    assert len(rolling) == 1
    assert rolling.median() == Decimal(2_999)
    assert len(rolling._lower) + len(rolling._upper) <= 1_026
