"""Injectable simulation and guarded wall clocks."""

from __future__ import annotations

import asyncio
import heapq
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from tradebot.core.errors import ClockDiscontinuityError, ClockMovedBackwardError
from tradebot.core.timestamps import require_utc

ScheduleCallback = Callable[[], None]


class ScheduledHandle(Protocol):
    """Cancellation handle returned by clock schedules."""

    @property
    def cancelled(self) -> bool:
        """Return whether the callback has been cancelled."""
        ...

    @property
    def failed(self) -> bool:
        """Return whether this schedule died because the clock or the callback raised."""
        ...

    def cancel(self) -> None:
        """Prevent the callback from running if it has not run yet."""
        ...


class ReadClock(Protocol):
    """Narrow clock view exposed to strategy code."""

    def now(self) -> datetime:
        """Return current UTC time."""
        ...


class Clock(ReadClock, Protocol):
    """Clock port used at I/O and scheduling boundaries."""

    async def sleep_until(self, ts: datetime) -> None:
        """Suspend until UTC timestamp *ts*."""
        ...

    def schedule(self, ts: datetime, callback: ScheduleCallback) -> ScheduledHandle:
        """Schedule *callback* for UTC timestamp *ts*."""
        ...


class _SimScheduledHandle:
    __slots__ = ("callback", "cancelled", "failed", "sequence", "timestamp")

    def __init__(self, timestamp: datetime, sequence: int, callback: ScheduleCallback) -> None:
        self.timestamp = timestamp
        self.sequence = sequence
        self.callback = callback
        self.cancelled = False
        self.failed = False

    def cancel(self) -> None:
        self.cancelled = True


class SimClock:
    """Deterministic UTC clock advanced only by the simulation runner."""

    def __init__(self, start: datetime) -> None:
        self._now = require_utc(start, field="SimClock start")
        self._sequence = 0
        self._scheduled: list[tuple[datetime, int, _SimScheduledHandle]] = []
        self._advancing = False

    def now(self) -> datetime:
        """Return current simulated UTC time."""
        return self._now

    def advance_to(self, ts: datetime) -> None:
        """Advance to UTC *ts* and execute due callbacks in stable registration order."""
        target = require_utc(ts, field="SimClock target")
        if self._advancing:
            raise ClockDiscontinuityError("SimClock advance_to is not re-entrant")
        if target < self._now:
            raise ClockMovedBackwardError(
                f"SimClock cannot move backward from {self._now.isoformat()} "
                f"to {target.isoformat()}"
            )
        self._advancing = True
        try:
            while self._scheduled and self._scheduled[0][0] <= target:
                timestamp, _sequence, handle = heapq.heappop(self._scheduled)
                if handle.cancelled:
                    continue
                self._now = timestamp
                # Unlike a wall timer, this callback has a caller — the simulation
                # driver — so the failure propagates. Mark the handle first so a
                # dead schedule never reports itself live in either clock.
                try:
                    handle.callback()
                except BaseException:
                    handle.failed = True
                    raise
            self._now = target
        finally:
            self._advancing = False

    async def sleep_until(self, ts: datetime) -> None:
        """Wait until the simulation runner advances to UTC *ts*."""
        target = require_utc(ts, field="sleep target")
        if target <= self._now:
            return
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        handle = self.schedule(target, lambda: future.set_result(None))
        try:
            await future
        finally:
            handle.cancel()

    def schedule(self, ts: datetime, callback: ScheduleCallback) -> ScheduledHandle:
        """Schedule a synchronous callback at UTC *ts*."""
        timestamp = require_utc(ts, field="schedule timestamp")
        if timestamp < self._now:
            raise ClockMovedBackwardError("cannot schedule a SimClock callback in the past")
        self._sequence += 1
        handle = _SimScheduledHandle(timestamp, self._sequence, callback)
        heapq.heappush(self._scheduled, (timestamp, self._sequence, handle))
        return handle


