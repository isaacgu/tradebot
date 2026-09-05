"""Streaming tick-quality checks for the immutable clean layer (SPEC 4.4).

The pipeline retains evidence and separates two questions that are easy to blur:
whether a row belongs in the clean corpus, and whether it is safe input to a mid/bar.
Locked, crossed, non-positive and out-of-session rows stay in clean ticks with flags
but have ``eligible_for_bars=False``. A future-confirmed transient price is retained
as a separately named retrospective annotation and never rewrites causal bar input.
"""

from __future__ import annotations

import heapq
from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from tradebot.core.timestamps import require_utc
from tradebot.core.types import QualityFlag
from tradebot.data.bars import BarBoundary
from tradebot.data.normalize import TickObservation, normalize_tick


class DataQualityFlag(StrEnum):
    """Deterministic flags added by the SPEC 4.4 clean-layer checks."""

    TIME_REGRESSION = "TIME_REGRESSION"
    LOCKED_QUOTE = "LOCKED_QUOTE"
    CROSSED_QUOTE = "CROSSED_QUOTE"
    NONPOSITIVE_BID = "NONPOSITIVE_BID"
    NONPOSITIVE_ASK = "NONPOSITIVE_ASK"
    SPREAD_OUTLIER = "SPREAD_OUTLIER"
    PRICE_OUTLIER = "PRICE_OUTLIER"
    GAP = "GAP"
    GAP_CALENDAR_UNKNOWN = "GAP_CALENDAR_UNKNOWN"
    OUT_OF_SESSION = "OUT_OF_SESSION"
    QUALITY_WARMUP = "QUALITY_WARMUP"


