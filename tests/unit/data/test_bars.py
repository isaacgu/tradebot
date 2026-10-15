from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradebot.core.clock import SimClock
from tradebot.core.errors import LateTickError
from tradebot.core.time_rules import fx_session_bounds
from tradebot.core.types import Bar, QualityFlag, Tick, VolumeKind
from tradebot.data.bars import BarBuilder, FixedInterval
from tradebot.data.normalize import TickObservation, normalize_tick

START = datetime(2025, 3, 17, 12, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def _tick(
    second: float,
    *,
    bid: str = "1.29000",
    spread: str = "0.00010",
    recv_second: float | None = None,
    flags: tuple[str, ...] = (),
) -> Tick:
    ts_event = START + timedelta(seconds=second)
    ts_recv = START + timedelta(seconds=second if recv_second is None else recv_second)
    return normalize_tick(
        TickObservation(
            instrument="GBP_USD",
            ts_event=ts_event,
            ts_recv=ts_recv,
            bid=Decimal(bid),
            ask=Decimal(bid) + Decimal(spread),
            source_flags=flags,
        )
    )


def _builder(**changes: object) -> BarBuilder:
    fields: dict[str, object] = {
        "instrument": "GBP_USD",
        "boundary": FixedInterval(MINUTE),
        "clock": SimClock(START),
    }
    return BarBuilder(**(fields | changes))  # type: ignore[arg-type]


def test_a_bar_is_emitted_only_once_its_interval_has_ended() -> None:
    builder = _builder()

    assert builder.add(_tick(0)) == ()
    assert builder.add(_tick(30)) == (), "still forming"
    assert builder.forming_close == START + MINUTE

    closed = builder.add(_tick(60))

    assert len(closed) == 1
    assert closed[0].ts_open == START
    assert closed[0].ts_event == closed[0].ts_close == START + MINUTE


def test_ohlc_is_taken_from_tick_mids_in_arrival_order() -> None:
    builder = _builder()
    for second, bid in ((0, "1.29000"), (10, "1.29100"), (20, "1.28900"), (30, "1.29050")):
        builder.add(_tick(second, bid=bid))

    bar = builder.flush(START + MINUTE)[0]

    assert bar.open == Decimal("1.29005")
    assert bar.high == Decimal("1.29105")
    assert bar.low == Decimal("1.28905")
    assert bar.close == Decimal("1.29055")


def test_volume_is_tick_count_and_labelled_as_such() -> None:
    """SPEC 4.3: FX volume is a tick count and must never read as traded volume."""
    builder = _builder()
    for second in range(4):
        builder.add(_tick(second))

    bar = builder.flush(START + MINUTE)[0]

    assert bar.n_ticks == 4
    assert bar.volume == 4
    assert bar.volume_kind is VolumeKind.TICK_COUNT


def test_spread_mean_is_computed_from_ticks() -> None:
    """A broker's own bar 'spread' field may be the MINIMUM, which understates cost."""
    builder = _builder()
    builder.add(_tick(0, spread="0.00010"))
    builder.add(_tick(10, spread="0.00030"))
    builder.add(_tick(20, spread="0.00020"))

    bar = builder.flush(START + MINUTE)[0]

    assert bar.spread_mean == Decimal("0.00020")


def test_quality_flags_union_the_constituent_ticks() -> None:
    """Aggregation must not launder tick-level problems into a clean-looking bar."""
    builder = _builder()
    builder.add(_tick(0, flags=("VENDOR_SUSPECT",)))
    builder.add(_tick(10, recv_second=5))  # venue ahead of local -> CLOCK_SKEW
    builder.add(_tick(20))

    bar = builder.flush(START + MINUTE)[0]

    assert QualityFlag.CLOCK_SKEW in bar.quality_flags
    assert "VENDOR_SUSPECT" in bar.quality_flags
    assert bar.quality_flags == tuple(sorted(set(bar.quality_flags))), "deterministic order"


def test_seal_latency_pushes_receipt_past_the_close_instant() -> None:
    """In live a bar is never observable at its close; backtest must agree (NN-1)."""
    builder = _builder(seal_latency=timedelta(milliseconds=250))
    builder.add(_tick(0))

    bar = builder.flush(START + MINUTE)[0]

    assert bar.ts_event == START + MINUTE
    assert bar.ts_recv == START + MINUTE + timedelta(milliseconds=250)
    assert bar.available_at == bar.ts_recv


def test_receipt_time_never_precedes_the_latest_constituent_tick() -> None:
    """A late-received tick inside the bar drags the bar's own receipt time out."""
    late_recv = START + timedelta(seconds=90)
    builder = _builder(clock=SimClock(late_recv))
    builder.add(_tick(0, recv_second=90))

    bar = builder.flush(late_recv)[0]

    assert bar.ts_recv >= late_recv


def test_receipt_time_never_precedes_the_emission_instant() -> None:
    """The third arm of the maximum: a bar sealed late is not backdated."""
    emitted_at = START + timedelta(minutes=5)
    builder = _builder(clock=SimClock(emitted_at))
    builder.add(_tick(0))

    bar = builder.flush(emitted_at)[0]

    assert bar.ts_recv == emitted_at


def test_a_bar_with_no_ticks_is_not_emitted() -> None:
    """SPEC 4.3 default: an empty interval produces no bar at all."""
    builder = _builder()
    builder.add(_tick(0))

    closed = builder.add(_tick(180))

    assert len(closed) == 1, "only the interval that actually had ticks"
    assert closed[0].ts_open == START
    assert builder.forming_close == START + timedelta(minutes=4)


def test_flush_before_the_interval_ends_emits_nothing() -> None:
    """Bars are closed when delivered; a forming bar is never handed out."""
    builder = _builder()
    builder.add(_tick(0))

    assert builder.flush(START + timedelta(seconds=59)) == ()
    assert builder.flush(START + MINUTE) != ()


def test_flush_defaults_to_the_injected_clock() -> None:
    clock = SimClock(START)
    builder = _builder(clock=clock)
    builder.add(_tick(0))

    assert builder.flush() == ()

    clock.advance_to(START + MINUTE)
    assert len(builder.flush()) == 1


def test_flushing_an_empty_builder_is_a_no_op() -> None:
    assert _builder().flush(START + timedelta(hours=1)) == ()


def test_a_tick_for_a_sealed_interval_is_refused_not_absorbed() -> None:
    """The bar cannot be reopened without a look-ahead, nor the tick dropped silently."""
    builder = _builder()
    builder.add(_tick(0))
    builder.add(_tick(60))  # seals [12:00, 12:01), opens the next

    with pytest.raises(LateTickError, match="sealed"):
        builder.add(_tick(30))


def test_a_tick_cannot_reopen_an_interval_sealed_by_flush() -> None:
    """A timer-sealed bar remains final even when no newer bar is forming."""
    builder = _builder()
    builder.add(_tick(0))
    assert len(builder.flush(START + MINUTE)) == 1
    assert builder.forming_close is None

    with pytest.raises(LateTickError, match="sealed watermark"):
        builder.add(_tick(30))

    assert builder.forming_close is None


def test_a_tick_for_another_instrument_is_refused() -> None:
    builder = _builder()
    other = normalize_tick(
        TickObservation(
            instrument="EUR_USD",
            ts_event=START,
            ts_recv=START,
            bid=Decimal("1.08000"),
            ask=Decimal("1.08010"),
        )
    )

    with pytest.raises(ValueError, match="EUR_USD"):
        builder.add(other)


def test_a_negative_seal_latency_is_refused() -> None:
    with pytest.raises(ValueError, match="seal_latency"):
        _builder(seal_latency=timedelta(seconds=-1))


@pytest.mark.parametrize(
    ("interval", "second", "expected_open"),
    [
        (timedelta(minutes=1), 90, datetime(2025, 3, 17, 12, 1, tzinfo=UTC)),
        (timedelta(minutes=5), 400, datetime(2025, 3, 17, 12, 5, tzinfo=UTC)),
        (timedelta(hours=1), 30, datetime(2025, 3, 17, 12, tzinfo=UTC)),
        (timedelta(hours=4), 30, datetime(2025, 3, 17, 12, tzinfo=UTC)),
    ],
)
def test_fixed_intervals_floor_to_natural_clock_boundaries(
    interval: timedelta, second: int, expected_open: datetime
) -> None:
    start, end = FixedInterval(interval)(START + timedelta(seconds=second))

    assert start == expected_open
    assert end == start + interval


def test_a_nonpositive_interval_is_refused() -> None:
    with pytest.raises(ValueError, match="interval"):
        FixedInterval(timedelta(0))


def test_the_boundary_is_half_open_at_the_interval_edge() -> None:
    """A tick exactly at the edge opens the NEW bar, matching SPEC 3.4's rule."""
    builder = _builder()
    builder.add(_tick(0))

    closed = builder.add(_tick(60))

    assert closed[0].ts_event == START + MINUTE
    assert builder.forming_close == START + timedelta(minutes=2)


def test_the_same_builder_drives_a_session_boundary() -> None:
    """SPEC 4.3: daily FX bars use the 17:00 NY session, same code path."""
    session_start = datetime(2025, 3, 17, 21, tzinfo=UTC)  # 17:00 America/New_York
    clock = SimClock(session_start)
    builder = BarBuilder(instrument="GBP_USD", boundary=fx_session_bounds, clock=clock)

    inside = normalize_tick(
        TickObservation(
            instrument="GBP_USD",
            ts_event=session_start + timedelta(hours=3),
            ts_recv=session_start + timedelta(hours=3),
            bid=Decimal("1.29000"),
            ask=Decimal("1.29010"),
        )
    )
    builder.add(inside)
    next_close = datetime(2025, 3, 18, 21, tzinfo=UTC)

    assert builder.forming_close == next_close

    clock.advance_to(next_close)
    bar = builder.flush()[0]

    assert bar.ts_open == session_start
    assert bar.ts_event == next_close


def test_a_tick_outside_any_session_is_refused() -> None:
    saturday = datetime(2025, 1, 11, 12, tzinfo=UTC)
    builder = BarBuilder(instrument="GBP_USD", boundary=fx_session_bounds, clock=SimClock(saturday))
    weekend_tick = normalize_tick(
        TickObservation(
            instrument="GBP_USD",
            ts_event=saturday,
            ts_recv=saturday,
            bid=Decimal("1.29000"),
            ask=Decimal("1.29010"),
        )
    )

    with pytest.raises(LateTickError, match="session"):
        builder.add(weekend_tick)


def test_sealed_bars_satisfy_the_platform_event_invariants() -> None:
    """Whatever the builder emits must be admissible: ts_recv >= ts_event, ts_open <."""
    builder = _builder(seal_latency=timedelta(milliseconds=100))
    for second in (0, 15, 45, 61, 75):
        builder.add(_tick(second))
    bars: list[Bar] = list(builder.flush(START + timedelta(minutes=2)))

    assert bars
    for bar in bars:
        assert bar.ts_open < bar.ts_event
        assert bar.ts_recv >= bar.ts_event
        assert bar.available_at == bar.ts_recv
