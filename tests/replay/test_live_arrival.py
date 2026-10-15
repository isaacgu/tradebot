"""Accelerated-`WallClock` arrival tests, required by ADR-0006.

The replay code-parity harness cannot cover this ground: its driver advances
`SimClock` to an event's availability key *before* publishing, so ordering and
admission come free and the deferral queue is never exercised. Live is the opposite
— time moves on its own and events arrive whenever the network delivers them.

These tests drive a real `WallClock` from an injected, deterministic time source so
minutes pass instantly. Both the wall and monotonic sources advance in lockstep, so
the clock's own discontinuity guard stays satisfied while the test skips ahead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradebot.core.bus import EventBus
from tradebot.core.clock import WallClock
from tradebot.core.errors import LookAheadError
from tradebot.core.types import QualityFlag, Tick
from tradebot.data.deferral import DeferralQueue
from tradebot.data.normalize import TickObservation, normalize_tick

START = datetime(2025, 3, 17, 12, tzinfo=UTC)


class AcceleratedTime:
    """A wall/monotonic pair that advances together, so the guard never trips."""

    def __init__(self, start: datetime) -> None:
        self._start = start
        self._elapsed = 0.0

    def advance(self, seconds: float) -> None:
        self._elapsed += seconds

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._elapsed)

    def monotonic(self) -> float:
        return 1000.0 + self._elapsed


def _clock(time_source: AcceleratedTime) -> WallClock:
    return WallClock(now_source=time_source.now, monotonic_source=time_source.monotonic)


def _observation(offset: float, *, recv_offset: float | None = None) -> TickObservation:
    """A tick whose market time is START+offset, received at START+recv_offset."""
    ts_event = START + timedelta(seconds=offset)
    ts_recv = START + timedelta(seconds=offset if recv_offset is None else recv_offset)
    return TickObservation(
        instrument="GBP_USD",
        ts_event=ts_event,
        ts_recv=ts_recv,
        bid=Decimal("1.29000"),
        ask=Decimal("1.29010"),
    )


def test_the_bus_refuses_an_event_that_has_not_arrived_yet() -> None:
    """Publishing straight from a live feed, with no deferral, is a look-ahead."""
    time_source = AcceleratedTime(START)
    bus = EventBus(_clock(time_source))
    delivered: list[Tick] = []
    bus.subscribe(Tick, delivered.append)

    # The adapter learned of this tick early — its key is 30 s in the future.
    premature = normalize_tick(_observation(30))

    with pytest.raises(LookAheadError):
        bus.publish(premature)

    assert delivered == []


def test_deferral_holds_an_early_arrival_then_admits_it_on_time() -> None:
    """The queue is what makes live admission work; the bus can only refuse."""
    time_source = AcceleratedTime(START)
    clock = _clock(time_source)
    bus = EventBus(clock)
    queue = DeferralQueue(clock)
    delivered: list[Tick] = []
    bus.subscribe(Tick, delivered.append)

    queue.submit(normalize_tick(_observation(30)))

    for event in queue.release():
        bus.publish(event)
    assert delivered == [], "held, and never offered to the bus"

    time_source.advance(30)
    for event in queue.release():
        bus.publish(event)

    assert len(delivered) == 1
    assert delivered[0].available_at == START + timedelta(seconds=30)


def test_a_live_tail_is_delivered_in_source_order_as_time_passes() -> None:
    time_source = AcceleratedTime(START)
    clock = _clock(time_source)
    bus = EventBus(clock)
    queue = DeferralQueue(clock)
    delivered: list[Tick] = []
    bus.subscribe(Tick, delivered.append)

    # A burst arrives at once, keyed one second apart.
    for offset in range(5):
        queue.submit(normalize_tick(_observation(offset)))

    # Only the tick keyed at START is observable at START.
    for event in queue.release():
        bus.publish(event)
    assert len(delivered) == 1

    for _ in range(4):
        time_source.advance(1)
        for event in queue.release():
            bus.publish(event)

    assert len(delivered) == 5
    keys = [tick.available_at for tick in delivered]
    assert keys == sorted(keys), "delivery order matches source order"
    assert len(queue) == 0


def test_nothing_is_admitted_ahead_of_the_clock_across_a_whole_run() -> None:
    """The invariant that matters: no event ever reaches a handler early.

    Asserted at the handler rather than the queue, so it holds for the composition
    of normalizer, queue and bus rather than for any one of them.
    """
    time_source = AcceleratedTime(START)
    clock = _clock(time_source)
    bus = EventBus(clock)
    queue = DeferralQueue(clock)
    violations: list[str] = []

    def check_on_delivery(tick: Tick) -> None:
        # A fresh reading, not the one the queue used, so a stale comparison
        # cannot hide a violation.
        if tick.available_at > time_source.now():
            violations.append(tick.available_at.isoformat())

    bus.subscribe(Tick, check_on_delivery)

    for offset in (0, 2, 2, 5, 11):
        queue.submit(normalize_tick(_observation(offset)))

    for _ in range(15):
        for event in queue.release():
            bus.publish(event)
        time_source.advance(1)

    assert violations == []
    assert len(queue) == 0, "everything was eventually delivered"


def test_a_backfilled_arrival_is_admitted_at_once_but_flagged_stale() -> None:
    """ADR-0006: backfill is exempt from an arrival bound, never admitted as fresh.

    A reconnect fetch returns two-hour-old market data stamped with a current
    receipt time. A naive `ts_recv - ts_event` bound would halt the trader on its
    own documented recovery path; the key correctly says we can act now, and the
    BACKFILLED flag is what stops it counting as a fresh observation.
    """
    time_source = AcceleratedTime(START)
    clock = _clock(time_source)
    bus = EventBus(clock)
    queue = DeferralQueue(clock)
    delivered: list[Tick] = []
    bus.subscribe(Tick, delivered.append)

    recovered = normalize_tick(
        TickObservation(
            instrument="GBP_USD",
            ts_event=START - timedelta(hours=2),
            ts_recv=START,
            bid=Decimal("1.29000"),
            ask=Decimal("1.29010"),
            backfilled=True,
        )
    )
    queue.submit(recovered)
    for event in queue.release():
        bus.publish(event)

    assert len(delivered) == 1
    assert delivered[0].available_at == START
    assert QualityFlag.BACKFILLED in delivered[0].quality_flags
    assert delivered[0].ts_event == START - timedelta(hours=2), "market time is untouched"


def test_a_venue_stamp_ahead_of_local_receipt_defers_to_the_venue_stamp() -> None:
    """Under skew the key follows the venue, so admission is the later of the two."""
    time_source = AcceleratedTime(START)
    clock = _clock(time_source)
    bus = EventBus(clock)
    queue = DeferralQueue(clock)
    delivered: list[Tick] = []
    bus.subscribe(Tick, delivered.append)

    # Venue says 10 s from now; we received it now. Skew, not corruption.
    skewed = normalize_tick(_observation(10, recv_offset=0))
    assert QualityFlag.CLOCK_SKEW in skewed.quality_flags
    assert skewed.ts_recv == START

    queue.submit(skewed)
    for event in queue.release():
        bus.publish(event)
    assert delivered == [], "not admitted while the venue stamp is still future"

    time_source.advance(10)
    for event in queue.release():
        bus.publish(event)

    assert len(delivered) == 1
