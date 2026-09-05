"""The P1 feed chain end to end: normalize -> sequence -> defer -> bus -> bars.

Each piece has its own unit tests. This asserts they compose, on a real `WallClock`
driven by an injected time source so a trading hour passes in milliseconds. Wiring
defects — a stamp that means one thing in one module and another next door — only
show up here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradebot.core.bus import EventBus
from tradebot.core.clock import WallClock
from tradebot.core.errors import LookAheadError
from tradebot.core.types import Bar, QualityFlag, Tick
from tradebot.data.bars import BarBuilder, FixedInterval
from tradebot.data.deferral import DeferralQueue
from tradebot.data.ingest import TickIngester
from tradebot.data.normalize import TickObservation

START = datetime(2025, 3, 17, 12, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


class _AcceleratedTime:
    """Wall and monotonic advancing together, so the clock's guard stays satisfied."""

    def __init__(self, start: datetime) -> None:
        self._start = start
        self._elapsed = 0.0

    def advance(self, seconds: float) -> None:
        self._elapsed += seconds

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._elapsed)

    def monotonic(self) -> float:
        return 1000.0 + self._elapsed


def _observation(second: int, *, bid: str, recv_second: int | None = None) -> TickObservation:
    return TickObservation(
        instrument="GBP_USD",
        ts_event=START + timedelta(seconds=second),
        ts_recv=START + timedelta(seconds=second if recv_second is None else recv_second),
        bid=Decimal(bid),
        ask=Decimal(bid) + Decimal("0.00010"),
    )


def test_ticks_become_bars_without_any_look_ahead() -> None:
    time_source = _AcceleratedTime(START)
    clock = WallClock(now_source=time_source.now, monotonic_source=time_source.monotonic)
    bus = EventBus(clock)
    tick_queue = DeferralQueue(clock)
    bar_queue = DeferralQueue(clock)
    ingester = TickIngester(source="probe", instrument="GBP_USD", clock=clock)
    builder = BarBuilder(
        instrument="GBP_USD",
        boundary=FixedInterval(MINUTE),
        clock=clock,
        seal_latency=timedelta(milliseconds=250),
    )

    ticks: list[Tick] = []
    bars: list[Bar] = []
    violations: list[str] = []

    def on_tick(tick: Tick) -> None:
        ticks.append(tick)
        for bar in builder.add(tick):
            bar_queue.submit(bar)

    def guard(event: Tick | Bar) -> None:
        # Checked against a freshly read source, not the reading any component used.
        if event.available_at > time_source.now():
            violations.append(f"{type(event).__name__}@{event.available_at.isoformat()}")

    bus.subscribe(Tick, guard)
    bus.subscribe(Tick, on_tick)
    bus.subscribe(Bar, guard)
    bus.subscribe(Bar, bars.append)

    # One fetch covering two minutes, sequenced by the raw layer.
    prices = ["1.29000", "1.29100", "1.28900", "1.29050", "1.29200", "1.29150"]
    seconds = [0, 20, 40, 65, 80, 100]
    result = ingester.ingest(
        [_observation(second, bid=bid) for second, bid in zip(seconds, prices, strict=True)],
        run_id="run-1",
    )
    assert [row.seq for row in result.appended] == [0, 1, 2, 3, 4, 5]

    for row in result.appended:
        tick_queue.submit(row.tick)

    # Walk the clock forward. Bars go through their own queue, because a bar sealed
    # at its close instant is not observable until close + seal_latency.
    for _ in range(130):
        for event in tick_queue.release():
            bus.publish(event)
        for bar in builder.flush():
            bar_queue.submit(bar)
        for event in bar_queue.release():
            bus.publish(event)
        time_source.advance(1)

    assert violations == []
    assert len(ticks) == 6
    assert len(bars) == 2, "two complete minutes"

    first, second = bars
    assert first.ts_open == START
    assert first.ts_event == START + MINUTE
    assert first.n_ticks == 3
    assert first.open == Decimal("1.29005")
    assert first.close == Decimal("1.28905")
    assert second.ts_open == START + MINUTE
    assert second.n_ticks == 3

    for bar in bars:
        assert bar.available_at == bar.ts_recv
        assert bar.ts_recv >= bar.ts_event + timedelta(milliseconds=250)


def test_a_sealed_bar_is_not_yet_observable_under_seal_latency() -> None:
    """The first arm of the seal maximum has teeth: a fresh bar needs deferring.

    Publishing a sealed bar straight to the bus is a look-ahead. This is why bars
    take a deferral queue of their own rather than being handed over on emission.
    """
    time_source = _AcceleratedTime(START)
    clock = WallClock(now_source=time_source.now, monotonic_source=time_source.monotonic)
    bus = EventBus(clock)
    builder = BarBuilder(
        instrument="GBP_USD",
        boundary=FixedInterval(MINUTE),
        clock=clock,
        seal_latency=timedelta(milliseconds=250),
    )
    delivered: list[Bar] = []
    bus.subscribe(Bar, delivered.append)

    ingester = TickIngester(source="probe", instrument="GBP_USD", clock=clock)
    for row in ingester.ingest([_observation(0, bid="1.29000")], run_id="run-1").appended:
        builder.add(row.tick)

    time_source.advance(60)
    sealed = builder.flush()[0]

    assert sealed.available_at > time_source.now()
    with pytest.raises(LookAheadError, match="ts_recv"):
        bus.publish(sealed)
    assert delivered == []

    time_source.advance(1)
    bus.publish(sealed)

    assert delivered == [sealed], "observable once the seal latency has elapsed"


def test_tick_quality_flags_survive_aggregation_into_the_bar() -> None:
    """A skewed tick must not become a clean-looking bar."""
    time_source = _AcceleratedTime(START)
    clock = WallClock(now_source=time_source.now, monotonic_source=time_source.monotonic)
    bus = EventBus(clock)
    queue = DeferralQueue(clock)
    builder = BarBuilder(instrument="GBP_USD", boundary=FixedInterval(MINUTE), clock=clock)
    ingester = TickIngester(source="probe", instrument="GBP_USD", clock=clock)

    bars: list[Bar] = []
    bus.subscribe(Bar, bars.append)

    def fold(tick: Tick) -> None:
        for bar in builder.add(tick):
            bus.publish(bar)

    bus.subscribe(Tick, fold)

    # The middle tick carries a venue stamp ahead of local receipt.
    result = ingester.ingest(
        [
            _observation(0, bid="1.29000"),
            _observation(30, bid="1.29100", recv_second=10),
            _observation(70, bid="1.29050"),
        ],
        run_id="run-1",
    )
    for row in result.appended:
        queue.submit(row.tick)

    for _ in range(90):
        for event in queue.release():
            bus.publish(event)
        time_source.advance(1)

    assert len(bars) == 1
    assert QualityFlag.CLOCK_SKEW in bars[0].quality_flags


def test_a_re_fetch_does_not_duplicate_bars() -> None:
    """Idempotent ingest means replaying a range changes nothing downstream."""
    time_source = _AcceleratedTime(START)
    clock = WallClock(now_source=time_source.now, monotonic_source=time_source.monotonic)
    ingester = TickIngester(source="probe", instrument="GBP_USD", clock=clock)
    batch = [_observation(second, bid="1.29000") for second in (0, 20, 40)]

    ingester.ingest(batch, run_id="run-1")
    second_pass = ingester.ingest([*batch, _observation(70, bid="1.29100")], run_id="run-2")

    assert second_pass.overlap == 3
    assert len(second_pass.appended) == 1, "only the genuinely new tick reaches the builder"
