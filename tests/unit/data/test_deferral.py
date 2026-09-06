from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from tradebot.core.clock import SimClock
from tradebot.core.errors import InvalidTimestampError
from tradebot.core.types import Event
from tradebot.data.deferral import DeferralQueue


def _utc(second: int = 0) -> datetime:
    return datetime(2025, 3, 17, 12, 0, second, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Arrival:
    """Minimal Event: the queue cares only about the availability key."""

    ts_event: datetime
    ts_recv: datetime
    name: str

    @property
    def available_at(self) -> datetime:
        return max(self.ts_event, self.ts_recv)


def _arrival(name: str, key: datetime) -> _Arrival:
    return _Arrival(ts_event=key, ts_recv=key, name=name)


def _names(events: tuple[Event, ...]) -> list[str]:
    names: list[str] = []
    for event in events:
        assert isinstance(event, _Arrival)
        names.append(event.name)
    return names


def test_an_event_is_not_released_before_its_availability_key() -> None:
    clock = SimClock(_utc(0))
    queue = DeferralQueue(clock)
    queue.submit(_arrival("future", _utc(5)))

    assert queue.release() == ()
    assert len(queue) == 1
    assert queue.head_available_at == _utc(5)


def test_an_event_is_released_once_the_clock_reaches_its_key() -> None:
    clock = SimClock(_utc(0))
    queue = DeferralQueue(clock)
    queue.submit(_arrival("due", _utc(5)))

    clock.advance_to(_utc(5))
    released = queue.release()

    assert _names(released) == ["due"]
    assert len(queue) == 0
    assert queue.head_available_at is None


def test_release_is_inclusive_at_the_key_instant() -> None:
    """Admission is `key <= now`, matching the bus, so the boundary is releasable."""
    clock = SimClock(_utc(5))
    queue = DeferralQueue(clock)
    queue.submit(_arrival("boundary", _utc(5)))

    assert _names(queue.release()) == ["boundary"]


def test_release_preserves_submission_order() -> None:
    clock = SimClock(_utc(0))
    queue = DeferralQueue(clock)
    for index in range(4):
        queue.submit(_arrival(f"e{index}", _utc(index)))

    clock.advance_to(_utc(9))

    assert _names(queue.release()) == ["e0", "e1", "e2", "e3"]


def test_the_head_blocks_everything_behind_it() -> None:
    """Head-of-line: source order outranks key order, so nothing overtakes."""
    clock = SimClock(_utc(0))
    queue = DeferralQueue(clock)
    queue.submit(_arrival("ready", _utc(1)))
    queue.submit(_arrival("blocking", _utc(9)))
    queue.submit(_arrival("also-ready", _utc(2)))

    clock.advance_to(_utc(3))
    first = queue.release()

    assert _names(first) == ["ready"]
    assert len(queue) == 2, "also-ready is held behind the blocking head"

    clock.advance_to(_utc(9))
    assert _names(queue.release()) == ["blocking", "also-ready"]


def test_a_partial_release_leaves_the_remainder_in_order() -> None:
    clock = SimClock(_utc(0))
    queue = DeferralQueue(clock)
    for index in range(5):
        queue.submit(_arrival(f"e{index}", _utc(index)))

    clock.advance_to(_utc(2))
    assert _names(queue.release()) == ["e0", "e1", "e2"]
    assert queue.head_available_at == _utc(3)

    clock.advance_to(_utc(4))
    assert _names(queue.release()) == ["e3", "e4"]


def test_submission_never_consults_the_clock() -> None:
    """An event arriving before its own key is the normal case, not an error."""
    clock = SimClock(_utc(0))
    queue = DeferralQueue(clock)

    queue.submit(_arrival("far-future", _utc(59)))

    assert len(queue) == 1


def test_a_late_arrival_is_held_in_arrival_order_not_reordered() -> None:
    """Transit jitter delivers an older tick after a newer one; that is expected.

    The late event waits behind its predecessor rather than overtaking it. The cost
    is delay past its own key; the benefit is that the delivered sequence is the one
    a replay reproduces. Releasing it early is the per-event-timer behaviour
    ADR-0006 rules out.
    """
    clock = SimClock(_utc(0))
    queue = DeferralQueue(clock)
    queue.submit(_arrival("newer", _utc(5)))
    queue.submit(_arrival("late-but-older", _utc(1)))

    clock.advance_to(_utc(1))
    assert queue.release() == (), "the older event does not overtake the head"

    clock.advance_to(_utc(5))
    assert _names(queue.release()) == ["newer", "late-but-older"]


def test_an_equal_availability_key_is_accepted() -> None:
    """Ties are legitimate: two quotes can share a millisecond."""
    clock = SimClock(_utc(0))
    queue = DeferralQueue(clock)
    queue.submit(_arrival("first", _utc(5)))
    queue.submit(_arrival("second", _utc(5)))

    clock.advance_to(_utc(5))

    assert _names(queue.release()) == ["first", "second"]


def test_an_empty_queue_releases_nothing() -> None:
    queue = DeferralQueue(SimClock(_utc(0)))

    assert queue.release() == ()
    assert queue.head_available_at is None


def test_a_naive_availability_key_is_rejected_on_submission() -> None:
    queue = DeferralQueue(SimClock(_utc(0)))
    naive = datetime(2025, 3, 17, 12, 0, 5)

    with pytest.raises(InvalidTimestampError, match="available_at"):
        queue.submit(_Arrival(ts_event=naive, ts_recv=naive, name="naive"))


def test_a_clock_failure_during_release_propagates() -> None:
    """Fail closed: a broken clock must not release events on a guess."""

    class _BrokenClock:
        def now(self) -> datetime:
            raise RuntimeError("clock unavailable")

    queue = DeferralQueue(_BrokenClock())
    queue.submit(_arrival("held", _utc(1)))

    with pytest.raises(RuntimeError, match="clock unavailable"):
        queue.release()
    assert len(queue) == 1, "nothing is released when the clock cannot be read"


def test_a_backfilled_event_releases_immediately() -> None:
    """Its key is its receipt time, so we could act as soon as we learned it.

    ADR-0006: backfill is exempt from any live-tail arrival bound. Freshness is a
    separate concern, carried by the BACKFILLED quality flag, not by withholding.
    """
    now = _utc(30)
    clock = SimClock(now)
    queue = DeferralQueue(clock)
    stale_market_time = now - timedelta(hours=2)
    queue.submit(_Arrival(ts_event=stale_market_time, ts_recv=now, name="backfilled"))

    assert _names(queue.release()) == ["backfilled"]