class _WallScheduledHandle:
    __slots__ = (
        "_callback",
        "_cancelled",
        "_failed",
        "_loop",
        "_now",
        "_on_failure",
        "_target",
        "_timer",
    )

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        now: Callable[[], datetime],
        target: datetime,
        callback: ScheduleCallback,
        current: datetime,
        on_failure: Callable[[BaseException], None],
    ) -> None:
        self._loop = loop
        self._now = now
        self._target = target
        self._callback = callback
        self._on_failure = on_failure
        self._cancelled = False
        self._failed = False
        self._timer: asyncio.TimerHandle | None = None
        self._arm(current)

    def _arm(self, current: datetime) -> None:
        delay = max(0.0, (self._target - current).total_seconds())
        self._timer = self._loop.call_later(delay, self._wake)

    def _wake(self) -> None:
        self._timer = None
        if self._cancelled:
            return
        # A timer callback has no caller to propagate to: an escaping exception
        # reaches only asyncio's default handler, which would silently drop the
        # callback while this handle still reported itself live. Latch instead.
        try:
            current = self._now()
            if current < self._target:
                self._arm(current)
                return
            self._callback()
        except BaseException as error:
            self._failed = True
            self._on_failure(error)

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def failed(self) -> bool:
        """Return whether the clock guard or the callback raised, killing this schedule."""
        return self._failed

    def cancel(self) -> None:
        self._cancelled = True
        if self._timer is not None:
            self._timer.cancel()


def _utc_now() -> datetime:
    return datetime.now(UTC)


class WallClock:
    """UTC wall clock that detects regression and NTP-style discontinuities."""

    def __init__(
        self,
        *,
        now_source: Callable[[], datetime] = _utc_now,
        monotonic_source: Callable[[], float] = time.monotonic,
        max_step: timedelta = timedelta(seconds=1),
        on_schedule_failure: Callable[[BaseException], None] | None = None,
    ) -> None:
        if max_step < timedelta(0):
            raise ValueError("max_step cannot be negative")
        self._now_source = now_source
        self._monotonic_source = monotonic_source
        self._max_step = max_step
        self._on_schedule_failure = on_schedule_failure
        self._last_wall: datetime | None = None
        self._last_monotonic: float | None = None
        self._schedule_failure: BaseException | None = None

    @property
    def schedule_failure(self) -> BaseException | None:
        """Return the first failure that killed a scheduled callback, else None."""
        return self._schedule_failure

    def _record_schedule_failure(self, error: BaseException) -> None:
        """Latch a dead schedule and notify the supervisor without re-entering the loop."""
        if self._schedule_failure is None:
            self._schedule_failure = error
        if self._on_schedule_failure is None:
            return
        try:
            self._on_schedule_failure(error)
        except BaseException as observer_error:
            error.add_note(
                f"schedule failure observer also raised "
                f"{type(observer_error).__name__}: {observer_error}"
            )

    def now(self) -> datetime:
        """Return guarded UTC wall time, raising on a discontinuity larger than policy."""
        wall = require_utc(self._now_source(), field="WallClock source")
        monotonic = self._monotonic_source()
        if self._last_wall is not None and self._last_monotonic is not None:
            wall_delta = (wall - self._last_wall).total_seconds()
            monotonic_delta = monotonic - self._last_monotonic
            if wall_delta < 0 or monotonic_delta < 0:
                raise ClockMovedBackwardError("wall or monotonic clock moved backward")
            if abs(wall_delta - monotonic_delta) > self._max_step.total_seconds():
                raise ClockDiscontinuityError(
                    "wall clock step exceeded the configured monotonic guard"
                )
        self._last_wall = wall
        self._last_monotonic = monotonic
        return wall

    async def sleep_until(self, ts: datetime) -> None:
        """Sleep on monotonic timers until guarded wall time reaches UTC *ts*."""
        target = require_utc(ts, field="sleep target")
        while True:
            current = self.now()
            if current >= target:
                return
            await asyncio.sleep((target - current).total_seconds())

    def schedule(self, ts: datetime, callback: ScheduleCallback) -> ScheduledHandle:
        """Schedule a guarded callback for UTC *ts*, rejecting a deadline already past."""
        target = require_utc(ts, field="schedule timestamp")
        current = self.now()
        if target < current:
            raise ClockMovedBackwardError("cannot schedule a WallClock callback in the past")
        return _WallScheduledHandle(
            loop=asyncio.get_running_loop(),
            now=self.now,
            target=target,
            callback=callback,
            current=current,
            on_failure=self._record_schedule_failure,
        )
