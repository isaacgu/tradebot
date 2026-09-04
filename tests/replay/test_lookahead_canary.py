from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradebot.core.bus import EventBus
from tradebot.core.clock import SimClock, WallClock
from tradebot.core.errors import EventDispatchError, LookAheadError
from tradebot.core.types import Bar, VolumeKind


def _bar(instrument: str, ts_open: datetime, ts_close: datetime) -> Bar:
    return Bar(
        instrument=instrument,
        ts_open=ts_open,
        ts_event=ts_close,
        ts_recv=ts_close,
        open=Decimal("1.2900"),
        high=Decimal("1.2910"),
        low=Decimal("1.2890"),
        close=Decimal("1.2905"),
        volume=10,
        volume_kind=VolumeKind.TICK_COUNT,
        spread_mean=Decimal("0.0001"),
        n_ticks=10,
    )


def test_replay_canary_cannot_deliver_a_future_closed_bar() -> None:
    """Prove a mis-sequenced replay cannot expose a future close to strategy code."""
    now = datetime(2025, 3, 17, 12, tzinfo=UTC)
    future_close = now + timedelta(minutes=1)
    future_bar = Bar(
        instrument="SYNTH_GBP_USD",
        ts_open=now,
        ts_event=future_close,
        ts_recv=future_close,
        open=Decimal("1.2900"),
        high=Decimal("1.2910"),
        low=Decimal("1.2890"),
        close=Decimal("1.2905"),
        volume=10,
        volume_kind=VolumeKind.TICK_COUNT,
        spread_mean=Decimal("0.0001"),
        n_ticks=10,
    )
    delivered: list[Bar] = []
    bus = EventBus(SimClock(now))
    bus.subscribe(Bar, delivered.append)

    with pytest.raises(LookAheadError, match="ts_event"):
        bus.publish(future_bar)

    assert delivered == []


def test_bus_over_wall_clock_rejects_a_future_closed_bar() -> None:
    """Every other bus test runs on SimClock; the guard must hold on WallClock too."""
    now = datetime(2025, 3, 17, 12, tzinfo=UTC)
    readings: Iterator[datetime] = iter([now, now, now])
    clock = WallClock(now_source=lambda: next(readings), monotonic_source=lambda: 100.0)
    delivered: list[Bar] = []
    bus = EventBus(clock)
    bus.subscribe(Bar, delivered.append)

    with pytest.raises(LookAheadError, match="ts_event"):
        bus.publish(_bar("SYNTH_GBP_USD", now, now + timedelta(minutes=1)))

    assert delivered == []


def test_bus_over_wall_clock_halts_on_a_clock_discontinuity() -> None:
    """A guard failure raised by the clock itself must not deliver a partial dispatch."""
    now = datetime(2025, 3, 17, 12, tzinfo=UTC)
    # Second reading jumps 30 s of wall time against 10 ms of monotonic elapsed time.
    readings: Iterator[datetime] = iter([now, now + timedelta(seconds=30)])
    monotonic: Iterator[float] = iter([100.0, 100.01])
    clock = WallClock(
        now_source=lambda: next(readings),
        monotonic_source=lambda: next(monotonic),
    )
    delivered: list[Bar] = []
    bus = EventBus(clock)
    bus.subscribe(Bar, delivered.append)
    bar = _bar("SYNTH_GBP_USD", now - timedelta(minutes=2), now - timedelta(minutes=1))

    # The first now() sets the baseline and admits the event; the re-validation
    # inside _drain trips the guard, which is inside the try, so the bus halts.
    with pytest.raises(EventDispatchError, match="Bar"):
        bus.publish(bar)

    assert bus.halted
    assert delivered == []
