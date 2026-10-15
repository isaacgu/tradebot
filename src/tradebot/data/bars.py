"""Tick-to-bar aggregation (SPEC 4.2, 4.3).

Three rules from the frozen spec shape this module, and each exists to close a
specific hole.

**Bars are closed when delivered.** A bar is emitted only once its interval has
ended, so a strategy can never see a forming bar. `ts_close` is the event timestamp
(`Bar.ts_event`), which is what makes every join on it causal by construction.

**`ts_recv = max(ts_close + seal_latency, max constituent ts_recv, emission time)`.**
In live a bar is never observable at its close instant — the builder needs a
post-boundary tick or timer to know the interval ended. Backtest runs the same
expression through the same code (NN-1), so the backtest cannot make a bar
actionable at an instant live could not.

**`quality_flags` unions the constituent ticks' flags.** Without this, aggregation
launders tick-level problems: a bar built from skewed or imputed ticks would present
as clean, and the quality signal would vanish exactly where a strategy consumes it.

**A sealed bar is not necessarily observable yet.** With a non-zero `seal_latency`
the builder seals as soon as the interval ends, but the resulting `ts_recv` sits
`seal_latency` in the future — that is the whole content of the first arm of the
maximum. So an emitted bar MUST be routed through
:class:`~tradebot.data.deferral.DeferralQueue` like any other event; publishing it
straight to the bus is a look-ahead and the bus will say so. Bars and ticks are
separate streams and take separate queues, so a bar is not head-of-line blocked
behind a later tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from tradebot.core.clock import ReadClock
from tradebot.core.errors import LateTickError
from tradebot.core.timestamps import require_utc
from tradebot.core.types import Bar, Tick, VolumeKind

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_TWO = Decimal(2)


class BarBoundary(Protocol):
    """Maps an instant to the half-open interval containing it, or None if closed."""

    def __call__(self, ts: datetime) -> tuple[datetime, datetime] | None:
        """Return ``[start, end)`` covering *ts*, or None when the market is shut."""
        ...


@dataclass(frozen=True, slots=True)
class FixedInterval:
    """Floor-to-interval boundary for intraday time bars.

    Anchored to the Unix epoch so 1m, 5m, 15m, 1h and 4h intervals land on natural
    clock boundaries. Session-aware boundaries — the 17:00 New York FX day, or a
    broker symbol's own trading day — are supplied instead by
    :func:`tradebot.core.time_rules.fx_session_bounds` and its per-instrument
    equivalents, which share this call signature.
    """

    interval: timedelta

    def __post_init__(self) -> None:
        if self.interval <= timedelta(0):
            raise ValueError("interval must be positive")

    def __call__(self, ts: datetime) -> tuple[datetime, datetime]:
        """Return the interval containing UTC *ts*."""
        moment = require_utc(ts, field="ts")
        elapsed = moment - _EPOCH
        start = _EPOCH + (elapsed // self.interval) * self.interval
        return start, start + self.interval


@dataclass(slots=True)
class _Accumulator:
    """Mutable state of the one bar currently forming."""

    ts_open: datetime
    ts_close: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    spread_total: Decimal
    n_ticks: int
    max_ts_recv: datetime
    flags: set[str]


def _mid(tick: Tick) -> Decimal:
    return (tick.bid + tick.ask) / _TWO


class BarBuilder:
    """Aggregate ticks for one instrument into closed bars on a boundary rule."""

    __slots__ = (
        "_boundary",
        "_clock",
        "_current",
        "_instrument",
        "_last_sealed_close",
        "_seal_latency",
    )

    def __init__(
        self,
        *,
        instrument: str,
        boundary: BarBoundary,
        clock: ReadClock,
        seal_latency: timedelta = timedelta(0),
    ) -> None:
        if seal_latency < timedelta(0):
            raise ValueError("seal_latency cannot be negative")
        self._instrument = instrument
        self._boundary = boundary
        self._clock = clock
        self._seal_latency = seal_latency
        self._current: _Accumulator | None = None
        self._last_sealed_close: datetime | None = None

    @property
    def forming_close(self) -> datetime | None:
        """Return the close instant of the bar currently forming, if any."""
        return None if self._current is None else self._current.ts_close

    def add(self, tick: Tick) -> tuple[Bar, ...]:
        """Fold *tick* in, returning any bar its arrival closed.

        Raises :class:`LateTickError` when the tick belongs to an interval already
        sealed. The bar cannot be reopened — a strategy may already have acted on
        it, so amending it would be a look-ahead — and silently dropping the tick
        would corrupt tick-count volume and the completeness report. The caller owns
        the policy (quarantine, metric, halt), because only it has the context to
        choose; this layer's job is to refuse to hide the problem.
        """
        if tick.instrument != self._instrument:
            raise ValueError(
                f"tick for {tick.instrument!r} sent to a {self._instrument!r} bar builder"
            )
        interval = self._boundary(tick.ts_event)
        if interval is None:
            raise LateTickError(
                f"{tick.ts_event.isoformat()} falls outside any {self._instrument} session"
            )
        start, end = interval

        if self._last_sealed_close is not None and start < self._last_sealed_close:
            raise LateTickError(
                f"tick at {tick.ts_event.isoformat()} belongs to the interval starting "
                f"{start.isoformat()}, which closed no later than the sealed watermark "
                f"{self._last_sealed_close.isoformat()}"
            )

        closed: tuple[Bar, ...] = ()
        if self._current is not None and start > self._current.ts_open:
            closed = (self._seal(self._current),)
            self._current = None
        elif self._current is not None and start < self._current.ts_open:
            raise LateTickError(
                f"tick at {tick.ts_event.isoformat()} belongs to the interval starting "
                f"{start.isoformat()}, which was sealed when the bar opening "
                f"{self._current.ts_open.isoformat()} began"
            )

        self._absorb(tick, start, end)
        return closed

    def flush(self, until: datetime | None = None) -> tuple[Bar, ...]:
        """Seal the forming bar if its interval has ended by *until* (default now).

        This is how a bar closes when no further tick arrives — the live case the
        `seal_latency` rule exists for.
        """
        if self._current is None:
            return ()
        moment = require_utc(self._clock.now() if until is None else until, field="flush time")
        if self._current.ts_close > moment:
            return ()
        sealed = self._seal(self._current)
        self._current = None
        return (sealed,)

    def _absorb(self, tick: Tick, start: datetime, end: datetime) -> None:
        mid = _mid(tick)
        spread = tick.ask - tick.bid
        if self._current is None:
            self._current = _Accumulator(
                ts_open=start,
                ts_close=end,
                open=mid,
                high=mid,
                low=mid,
                close=mid,
                spread_total=spread,
                n_ticks=1,
                max_ts_recv=tick.ts_recv,
                flags=set(tick.quality_flags),
            )
            return
        current = self._current
        current.high = max(current.high, mid)
        current.low = min(current.low, mid)
        current.close = mid
        current.spread_total += spread
        current.n_ticks += 1
        current.max_ts_recv = max(current.max_ts_recv, tick.ts_recv)
        current.flags.update(tick.quality_flags)

    def _seal(self, accumulator: _Accumulator) -> Bar:
        # The three-way maximum is the whole point: a bar is observable no earlier
        # than its close plus the configured seal latency, no earlier than the last
        # tick that went into it, and no earlier than the moment we emitted it.
        ts_recv = max(
            accumulator.ts_close + self._seal_latency,
            accumulator.max_ts_recv,
            require_utc(self._clock.now(), field="clock.now()"),
        )
        bar = Bar(
            instrument=self._instrument,
            ts_open=accumulator.ts_open,
            ts_event=accumulator.ts_close,
            ts_recv=ts_recv,
            open=accumulator.open,
            high=accumulator.high,
            low=accumulator.low,
            close=accumulator.close,
            volume=accumulator.n_ticks,
            volume_kind=VolumeKind.TICK_COUNT,
            spread_mean=accumulator.spread_total / accumulator.n_ticks,
            n_ticks=accumulator.n_ticks,
            quality_flags=tuple(sorted(accumulator.flags)),
        )
        self._last_sealed_close = accumulator.ts_close
        return bar
