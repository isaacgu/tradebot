"""Head-of-line deferral queue for live feeds (ADR-0006).

A live adapter learns about an event at an arbitrary instant and must not hand it to
the bus before the platform could have acted on it. That release decision belongs
here rather than in the bus: the bus's job is to refuse a look-ahead, and a
component whose only tool is refusal cannot *wait*.

Deliberately NOT per-event timers. Independent timers fire in whatever order their
delays expire, which reorders events under transit jitter and would void ADR-0002's
guarantee that upstream adapters own stable source order. One queue, one clock
reading per release, strict FIFO.

The backtest path needs none of this: its driver advances `SimClock` to an event's
availability key before publishing, so ordering comes free. That asymmetry is why
ADR-0006 requires an accelerated-`WallClock` arrival test — the replay harness
cannot exercise this file.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

from tradebot.core.clock import ReadClock
from tradebot.core.timestamps import require_utc
from tradebot.core.types import Event


class DeferralQueue:
    """Hold events in submission order, releasing only those already observable."""

    __slots__ = ("_clock", "_pending")

    def __init__(self, clock: ReadClock) -> None:
        self._clock = clock
        self._pending: deque[Event] = deque()

    def __len__(self) -> int:
        """Return how many events are still held."""
        return len(self._pending)

    @property
    def head_available_at(self) -> datetime | None:
        """Return the availability key gating the queue, or None when empty."""
        if not self._pending:
            return None
        return require_utc(self._pending[0].available_at, field="available_at")

    def submit(self, event: Event) -> None:
        """Accept *event* at the tail, in the source order the feed owns.

        Submission never consults the clock: an event arriving before its own
        availability key is the normal case and the reason this queue exists.

        A key that regresses against the previous submission is **accepted**, not
        rejected. Transit jitter genuinely delivers an older tick after a newer one,
        and that is the case this queue exists to absorb: it is held in arrival
        order and released behind its predecessor, rather than overtaking it. The
        cost is that a late event waits past its own key; the benefit is that the
        delivered sequence is the one a replay reproduces. Reordering to release it
        early would take ownership of an ordering guarantee ADR-0002 assigns to the
        adapter, and is exactly the per-event-timer behaviour ADR-0006 rules out.
        """
        require_utc(event.available_at, field="available_at")
        self._pending.append(event)

    def release(self) -> tuple[Event, ...]:
        """Return the leading run of events whose availability key has passed.

        The clock is read ONCE per call, so every event in one release is judged
        against a single instant; reading per event would let time drift mid-drain
        and make the boundary depend on how many events happened to be queued.

        Stops at the first event that is not yet observable and does not look past
        it, which is what keeps release order equal to submission order.
        """
        now = require_utc(self._clock.now(), field="clock.now()")
        released: list[Event] = []
        while self._pending:
            head = self._pending[0]
            if require_utc(head.available_at, field="available_at") > now:
                break
            released.append(self._pending.popleft())
        return tuple(released)