class QualityCheckStatus(StrEnum):
    """A four-state result; only PASSED asserts that a check passed."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    INDETERMINATE = "INDETERMINATE"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class LiquidityDayLike(Protocol):
    """Narrow view of calendar.LiquidityDay consumed by quality checks."""

    expected_intervals: tuple[tuple[datetime, datetime], ...]


class LiquidityCalendarLike(Protocol):
    """Point-in-time expected-liquidity lookup; None always means unknown."""

    def lookup(
        self, instrument: str, day: date, *, known_at: datetime
    ) -> LiquidityDayLike | None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityThresholds:
    """Prospective defaults adopted for P1; configuration remains the owner."""

    spread_multiplier: Decimal = Decimal("10")
    price_sigma: Decimal = Decimal("20")
    price_reversion_ticks: int = 5
    gap_threshold: timedelta = timedelta(seconds=10)
    fast_market_median: timedelta = timedelta(seconds=1)
    rolling_horizon: timedelta = timedelta(hours=1)
    minimum_history: int = 20

    def __post_init__(self) -> None:
        for field in ("spread_multiplier", "price_sigma"):
            value = getattr(self, field)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{field} must be a finite positive Decimal")
        if type(self.price_reversion_ticks) is not int or self.price_reversion_ticks < 1:
            raise ValueError("price_reversion_ticks must be a positive int")
        if type(self.minimum_history) is not int or self.minimum_history < 2:
            raise ValueError("minimum_history must be an int of at least 2")
        for field in ("gap_threshold", "fast_market_median", "rolling_horizon"):
            if getattr(self, field) <= timedelta(0):
                raise ValueError(f"{field} must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityInput:
    """One exact source row plus the stable sequence assigned by persistence."""

    instrument: str
    source: str
    seq: int
    ts_event: datetime
    bid: Decimal
    ask: Decimal
    source_flags: int = 0
    raw_identity: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.instrument or not self.source:
            raise ValueError("instrument and source must be non-empty")
        if type(self.seq) is not int or self.seq < 0:
            raise ValueError("seq must be a non-negative int")
        if type(self.source_flags) is not int:
            raise TypeError("source_flags must be int")
        if type(self.raw_identity) is not tuple or any(
            not isinstance(item, str) for item in self.raw_identity
        ):
            raise TypeError("raw_identity must be tuple[str, ...]")
        require_utc(self.ts_event, field="ts_event")
        for field in ("bid", "ask"):
            value = getattr(self, field)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field} must be a finite Decimal")


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanTickRecord:
    """A clean-layer row; invalid mids remain evidence but are bar-ineligible."""

    instrument: str
    ts_event: datetime
    ts_recv: datetime
    available_at: datetime
    bid: Decimal
    ask: Decimal
    bid_size: int | None
    ask_size: int | None
    source: str
    seq: int
    source_flags: int
    quality_flags: tuple[str, ...]
    retrospective_flags: tuple[str, ...]
    eligible_for_bars: bool

    def as_mapping(self) -> Mapping[str, object]:
        """Return a PyArrow-ready mapping in the frozen clean schema."""
        return {
            "instrument": self.instrument,
            "ts_event": self.ts_event,
            "ts_recv": self.ts_recv,
            "available_at": self.available_at,
            "bid": self.bid,
            "ask": self.ask,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "source": self.source,
            "seq": self.seq,
            "source_flags": self.source_flags,
            "quality_flags": list(self.quality_flags),
            "retrospective_flags": list(self.retrospective_flags),
            "eligible_for_bars": self.eligible_for_bars,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class QualitySummary:
    """Measured row counts and honest evaluability statuses for one stream."""

    input_rows: int
    output_rows: int
    duplicate_rows: int
    bar_eligible_rows: int
    bar_excluded_rows: int
    flag_counts: tuple[tuple[str, int], ...]
    retrospective_flag_counts: tuple[tuple[str, int], ...]
    quality_status: QualityCheckStatus
    calendar_status: QualityCheckStatus
    cross_source_status: QualityCheckStatus
    calendar_days_checked: int
    calendar_days_missing: tuple[str, ...]


@dataclass(slots=True)
class _PendingTick:
    tick_index: int
    record: CleanTickRecord
    flags: set[str]
    retrospective_flags: set[str]
    eligible_for_bars: bool
    candidate_baseline: Decimal | None = None
    candidate_band: Decimal | None = None


class _RollingMedian:
    """Exact O(log n) rolling median with timestamp expiry and lazy heap deletion."""

    def __init__(self, horizon: timedelta) -> None:
        self._horizon = horizon
        self._lower: list[tuple[Decimal, int]] = []
        self._upper: list[tuple[Decimal, int]] = []
        self._expired: set[int] = set()
        self._side: dict[int, str] = {}
        self._values: dict[int, Decimal] = {}
        self._arrival: deque[tuple[datetime, int]] = deque()
        self._next_id = 0
        self._lower_size = 0
        self._upper_size = 0

    def _prune(self, heap: list[tuple[Decimal, int]]) -> None:
        while heap and heap[0][1] in self._expired:
            _value, identifier = heapq.heappop(heap)
            self._expired.remove(identifier)

    def _rebalance(self) -> None:
        self._prune(self._lower)
        self._prune(self._upper)
        while self._lower_size > self._upper_size + 1:
            negative, identifier = heapq.heappop(self._lower)
            heapq.heappush(self._upper, (-negative, identifier))
            self._side[identifier] = "upper"
            self._lower_size -= 1
            self._upper_size += 1
            self._prune(self._lower)
        while self._lower_size < self._upper_size:
            value, identifier = heapq.heappop(self._upper)
            heapq.heappush(self._lower, (-value, identifier))
            self._side[identifier] = "lower"
            self._upper_size -= 1
            self._lower_size += 1
            self._prune(self._upper)

    def _compact(self) -> None:
        logical = self._lower_size + self._upper_size
        if len(self._lower) + len(self._upper) <= (2 * logical) + 1024:
            return
        ordered = sorted((value, identifier) for identifier, value in self._values.items())
        split = (len(ordered) + 1) // 2
        self._lower = [(-value, identifier) for value, identifier in ordered[:split]]
        self._upper = ordered[split:]
        heapq.heapify(self._lower)
        heapq.heapify(self._upper)
        self._side = {identifier: "lower" for _value, identifier in ordered[:split]} | {
            identifier: "upper" for _value, identifier in ordered[split:]
        }
        self._expired.clear()
        self._lower_size = split
        self._upper_size = len(ordered) - split

    def trim(self, now: datetime) -> None:
        cutoff = now - self._horizon
        while self._arrival and self._arrival[0][0] < cutoff:
            _timestamp, identifier = self._arrival.popleft()
            side = self._side.pop(identifier)
            del self._values[identifier]
            self._expired.add(identifier)
            if side == "lower":
                self._lower_size -= 1
            else:
                self._upper_size -= 1
        self._rebalance()
        self._compact()

    def append(self, timestamp: datetime, value: Decimal) -> None:
        identifier = self._next_id
        self._next_id += 1
        self._values[identifier] = value
        self._prune(self._lower)
        if not self._lower or value <= -self._lower[0][0]:
            heapq.heappush(self._lower, (-value, identifier))
            self._side[identifier] = "lower"
            self._lower_size += 1
        else:
            heapq.heappush(self._upper, (value, identifier))
            self._side[identifier] = "upper"
            self._upper_size += 1
        self._arrival.append((timestamp, identifier))
        self._rebalance()

    def __len__(self) -> int:
        return self._lower_size + self._upper_size

    def median(self) -> Decimal:
        if not self:
            raise ValueError("rolling median is empty")
        self._prune(self._lower)
        self._prune(self._upper)
        lower = -self._lower[0][0]
        if self._lower_size == self._upper_size:
            return (lower + self._upper[0][0]) / 2
        return lower


class _RollingMoments:
    """O(1) rolling population volatility for exact Decimal returns."""

    def __init__(self, horizon: timedelta) -> None:
        self._horizon = horizon
        self._values: deque[tuple[datetime, Decimal]] = deque()
        self._total = Decimal(0)
        self._squares = Decimal(0)

    def trim(self, now: datetime) -> None:
        cutoff = now - self._horizon
        while self._values and self._values[0][0] < cutoff:
            _timestamp, value = self._values.popleft()
            self._total -= value
            self._squares -= value * value

    def append(self, timestamp: datetime, value: Decimal) -> None:
        self._values.append((timestamp, value))
        self._total += value
        self._squares += value * value

    def __len__(self) -> int:
        return len(self._values)

    def volatility(self) -> Decimal:
        if not self._values:
            return Decimal(0)
        count = Decimal(len(self._values))
        variance = (self._squares / count) - (self._total / count) ** 2
        # Decimal cancellation can leave a tiny negative residue.
        return max(variance, Decimal(0)).sqrt()


class TickQualityPipeline:
    """Apply checks 1--6 and completeness-calendar coverage in bounded memory."""

    def __init__(
        self,
        *,
        instrument: str,
        source: str,
        session_boundary: BarBoundary,
        thresholds: QualityThresholds | None = None,
        calendar: LiquidityCalendarLike | None = None,
        calendar_instrument: str | None = None,
        known_at: datetime | None = None,
    ) -> None:
        if not instrument or not source:
            raise ValueError("instrument and source must be non-empty")
        if calendar is not None and known_at is None:
            raise ValueError("known_at is required when a liquidity calendar is supplied")
        self._instrument = instrument
        self._source = source
        self._session_boundary = session_boundary
        self._thresholds = thresholds or QualityThresholds()
        self._calendar = calendar
        self._calendar_instrument = calendar_instrument or f"{source}/{instrument}"
        self._known_at = require_utc(known_at, field="known_at") if known_at is not None else None
        self._spread_history = _RollingMedian(self._thresholds.rolling_horizon)
        self._return_history = _RollingMoments(self._thresholds.rolling_horizon)
        self._gap_history = _RollingMedian(self._thresholds.rolling_horizon)
        self._pending: deque[_PendingTick] = deque()
        self._last_time: datetime | None = None
        self._last_mid: Decimal | None = None
        self._last_identity: tuple[str, ...] | None = None
        self._last_seq: int | None = None
        self._tick_index = -1
        self._input_rows = 0
        self._output_rows = 0
        self._duplicates = 0
        self._eligible = 0
        self._flag_counts: Counter[str] = Counter()
        self._retrospective_counts: Counter[str] = Counter()
        self._calendar_checked: set[date] = set()
        self._calendar_missing: set[date] = set()
        self._finished = False

    def require_calendar_day(self, day: date) -> None:
        """Declare a day required by completeness evaluation, even if it has no ticks."""
        if day in self._calendar_checked:
            return
        self._calendar_checked.add(day)
        if self._calendar is None or self._known_at is None:
            self._calendar_missing.add(day)
            return
        if self._calendar.lookup(self._calendar_instrument, day, known_at=self._known_at) is None:
            self._calendar_missing.add(day)

    def _calendar_day(self, instant: datetime) -> date:
        """Map an instant to its session key without consulting the calendar itself."""
        interval = self._session_boundary(instant)
        return instant.date() if interval is None else interval[0].date()

    def _calendar_gap_expected(self, start: datetime, end: datetime) -> bool | None:
        if self._calendar is None or self._known_at is None:
            return None
        day = self._calendar_day(start)
        final_day = self._calendar_day(end)
        expected_overlap = Decimal(0)
        while day <= final_day:
            self.require_calendar_day(day)
            entry = self._calendar.lookup(self._calendar_instrument, day, known_at=self._known_at)
            if entry is None:
                return None
            for interval_start, interval_end in entry.expected_intervals:
                overlap_start = max(start, require_utc(interval_start))
                overlap_end = min(end, require_utc(interval_end))
                if overlap_end > overlap_start:
                    expected_overlap += Decimal(str((overlap_end - overlap_start).total_seconds()))
            day += timedelta(days=1)
        return expected_overlap > Decimal(str(self._thresholds.gap_threshold.total_seconds()))

    def process(self, item: QualityInput) -> tuple[CleanTickRecord, ...]:
        """Consume one source row and return the now-final leading clean rows."""
        if self._finished:
            raise RuntimeError("quality pipeline is already finished")
        if item.instrument != self._instrument or item.source != self._source:
            raise ValueError("quality input belongs to another source/instrument stream")
        if self._last_seq is not None and item.seq <= self._last_seq:
            raise ValueError("seq must be unique and strictly increasing in source order")
        self._last_seq = item.seq
        self._input_rows += 1
        identity = item.raw_identity or (
            item.ts_event.isoformat(),
            str(item.bid),
            str(item.ask),
            str(item.source_flags),
        )
        if identity == self._last_identity:
            self._duplicates += 1
        self._last_identity = identity
        self._tick_index += 1

        ts_event = require_utc(item.ts_event, field="ts_event")
        session_interval = self._session_boundary(ts_event)
        self.require_calendar_day(
            ts_event.date() if session_interval is None else session_interval[0].date()
        )
        flags: set[str] = {QualityFlag.TS_RECV_IMPUTED}
        eligible = True
        valid_mid = item.bid > 0 and item.ask > item.bid
        if item.bid <= 0:
            flags.add(DataQualityFlag.NONPOSITIVE_BID)
            eligible = False
        if item.ask <= 0:
            flags.add(DataQualityFlag.NONPOSITIVE_ASK)
            eligible = False
        if item.ask == item.bid:
            flags.add(DataQualityFlag.LOCKED_QUOTE)
            eligible = False
        elif item.ask < item.bid:
            flags.add(DataQualityFlag.CROSSED_QUOTE)
            eligible = False

        regressed = self._last_time is not None and ts_event < self._last_time
        if regressed:
            flags.add(DataQualityFlag.TIME_REGRESSION)
            eligible = False
        if session_interval is None:
            flags.add(DataQualityFlag.OUT_OF_SESSION)
            eligible = False

        # Valid quote normalization goes through the one frozen historical/live
        # normalizer.  Invalid quotes cannot instantiate core.Tick by design, but
        # remain clean-layer evidence under the same exact timestamp rule.
        if valid_mid:
            normalized = normalize_tick(
                TickObservation(
                    instrument=item.instrument,
                    ts_event=ts_event,
                    ts_recv=None,
                    bid=item.bid,
                    ask=item.ask,
                )
            )
            ts_recv = normalized.ts_recv
            available_at = normalized.available_at
            flags.update(normalized.quality_flags)
        else:
            ts_recv = ts_event
            available_at = ts_event

        candidate_baseline: Decimal | None = None
        candidate_band: Decimal | None = None
        current_mid = (item.bid + item.ask) / 2 if valid_mid else None

        if self._last_time is not None and not regressed:
            elapsed = Decimal(str((ts_event - self._last_time).total_seconds()))
            # Assess the gap against the market cadence known at its left edge.
            # Trimming at ``ts_event`` first would erase the whole pre-gap window
            # precisely when a long outage needs to be evaluated.
            self._gap_history.trim(self._last_time)
            if (
                elapsed > Decimal(str(self._thresholds.gap_threshold.total_seconds()))
                and len(self._gap_history) >= self._thresholds.minimum_history
                and self._gap_history.median()
                < Decimal(str(self._thresholds.fast_market_median.total_seconds()))
            ):
                expected = self._calendar_gap_expected(self._last_time, ts_event)
                if expected is None:
                    flags.add(DataQualityFlag.GAP_CALENDAR_UNKNOWN)
                elif expected:
                    flags.add(DataQualityFlag.GAP)
            if elapsed > 0:
                self._gap_history.append(ts_event, elapsed)

        if valid_mid and not regressed and current_mid is not None:
            self._spread_history.trim(ts_event)
            self._return_history.trim(ts_event)
            spread = item.ask - item.bid
            if len(self._spread_history) >= self._thresholds.minimum_history:
                rolling_median = self._spread_history.median()
                if (
                    rolling_median > 0
                    and spread > self._thresholds.spread_multiplier * rolling_median
                ):
                    flags.add(DataQualityFlag.SPREAD_OUTLIER)
            else:
                flags.add(DataQualityFlag.QUALITY_WARMUP)

            if self._last_mid is not None:
                tick_return = (current_mid - self._last_mid) / self._last_mid
                if len(self._return_history) >= self._thresholds.minimum_history:
                    volatility = self._return_history.volatility()
                    if abs(tick_return) > self._thresholds.price_sigma * volatility:
                        candidate_baseline = self._last_mid
                        candidate_band = self._thresholds.price_sigma * volatility * self._last_mid
                else:
                    flags.add(DataQualityFlag.QUALITY_WARMUP)
                self._return_history.append(ts_event, tick_return)
            else:
                flags.add(DataQualityFlag.QUALITY_WARMUP)
            self._spread_history.append(ts_event, spread)
            self._last_mid = current_mid

            # A candidate is bad only if a later valid quote returns to the
            # pre-jump normal band within M source ticks.  A sustained jump remains.
            for pending in self._pending:
                if pending.candidate_baseline is None or pending.candidate_band is None:
                    continue
                if self._tick_index - pending.tick_index > self._thresholds.price_reversion_ticks:
                    continue
                if abs(current_mid - pending.candidate_baseline) <= pending.candidate_band:
                    pending.retrospective_flags.add(DataQualityFlag.PRICE_OUTLIER)

        if self._last_time is None or ts_event >= self._last_time:
            self._last_time = ts_event

        record = CleanTickRecord(
            instrument=item.instrument,
            ts_event=ts_event,
            ts_recv=ts_recv,
            available_at=available_at,
            bid=item.bid,
            ask=item.ask,
            bid_size=None,
            ask_size=None,
            source=item.source,
            seq=item.seq,
            source_flags=item.source_flags,
            quality_flags=(),
            retrospective_flags=(),
            eligible_for_bars=eligible,
        )
        self._pending.append(
            _PendingTick(
                tick_index=self._tick_index,
                record=record,
                flags=flags,
                retrospective_flags=set(),
                eligible_for_bars=eligible,
                candidate_baseline=candidate_baseline,
                candidate_band=candidate_band,
            )
        )
        ready: list[CleanTickRecord] = []
        while (
            self._pending
            and self._tick_index - self._pending[0].tick_index
            >= self._thresholds.price_reversion_ticks
        ):
            ready.append(self._emit(self._pending.popleft()))
        return tuple(ready)

    def _emit(self, pending: _PendingTick) -> CleanTickRecord:
        flags = tuple(sorted(pending.flags))
        retrospective_flags = tuple(sorted(pending.retrospective_flags))
        record = CleanTickRecord(
            instrument=pending.record.instrument,
            ts_event=pending.record.ts_event,
            ts_recv=pending.record.ts_recv,
            available_at=pending.record.available_at,
            bid=pending.record.bid,
            ask=pending.record.ask,
            bid_size=pending.record.bid_size,
            ask_size=pending.record.ask_size,
            source=pending.record.source,
            seq=pending.record.seq,
            source_flags=pending.record.source_flags,
            quality_flags=flags,
            retrospective_flags=retrospective_flags,
            eligible_for_bars=pending.eligible_for_bars,
        )
        self._output_rows += 1
        self._eligible += pending.eligible_for_bars
        self._flag_counts.update(flags)
        self._retrospective_counts.update(retrospective_flags)
        return record

    def finish(self) -> tuple[CleanTickRecord, ...]:
        """Finalize the tail after it has seen fewer than M future ticks."""
        if self._finished:
            return ()
        self._finished = True
        return tuple(self._emit(item) for item in self._pending)

    def summary(self) -> QualitySummary:
        """Return final metrics; callers must finish the stream first."""
        if not self._finished:
            raise RuntimeError("finish the quality stream before requesting its summary")
        severe = {
            DataQualityFlag.TIME_REGRESSION,
            DataQualityFlag.LOCKED_QUOTE,
            DataQualityFlag.CROSSED_QUOTE,
            DataQualityFlag.NONPOSITIVE_BID,
            DataQualityFlag.NONPOSITIVE_ASK,
            DataQualityFlag.GAP,
            DataQualityFlag.OUT_OF_SESSION,
        }
        if (
            any(self._flag_counts[str(flag)] for flag in severe)
            or self._retrospective_counts[DataQualityFlag.PRICE_OUTLIER]
        ):
            status = QualityCheckStatus.FAILED
        elif (
            self._calendar_missing
            or self._flag_counts[DataQualityFlag.QUALITY_WARMUP]
            or self._flag_counts[DataQualityFlag.GAP_CALENDAR_UNKNOWN]
        ):
            status = QualityCheckStatus.INDETERMINATE
        else:
            status = QualityCheckStatus.PASSED
        calendar_status = (
            QualityCheckStatus.INDETERMINATE
            if self._calendar_missing or self._calendar is None
            else QualityCheckStatus.PASSED
        )
        return QualitySummary(
            input_rows=self._input_rows,
            output_rows=self._output_rows,
            duplicate_rows=self._duplicates,
            bar_eligible_rows=self._eligible,
            bar_excluded_rows=self._output_rows - self._eligible,
            flag_counts=tuple(sorted(self._flag_counts.items())),
            retrospective_flag_counts=tuple(sorted(self._retrospective_counts.items())),
            quality_status=status,
            calendar_status=calendar_status,
            cross_source_status=QualityCheckStatus.NOT_EVALUABLE,
            calendar_days_checked=len(self._calendar_checked),
            calendar_days_missing=tuple(day.isoformat() for day in sorted(self._calendar_missing)),
        )
