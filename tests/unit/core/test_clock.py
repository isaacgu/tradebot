from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from tradebot.core.clock import SimClock, WallClock
from tradebot.core.errors import (
    ClockDiscontinuityError,
    ClockMovedBackwardError,
    InvalidTimestampError,
)


def _start() -> datetime:
    return datetime(2024, 2, 29, 23, 59, 58, tzinfo=UTC)


def test_sim_clock_advances_and_runs_same_time_callbacks_fifo() -> None:
    start = _start()
    clock = SimClock(start)
    called: list[str] = []
    clock.schedule(start + timedelta(seconds=2), lambda: called.append("second-a"))
    clock.schedule(start + timedelta(seconds=1), lambda: called.append("first"))
    clock.schedule(start + timedelta(seconds=2), lambda: called.append("second-b"))

    clock.advance_to(start + timedelta(seconds=3))

    assert called == ["first", "second-a", "second-b"]
    assert clock.now() == datetime(2024, 3, 1, 0, 0, 1, tzinfo=UTC)


def test_sim_clock_cancellation_and_backward_guard() -> None:
    start = _start()
    clock = SimClock(start)
    called: list[str] = []
    handle = clock.schedule(start + timedelta(seconds=1), lambda: called.append("bad"))
    handle.cancel()
    clock.advance_to(start + timedelta(seconds=2))

    assert called == []
    assert handle.cancelled
    with pytest.raises(ClockMovedBackwardError):
        clock.advance_to(start)
    with pytest.raises(ClockMovedBackwardError, match="schedule"):
        clock.schedule(start, lambda: None)


async def test_sim_clock_sleep_completes_only_after_advance() -> None:
    start = _start()
    clock = SimClock(start)
    task = asyncio.create_task(clock.sleep_until(start + timedelta(seconds=1)))
    await asyncio.sleep(0)
    assert not task.done()

    clock.advance_to(start + timedelta(seconds=1))
    await task


async def test_sim_clock_sleep_in_past_returns_immediately() -> None:
    clock = SimClock(_start())
    await clock.sleep_until(_start())


async def test_cancelled_sim_sleep_removes_scheduled_callback() -> None:
    start = _start()
    clock = SimClock(start)
    task = asyncio.create_task(clock.sleep_until(start + timedelta(seconds=1)))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    clock.advance_to(start + timedelta(seconds=2))


def test_sim_clock_rejects_reentrant_advance_without_regressing() -> None:
    start = _start()
    clock = SimClock(start)
    clock.schedule(
        start + timedelta(seconds=1),
        lambda: clock.advance_to(start + timedelta(seconds=3)),
    )
    with pytest.raises(ClockDiscontinuityError, match="re-entrant"):
        clock.advance_to(start + timedelta(seconds=2))
    assert clock.now() == start + timedelta(seconds=1)


def test_wall_clock_detects_backward_ntp_step() -> None:
    start = _start()
    readings: Iterator[datetime] = iter([start, start - timedelta(seconds=2)])
    clock = WallClock(now_source=lambda: next(readings))

    assert clock.now() == start
    with pytest.raises(ClockMovedBackwardError, match="backward"):
        clock.now()


def test_wall_clock_detects_large_forward_ntp_step() -> None:
    start = _start()
    wall_readings: Iterator[datetime] = iter([start, start + timedelta(seconds=2.1)])
    monotonic_readings: Iterator[float] = iter([100.0, 100.1])
    clock = WallClock(
        now_source=lambda: next(wall_readings),
        monotonic_source=lambda: next(monotonic_readings),
        max_step=timedelta(seconds=1),
    )

    assert clock.now() == start
    with pytest.raises(ClockDiscontinuityError, match="monotonic guard"):
        clock.now()


def test_wall_clock_detects_monotonic_regression_and_bad_policy() -> None:
    start = _start()
    wall_readings: Iterator[datetime] = iter([start, start + timedelta(milliseconds=1)])
    monotonic_readings: Iterator[float] = iter([10.0, 9.0])
    clock = WallClock(
        now_source=lambda: next(wall_readings),
        monotonic_source=lambda: next(monotonic_readings),
    )
    assert clock.now() == start
    with pytest.raises(ClockMovedBackwardError):
        clock.now()
    with pytest.raises(ValueError, match="max_step"):
        WallClock(max_step=timedelta(seconds=-1))


async def test_wall_clock_past_sleep_returns_immediately() -> None:
    clock = WallClock()
    await clock.sleep_until(clock.now() - timedelta(seconds=1))


async def test_wall_clock_schedule_rejects_a_deadline_already_past() -> None:
    """Both clocks must refuse a past deadline; only SimClock used to."""
    clock = WallClock()
    with pytest.raises(ClockMovedBackwardError, match="schedule"):
        clock.schedule(clock.now() - timedelta(seconds=1), lambda: None)


async def test_wall_clock_schedule_runs_and_cancels_a_future_deadline() -> None:
    clock = WallClock()
    called = asyncio.Event()
    handle = clock.schedule(clock.now() + timedelta(milliseconds=20), called.set)
    await asyncio.wait_for(called.wait(), timeout=1.0)
    assert not handle.cancelled
    assert not handle.failed
    handle.cancel()
    assert handle.cancelled


