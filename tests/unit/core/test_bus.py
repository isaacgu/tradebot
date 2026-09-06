from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from tradebot.core.bus import EventBus
from tradebot.core.clock import SimClock
from tradebot.core.errors import BusHaltedError, EventDispatchError, LookAheadError


@dataclass(frozen=True, slots=True)
class ProbeEvent:
    ts_event: datetime
    ts_recv: datetime
    name: str

    @property
    def available_at(self) -> datetime:
        return max(self.ts_event, self.ts_recv)


@dataclass(frozen=True, slots=True)
class ChildEvent:
    ts_event: datetime
    ts_recv: datetime
    name: str

    @property
    def available_at(self) -> datetime:
        return max(self.ts_event, self.ts_recv)


@dataclass(slots=True)
class MutableEvent:
    """Derives its key, so mutating either stamp also moves ``available_at``."""

    ts_event: datetime
    ts_recv: datetime
    name: str

    @property
    def available_at(self) -> datetime:
        return max(self.ts_event, self.ts_recv)


@dataclass(frozen=True, slots=True)
class UnderstatedEvent:
    """A hostile adapter's event: a key that lies about its own stamps.

    The concrete platform types cannot express this — their invariants forbid it —
    but ``Event`` is a structural protocol, so the bus must not trust the key alone.
    """

    ts_event: datetime
    ts_recv: datetime
    available_at: datetime
    name: str


def _start() -> datetime:
    return datetime(2025, 3, 17, 12, tzinfo=UTC)


def test_bus_blocks_future_market_timestamp() -> None:
    now = _start()
    bus = EventBus(SimClock(now))

    with pytest.raises(LookAheadError, match="ts_event"):
        bus.publish(ProbeEvent(now + timedelta(seconds=1), now, "future"))


def test_bus_blocks_future_observation_timestamp() -> None:
    now = _start()
    bus = EventBus(SimClock(now))

    with pytest.raises(LookAheadError, match="ts_recv"):
        bus.publish(ProbeEvent(now - timedelta(hours=1), now + timedelta(seconds=1), "late"))


def test_bus_is_fifo_and_defers_reentrant_events() -> None:
    now = _start()
    bus = EventBus(SimClock(now))
    order: list[str] = []

    def first(event: ProbeEvent) -> None:
        order.append(f"first:{event.name}")
        bus.publish(ChildEvent(now, now, "child"))

    def second(event: ProbeEvent) -> None:
        order.append(f"second:{event.name}")

    def child(event: ChildEvent) -> None:
        order.append(f"child:{event.name}")

    bus.subscribe(ProbeEvent, first)
    bus.subscribe(ProbeEvent, second)
    bus.subscribe(ChildEvent, child)
    bus.publish(ProbeEvent(now, now, "root"))

    assert order == ["first:root", "second:root", "child:child"]


def test_handler_failure_halts_bus_and_propagates() -> None:
    now = _start()
    bus = EventBus(SimClock(now))

    def fail(_event: ProbeEvent) -> None:
        raise RuntimeError("boom")

    bus.subscribe(ProbeEvent, fail)

    with pytest.raises(EventDispatchError, match="ProbeEvent"):
        bus.publish(ProbeEvent(now, now, "root"))

    assert bus.halted
    with pytest.raises(BusHaltedError):
        bus.publish(ProbeEvent(now, now, "again"))


def test_subscription_cancellation_is_idempotent() -> None:
    now = _start()
    bus = EventBus(SimClock(now))
    seen: list[str] = []
    subscription = bus.subscribe(ProbeEvent, lambda event: seen.append(event.name))

    subscription.cancel()
    subscription.cancel()
    assert subscription.cancelled
    bus.publish(ProbeEvent(now, now, "ignored"))
    assert seen == []


def test_processed_observer_failure_halts_and_discards_nested_queue() -> None:
    now = _start()
    seen: list[str] = []

    class FailingObserver:
        def processed(self, event_name: str) -> None:
            raise RuntimeError(f"metrics failed for {event_name}")

        def rejected(self, event_name: str, reason: str) -> None:
            del event_name, reason

        def failed(self, event_name: str, error: BaseException) -> None:
            del event_name, error

    bus = EventBus(SimClock(now), FailingObserver())

    def publish_child(event: ProbeEvent) -> None:
        seen.append(event.name)
        bus.publish(ChildEvent(now, now, "must-be-discarded"))

    bus.subscribe(ProbeEvent, publish_child)
    bus.subscribe(ChildEvent, lambda event: seen.append(event.name))
    with pytest.raises(EventDispatchError, match="dispatch failed"):
        bus.publish(ProbeEvent(now, now, "root"))
    assert bus.halted
    assert seen == ["root"]
    with pytest.raises(BusHaltedError):
        bus.publish(ProbeEvent(now, now, "later"))


def test_bus_revalidates_mutable_nested_event_before_delivery() -> None:
    now = _start()
    bus = EventBus(SimClock(now))
    seen: list[str] = []

    def enqueue_then_mutate(event: ProbeEvent) -> None:
        seen.append(event.name)
        child = MutableEvent(now, now, "child")
        bus.publish(child)
        child.ts_event = now + timedelta(seconds=1)

    bus.subscribe(ProbeEvent, enqueue_then_mutate)
    bus.subscribe(MutableEvent, lambda event: seen.append(event.name))

    with pytest.raises(EventDispatchError, match="MutableEvent"):
        bus.publish(ProbeEvent(now, now, "root"))

    assert bus.halted
    assert seen == ["root"]


def test_bus_revalidates_between_subscribers() -> None:
    now = _start()
    bus = EventBus(SimClock(now))
    seen: list[str] = []

    def mutate(event: MutableEvent) -> None:
        seen.append("first")
        event.ts_recv = now + timedelta(seconds=1)

    bus.subscribe(MutableEvent, mutate)
    bus.subscribe(MutableEvent, lambda _event: seen.append("second"))

    with pytest.raises(EventDispatchError, match="MutableEvent"):
        bus.publish(MutableEvent(now, now, "root"))

    assert bus.halted
    assert seen == ["first"]


def test_bus_rejects_an_event_whose_key_understates_its_stamps() -> None:
    """The key alone is not trusted: each stamp is checked in its own right."""
    now = _start()
    bus = EventBus(SimClock(now))
    seen: list[str] = []
    bus.subscribe(UnderstatedEvent, lambda event: seen.append(event.name))

    # available_at claims the event is already actionable while ts_event is future.
    with pytest.raises(LookAheadError, match="ts_event"):
        bus.publish(
            UnderstatedEvent(
                ts_event=now + timedelta(seconds=1),
                ts_recv=now,
                available_at=now,
                name="understated",
            )
        )

    assert seen == []


def test_bus_rejects_a_future_availability_key() -> None:
    """A deferred event is not admitted before its key, even with both stamps past."""
    now = _start()
    bus = EventBus(SimClock(now))
    seen: list[str] = []
    bus.subscribe(UnderstatedEvent, lambda event: seen.append(event.name))

    with pytest.raises(LookAheadError, match="available_at"):
        bus.publish(
            UnderstatedEvent(
                ts_event=now - timedelta(seconds=5),
                ts_recv=now - timedelta(seconds=1),
                available_at=now + timedelta(seconds=1),
                name="deferred",
            )
        )

    assert seen == []
