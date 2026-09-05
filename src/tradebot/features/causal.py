"""Pure observed-bar features for engineering replay; no fitted parameters or I/O."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import fmean, stdev

from tradebot.core.timestamps import require_utc
from tradebot.core.types import Bar


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    """Periods count observed bars, including across unfilled market-data gaps."""

    lookbacks: tuple[int, ...] = (8, 16, 32, 64)
    volatility_lookback: int = 32
    atr_lookback: int = 14

    def __post_init__(self) -> None:
        if type(self.lookbacks) is not tuple or not self.lookbacks:
            raise ValueError("lookbacks must be a nonempty tuple")
        if any(type(period) is not int or period < 1 for period in self.lookbacks):
            raise ValueError("lookbacks must contain positive integer periods")
        if tuple(sorted(set(self.lookbacks))) != self.lookbacks:
            raise ValueError("lookbacks must be unique and sorted")
        if type(self.volatility_lookback) is not int or self.volatility_lookback < 2:
            raise ValueError("volatility_lookback must be an integer >= 2 for sample stdev")
        if type(self.atr_lookback) is not int or self.atr_lookback < 1:
            raise ValueError("atr_lookback must be a positive integer")

    @property
    def required_bars(self) -> int:
        """Return complete bars needed, including the previous close for returns/TR."""
        return max(*self.lookbacks, self.volatility_lookback, self.atr_lookback) + 1


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    """Returns are horizon log fractions; volatility is per-bar sample log-return std.

    ATR is quote price; ``spread_to_atr`` is dimensionless. Window/gap values are seconds;
    gaps are diagnostic and are never filled or treated as observed flat prices.
    """

    instrument: str
    ts_close: datetime
    known_at: datetime
    bar_duration_seconds: float
    elapsed_window_seconds: float
    gap_seconds: float
    log_returns: tuple[tuple[int, float], ...]
    volatility: float
    atr: float
    spread_to_atr: float | None
    n_ticks: int | None


_DEFAULT_FEATURE_CONFIG = FeatureConfig()


def validate_bars(bars: Sequence[Bar], *, known_at: datetime) -> None:
    """Reject unknown-at-decision events and incoherent observed-bar sequences.

    The entire supplied sequence is checked, even when a feature needs only its tail.
    Gaps are allowed; mixed instruments/durations, repeats, and overlaps are not.
    """
    require_utc(known_at, field="known_at")
    previous: Bar | None = None
    for bar in bars:
        if max(bar.ts_close, bar.ts_recv, bar.available_at) > known_at:
            raise ValueError("future bar: event/receipt/availability exceeds known_at")
        if previous is not None:
            if bar.instrument != previous.instrument:
                raise ValueError("mixed instrument history")
            if bar.ts_close - bar.ts_open != previous.ts_close - previous.ts_open:
                raise ValueError("mixed bar duration history")
            if bar.ts_open < previous.ts_close or bar.ts_close <= previous.ts_close:
                raise ValueError("bar intervals must be increasing and non-overlapping")
        previous = bar


def _finite(value: float, field: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite in float feature units")
    return value


def _price(value: Decimal) -> float:
    result = _finite(float(value), "price")
    if result <= 0:
        raise ValueError("price underflows positive float feature units")
    return result


def _log_return(current: float, previous: float) -> float:
    ratio = _finite(current / previous, "price ratio")
    if ratio <= 0:
        raise ValueError("price ratio underflows float feature units")
    return _finite(math.log(ratio), "log return")


def compute_features(
    bars: Sequence[Bar], *, known_at: datetime, config: FeatureConfig = _DEFAULT_FEATURE_CONFIG
) -> FeatureSnapshot:
    """Compute trailing returns, sample volatility, mean true range and spread/ATR.

    No annualisation or estimation during warmup occurs. All prices are positive
    finite float values internally; exact Decimal prices remain on the input bars.
    A missing spread or zero ATR yields ``None`` for spread/ATR, never an imputation.
    """
    validate_bars(bars, known_at=known_at)
    if len(bars) < config.required_bars:
        raise ValueError(f"insufficient history: need {config.required_bars} bars")
    window = bars[-config.required_bars :]
    closes = [_price(bar.close) for bar in window]
    log_returns = tuple(
        (period, _log_return(closes[-1], closes[-period - 1])) for period in config.lookbacks
    )
    one_bar_returns = [
        _log_return(closes[index], closes[index - 1])
        for index in range(len(closes) - config.volatility_lookback, len(closes))
    ]
    volatility = _finite(stdev(one_bar_returns), "volatility")
    true_ranges = []
    for index in range(len(window) - config.atr_lookback, len(window)):
        high, low = _price(window[index].high), _price(window[index].low)
        previous_close = closes[index - 1]
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    atr = _finite(fmean(true_ranges), "ATR")
    last = window[-1]
    spread_to_atr = None
    if last.spread_mean is not None:
        spread = _finite(float(last.spread_mean), "spread")
        if last.spread_mean > 0 and spread <= 0:
            raise ValueError("spread underflows float feature units")
        if atr > 0:
            spread_to_atr = _finite(spread / atr, "spread/ATR")
    duration = (last.ts_close - last.ts_open).total_seconds()
    elapsed = (last.ts_close - window[0].ts_open).total_seconds()
    return FeatureSnapshot(
        instrument=last.instrument,
        ts_close=last.ts_close,
        known_at=known_at,
        bar_duration_seconds=duration,
        elapsed_window_seconds=elapsed,
        gap_seconds=elapsed - len(window) * duration,
        log_returns=log_returns,
        volatility=volatility,
        atr=atr,
        spread_to_atr=spread_to_atr,
        n_ticks=last.n_ticks,
    )