async def test_wall_clock_latches_a_failing_scheduled_callback() -> None:
    """A raising callback must not vanish into the loop leaving the handle live."""
    observed: list[BaseException] = []
    reported = asyncio.Event()
    failure = RuntimeError("scheduled callback failed")

    def observe(error: BaseException) -> None:
        observed.append(error)
        reported.set()

    def boom() -> None:
        raise failure

    clock = WallClock(on_schedule_failure=observe)
    handle = clock.schedule(clock.now() + timedelta(milliseconds=5), boom)
    await asyncio.wait_for(reported.wait(), timeout=1.0)

    assert handle.failed
    assert clock.schedule_failure is failure
    assert observed == [failure]
    assert not handle.cancelled


async def test_wall_clock_latches_a_discontinuity_raised_inside_the_timer() -> None:
    """The reported fail-open: the guard fires inside _wake, with no caller to catch it."""
    start = _start()
    wall_readings: Iterator[datetime] = iter([start, start + timedelta(seconds=30)])
    monotonic_readings: Iterator[float] = iter([100.0, 100.01])
    observed: list[BaseException] = []
    reported = asyncio.Event()

    def observe(error: BaseException) -> None:
        observed.append(error)
        reported.set()

    clock = WallClock(
        now_source=lambda: next(wall_readings),
        monotonic_source=lambda: next(monotonic_readings),
        on_schedule_failure=observe,
    )
    handle = clock.schedule(start + timedelta(milliseconds=5), lambda: None)
    await asyncio.wait_for(reported.wait(), timeout=1.0)

    assert handle.failed
    assert isinstance(clock.schedule_failure, ClockDiscontinuityError)
    assert isinstance(observed[0], ClockDiscontinuityError)


async def test_wall_clock_latches_without_a_supervisor_configured() -> None:
    """The default construction must still latch; supervision is optional, latching is not."""
    fired = asyncio.Event()
    failure = RuntimeError("no observer configured")

    def boom() -> None:
        fired.set()
        raise failure

    clock = WallClock()
    handle = clock.schedule(clock.now() + timedelta(milliseconds=5), boom)
    await asyncio.wait_for(fired.wait(), timeout=1.0)

    assert handle.failed
    assert clock.schedule_failure is failure


async def test_wall_clock_schedule_failure_observer_error_is_noted_not_raised() -> None:
    reported = asyncio.Event()
    failure = RuntimeError("scheduled callback failed")

    def hostile(_error: BaseException) -> None:
        reported.set()
        raise ValueError("observer exploded")

    def boom() -> None:
        raise failure

    clock = WallClock(on_schedule_failure=hostile)
    handle = clock.schedule(clock.now() + timedelta(milliseconds=5), boom)
    await asyncio.wait_for(reported.wait(), timeout=1.0)

    assert handle.failed
    assert clock.schedule_failure is failure
    assert any("observer exploded" in note for note in failure.__notes__)


def test_sim_clock_marks_a_failing_scheduled_callback_and_propagates() -> None:
    start = _start()
    clock = SimClock(start)

    def boom() -> None:
        raise RuntimeError("sim callback failed")

    handle = clock.schedule(start + timedelta(seconds=1), boom)
    with pytest.raises(RuntimeError, match="sim callback failed"):
        clock.advance_to(start + timedelta(seconds=2))

    assert handle.failed
    assert not handle.cancelled


async def test_wall_clock_sleep_rearms_after_allowed_slow_slew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _start()
    wall_readings: Iterator[datetime] = iter(
        [start, start + timedelta(seconds=0.5), start + timedelta(seconds=1)]
    )
    monotonic_readings: Iterator[float] = iter([100.0, 101.0, 102.0])
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("tradebot.core.clock.asyncio.sleep", fake_sleep)
    clock = WallClock(
        now_source=lambda: next(wall_readings),
        monotonic_source=lambda: next(monotonic_readings),
    )

    await clock.sleep_until(start + timedelta(seconds=1))

    assert delays == [1.0, 0.5]


def test_wall_clock_schedule_rearms_instead_of_firing_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTimer:
        def __init__(self) -> None:
            self.was_cancelled = False

        def cancel(self) -> None:
            self.was_cancelled = True

    class FakeLoop:
        def __init__(self) -> None:
            self.pending: list[tuple[float, object, FakeTimer]] = []

        def call_later(self, delay: float, callback: object) -> FakeTimer:
            timer = FakeTimer()
            self.pending.append((delay, callback, timer))
            return timer

    start = _start()
    wall_readings: Iterator[datetime] = iter(
        [start, start + timedelta(seconds=0.5), start + timedelta(seconds=1)]
    )
    monotonic_readings: Iterator[float] = iter([100.0, 101.0, 102.0])
    loop = FakeLoop()
    called: list[str] = []
    monkeypatch.setattr("tradebot.core.clock.asyncio.get_running_loop", lambda: loop)
    clock = WallClock(
        now_source=lambda: next(wall_readings),
        monotonic_source=lambda: next(monotonic_readings),
    )

    handle = clock.schedule(start + timedelta(seconds=1), lambda: called.append("ready"))
    first_delay, first_callback, _first_timer = loop.pending.pop(0)
    assert first_delay == 1.0
    assert callable(first_callback)
    first_callback()
    assert called == []

    second_delay, second_callback, _second_timer = loop.pending.pop(0)
    assert second_delay == 0.5
    assert callable(second_callback)
    second_callback()
    assert called == ["ready"]
    assert not handle.cancelled


def test_clock_rejects_naive_time() -> None:
    with pytest.raises(InvalidTimestampError, match="UTC-aware"):
        SimClock(datetime(2025, 1, 1))
