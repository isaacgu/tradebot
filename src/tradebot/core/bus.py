"""Synchronous, deterministic, fail-closed in-process event bus."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Protocol, TypeVar, cast

from tradebot.core.clock import ReadClock
from tradebot.core.errors import BusHaltedError, EventDispatchError, LookAheadError
from tradebot.core.timestamps import require_utc
from tradebot.core.types import Event

EventT = TypeVar("EventT", bound=Event)
EventHandler = Callable[[Event], None]


class BusObserver(Protocol):
    """Observability callbacks kept outside the deterministic dispatch policy."""

    def processed(self, event_name: str) -> None:
        """Record a successfully dispatched event."""
        ...

    def rejected(self, event_name: str, reason: str) -> None:
        """Record an event rejected before delivery."""
        ...

    def failed(self, event_name: str, error: BaseException) -> None:
        """Record a subscriber failure that halted dispatch."""
        ...


class _NullObserver:
    def processed(self, event_name: str) -> None:
        del event_name

    def rejected(self, event_name: str, reason: str) -> None:
        del event_name, reason

    def failed(self, event_name: str, error: BaseException) -> None:
        del event_name, error


class Subscription:
    """Idempotent subscription cancellation handle."""

    def __init__(self, cancel_callback: Callable[[], None]) -> None:
        self._cancel_callback = cancel_callback
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        """Return whether this subscription was cancelled."""
        return self._cancelled

    def cancel(self) -> None:
        """Remove the handler from future dispatches."""
        if not self._cancelled:
            self._cancel_callback()
            self._cancelled = True


class EventBus:
    """Deliver exact event types FIFO; nested publications append after current handlers."""

    def __init__(self, clock: ReadClock, observer: BusObserver | None = None) -> None:
        self._clock = clock
        self._observer = observer or _NullObserver()
        self._subscribers: dict[type[object], list[EventHandler]] = {}
        self._queue: deque[Event] = deque()
        self._delivering = False
        self._halted = False

    @property
    def halted(self) -> bool:
        """Return whether a subscriber failure halted this bus."""
        return self._halted

    def subscribe(
        self, event_type: type[EventT], handler: Callable[[EventT], None]
    ) -> Subscription:
        """Register *handler* for exactly *event_type*, preserving registration order."""
        handlers = self._subscribers.setdefault(event_type, [])
        erased_handler = cast(EventHandler, handler)
        handlers.append(erased_handler)

        def cancel() -> None:
            if erased_handler in handlers:
                handlers.remove(erased_handler)

        return Subscription(cancel)

    def publish(self, event: Event) -> None:
        """Publish a currently observable event, or halt/raise without partial continuation."""
        if self._halted:
            raise BusHaltedError("event bus is halted after a dispatch failure")
        self._validate_availability(event, notify_rejection=True)
        self._queue.append(event)
        if self._delivering:
            return
        self._drain()

    def _validate_availability(self, event: Event, *, notify_rejection: bool) -> None:
        """Reject events that were not observable at the current clock instant."""
        event_name = type(event).__name__
        now = require_utc(self._clock.now(), field="clock.now()")
        ts_event = require_utc(event.ts_event, field="ts_event")
        ts_recv = require_utc(event.ts_recv, field="ts_recv")
        if ts_event > now:
            if notify_rejection:
                self._notify_rejected(event_name, "ts_event")
            raise LookAheadError(
                f"{event_name} ts_event {ts_event.isoformat()} is later than "
                f"clock {now.isoformat()}"
            )
        if ts_recv > now:
            if notify_rejection:
                self._notify_rejected(event_name, "ts_recv")
            raise LookAheadError(
                f"{event_name} ts_recv {ts_recv.isoformat()} is later than clock {now.isoformat()}"
            )

    def _drain(self) -> None:
        self._delivering = True
        try:
            while self._queue:
                event = self._queue.popleft()
                event_name = type(event).__name__
                handlers = tuple(self._subscribers.get(type(event), ()))
                try:
                    self._validate_availability(event, notify_rejection=False)
                    for handler in handlers:
                        # Revalidate at the point of use. Event is a structural
                        # protocol, so an adapter could otherwise enqueue a mutable
                        # object and change its availability before a later handler.
                        self._validate_availability(event, notify_rejection=False)
                        handler(event)
                    self._observer.processed(event_name)
                except BaseException as error:
                    self._halt(event_name, error)
        finally:
            self._delivering = False

    def _notify_rejected(self, event_name: str, reason: str) -> None:
        try:
            self._observer.rejected(event_name, reason)
        except BaseException as error:
            self._halted = True
            self._queue.clear()
            raise EventDispatchError(
                f"observer failed while recording rejection for {event_name}; event bus halted"
            ) from error

    def _halt(self, event_name: str, error: BaseException) -> None:
        self._halted = True
        self._queue.clear()
        dispatch_error = EventDispatchError(f"dispatch failed for {event_name}; event bus halted")
        try:
            self._observer.failed(event_name, error)
        except BaseException as observer_error:
            dispatch_error.add_note(
                f"failure observer also raised {type(observer_error).__name__}: {observer_error}"
            )
        raise dispatch_error from error
