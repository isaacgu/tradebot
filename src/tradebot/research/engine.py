"""Stream observable closed bars through the existing bus and strategy protocol."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tradebot.core.bus import EventBus
from tradebot.core.clock import SimClock
from tradebot.core.ports import StrategyContext
from tradebot.core.types import Bar, Forecast
from tradebot.features.causal import FeatureSnapshot
from tradebot.research.feed import ReplayBar
from tradebot.strategies.momentum import MomentumConfig, MomentumStrategy


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    """Explicit series selection; timeframe is elapsed seconds per closed bar."""

    instruments: tuple[str, ...]
    timeframe_seconds: int
    momentum: MomentumConfig = field(default_factory=MomentumConfig)

    def __post_init__(self) -> None:
        if not self.instruments or tuple(sorted(set(self.instruments))) != self.instruments:
            raise ValueError("instruments must be a nonempty, sorted, unique tuple")
        for instrument in self.instruments:
            parts = instrument.split("/")
            if len(parts) != 2 or not parts[0] or parts[1] not in {"GBPUSD", "EURUSD"}:
                raise ValueError("research series must be venue-qualified GBPUSD or EURUSD")
        if len({instrument.split("/")[0] for instrument in self.instruments}) != 1:
            raise ValueError("a replay must select exactly one venue")
        if type(self.timeframe_seconds) is not int or self.timeframe_seconds < 1:
            raise ValueError("timeframe_seconds must be a positive integer")


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Audit one observation and its forecast or reason for abstaining."""

    instrument: str
    source: str
    seq: int
    bar_open: datetime
    bar_close: datetime
    available_at: datetime
    decision_at: datetime
    quality_flags: tuple[str, ...]
    status: str
    reason: str
    features: FeatureSnapshot | None
    forecast: Forecast | None


@dataclass(frozen=True, slots=True)
class _DecisionClock:
    """Give handlers the current timestamp without the driver's advance capability."""

    timestamp: datetime

    def now(self) -> datetime:
        return self.timestamp


def iter_decisions(records: Iterable[ReplayBar], config: ReplayConfig) -> Iterator[DecisionRecord]:
    """Yield decisions in strict availability/source/sequence order with bounded state.

    Each instrument gets its own strategy state. The last record must be consumed
    before a run can be considered complete: feeds verify snapshot hashes at EOF.
    """
    strategies = {name: MomentumStrategy(name, config.momentum) for name in config.instruments}
    seen: set[str] = set()
    previous_key: tuple[datetime, str, int, str] | None = None
    last_sequences: dict[tuple[str, str], int] = {}
    sources: dict[str, str] = {}
    clock: SimClock | None = None
    bus: EventBus | None = None
    emitted: list[Forecast] = []

    def on_bar(bar: Bar) -> None:
        if clock is None or bus is None:
            raise RuntimeError("replay clock and bus are not initialized")
        context = StrategyContext(_DecisionClock(clock.now()))
        for forecast in strategies[bar.instrument].on_bar(bar, context):
            bus.publish(forecast)

    for record in records:
        bar = record.bar
        if bar.instrument not in strategies:
            raise ValueError(f"unconfigured series: {bar.instrument}")
        if bar.ts_close - bar.ts_open != timedelta(seconds=config.timeframe_seconds):
            raise ValueError("bar duration differs from configured timeframe")
        if sources.setdefault(bar.instrument, record.source) != record.source:
            raise ValueError("a series cannot merge multiple sources in one replay")
        key = record.key
        if previous_key is not None and key <= previous_key:
            raise ValueError("replay keys must be strictly increasing")
        series_key = (record.source, bar.instrument)
        if record.seq <= last_sequences.get(series_key, -1):
            raise ValueError("source sequence regressed or was duplicated")
        previous_key = key
        last_sequences[series_key] = record.seq
        seen.add(bar.instrument)
        if clock is None:
            clock = SimClock(bar.available_at)
            bus = EventBus(clock)
            bus.subscribe(Bar, on_bar)
            bus.subscribe(Forecast, emitted.append)
        else:
            clock.advance_to(bar.available_at)
        if bus is None:
            raise RuntimeError("replay bus is not initialized")
        emitted.clear()
        bus.publish(bar)
        decision = strategies[bar.instrument].last_decision
        if decision is None:
            raise RuntimeError("strategy did not record a decision")
        if len(emitted) > 1 or (emitted[0] if emitted else None) != decision.forecast:
            raise RuntimeError("strategy audit and bus forecast disagree")
        yield DecisionRecord(
            instrument=bar.instrument,
            source=record.source,
            seq=record.seq,
            bar_open=bar.ts_open,
            bar_close=bar.ts_close,
            available_at=bar.available_at,
            decision_at=clock.now(),
            quality_flags=bar.quality_flags,
            status=decision.status,
            reason=decision.reason,
            features=decision.features,
            forecast=decision.forecast,
        )
    missing = set(config.instruments) - seen
    if missing:
        raise ValueError(f"snapshot contained no records for: {', '.join(sorted(missing))}")
