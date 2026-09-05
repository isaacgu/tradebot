from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import stdev

import pytest

from tradebot.core.types import Bar
from tradebot.features.causal import FeatureConfig, compute_features


def _bars() -> list[Bar]:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    return [
        Bar(
            instrument="GBP_USD",
            ts_open=start + timedelta(minutes=i),
            ts_event=start + timedelta(minutes=i + 1),
            ts_recv=start + timedelta(minutes=i + 1, seconds=1),
            open=Decimal(price),
            high=Decimal(price) + 1,
            low=Decimal(price) - 1,
            close=Decimal(price),
            volume=None,
            spread_mean=Decimal("0.5"),
            n_ticks=10 + i,
        )
        for i, price in enumerate(("100", "110", "99", "118.8"))
    ]


def test_known_answer_features_have_explicit_units() -> None:
    bars = _bars()
    config = FeatureConfig(lookbacks=(1, 3), volatility_lookback=3, atr_lookback=2)
    snapshot = compute_features(bars, known_at=bars[-1].available_at, config=config)
    # Last close is 1.2 times its predecessor and 1.188 times the first close.
    assert dict(snapshot.log_returns) == pytest.approx({1: math.log(1.2), 3: math.log(1.188)})
    assert snapshot.volatility == pytest.approx(stdev(map(math.log, (1.1, 0.9, 1.2))))
    # Last two true ranges are max(2, 10, 12)=12 and max(2, 20.8, 18.8)=20.8.
    assert snapshot.atr == pytest.approx(16.4)
    assert snapshot.spread_to_atr == pytest.approx(0.5 / 16.4)
    assert snapshot.n_ticks == 13
    assert snapshot.bar_duration_seconds == 60
    assert snapshot.known_at == bars[-1].available_at
    assert config.required_bars == 4


@pytest.mark.parametrize("lookbacks", [(), (0,), (1, 1), (2, 1), (True,)])
def test_invalid_lookbacks_rejected(lookbacks: tuple[int, ...]) -> None:
    with pytest.raises((ValueError, TypeError)):
        FeatureConfig(lookbacks=lookbacks)


@pytest.mark.parametrize("volatility_lookback,atr_lookback", [(1, 1), (2, 0), (True, 1)])
def test_invalid_periods_rejected(volatility_lookback: int, atr_lookback: int) -> None:
    with pytest.raises((ValueError, TypeError)):
        FeatureConfig(volatility_lookback=volatility_lookback, atr_lookback=atr_lookback)


def test_no_fabricated_warmup() -> None:
    bars = _bars()
    with pytest.raises(ValueError, match="insufficient"):
        compute_features(bars, known_at=bars[-1].available_at, config=FeatureConfig())


def test_all_input_admitted_before_tail_selection() -> None:
    bars = _bars()
    config = FeatureConfig(lookbacks=(1,), volatility_lookback=2, atr_lookback=1)
    now = bars[-1].available_at
    bars[0] = replace(bars[0], ts_recv=now + timedelta(seconds=1))
    with pytest.raises(ValueError, match="future"):
        compute_features(bars, known_at=now, config=config)


@pytest.mark.parametrize("mutation", ["instrument", "duration", "overlap", "order"])
def test_invalid_history_rejected(mutation: str) -> None:
    bars = _bars()
    if mutation == "instrument":
        bars[0] = replace(bars[0], instrument="EUR_USD")
    elif mutation == "duration":
        bars[0] = replace(bars[0], ts_open=bars[0].ts_open - timedelta(seconds=1))
    elif mutation == "overlap":
        bars[1] = replace(
            bars[1],
            ts_open=bars[1].ts_open - timedelta(seconds=1),
            ts_event=bars[1].ts_event - timedelta(seconds=1),
        )
    else:
        bars[0], bars[1] = bars[1], bars[0]
    with pytest.raises(ValueError):
        compute_features(bars, known_at=_bars()[-1].available_at)


def test_missing_spread_and_zero_range_not_fabricated() -> None:
    bars = [
        replace(
            bar,
            open=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            spread_mean=None,
        )
        for bar in _bars()
    ]
    config = FeatureConfig(lookbacks=(1,), volatility_lookback=2, atr_lookback=1)
    snapshot = compute_features(bars, known_at=bars[-1].available_at, config=config)
    assert snapshot.spread_to_atr is None
    assert snapshot.volatility == 0
    assert snapshot.atr == 0


def test_nonfinite_float_price_rejected() -> None:
    bars = _bars()
    huge = Decimal("1e999")
    bars[0] = replace(bars[0], open=huge, high=huge, low=huge, close=huge)
    config = FeatureConfig(lookbacks=(3,), volatility_lookback=2, atr_lookback=1)
    with pytest.raises(ValueError, match="finite"):
        compute_features(bars, known_at=bars[-1].available_at, config=config)


def test_gaps_are_exposed_without_fabricated_bars() -> None:
    original = _bars()
    bars = [
        replace(
            bar,
            ts_open=bar.ts_open + timedelta(minutes=3),
            ts_event=bar.ts_event + timedelta(minutes=3),
            ts_recv=bar.ts_recv + timedelta(minutes=3),
        )
        if index >= 2
        else bar
        for index, bar in enumerate(original)
    ]
    config = FeatureConfig(lookbacks=(3,), volatility_lookback=3, atr_lookback=2)
    snapshot = compute_features(bars, known_at=bars[-1].available_at, config=config)
    continuous = compute_features(original, known_at=original[-1].available_at, config=config)
    assert snapshot.log_returns == continuous.log_returns
    assert snapshot.gap_seconds == 180
    assert snapshot.elapsed_window_seconds == 420


def test_future_suffix_rejected_without_altering_computed_prefix() -> None:
    bars = _bars()
    config = FeatureConfig(lookbacks=(1,), volatility_lookback=2, atr_lookback=1)
    prefix = compute_features(bars[:3], known_at=bars[2].available_at, config=config)
    with pytest.raises(ValueError, match="future"):
        compute_features(bars, known_at=bars[2].available_at, config=config)
    assert compute_features(bars[:3], known_at=bars[2].available_at, config=config) == prefix


@pytest.mark.parametrize(
    "older,current", [("1e-999", "1"), ("1e-300", "1e300"), ("1e300", "1e-300")]
)
def test_price_and_ratio_underflow_or_overflow_fail_explicitly(older: str, current: str) -> None:
    bars = _bars()
    for index, value in ((2, older), (3, current)):
        price = Decimal(value)
        bars[index] = replace(bars[index], open=price, high=price, low=price, close=price)
    config = FeatureConfig(lookbacks=(1,), volatility_lookback=2, atr_lookback=1)
    with pytest.raises(ValueError, match=r"underflow|finite"):
        compute_features(bars, known_at=bars[-1].available_at, config=config)
