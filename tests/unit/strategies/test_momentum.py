from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradebot.core.ports import BarStrategy, StrategyContext
from tradebot.core.types import Bar, Forecast
from tradebot.features.causal import FeatureConfig
from tradebot.strategies.momentum import MomentumConfig, MomentumStrategy


@dataclass(frozen=True)
class ReadOnlyClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


def _bar(index: int, *, price: str | None = None) -> Bar:
    start = datetime(2026, 1, 5, tzinfo=UTC) + timedelta(minutes=index)
    close = Decimal(price) if price is not None else Decimal(100 + index + index % 3)
    return Bar(
        instrument="GBP_USD",
        ts_open=start,
        ts_event=start + timedelta(minutes=1),
        ts_recv=start + timedelta(minutes=1, milliseconds=100),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=None,
        spread_mean=Decimal("0.10"),
        n_ticks=42,
    )


def _strategy() -> MomentumStrategy:
    return MomentumStrategy(
        "GBP_USD",
        MomentumConfig(
            features=FeatureConfig(lookbacks=(1, 3), volatility_lookback=3, atr_lookback=2),
            forecast_scale=2.0,
        ),
    )


def _step(strategy: BarStrategy, bar: Bar) -> Sequence[Forecast]:
    clock = ReadOnlyClock(bar.available_at + timedelta(seconds=3))
    result = strategy.on_bar(bar, StrategyContext(clock))
    assert clock.now() == bar.available_at + timedelta(seconds=3)
    return result


def test_warmup_formula_and_decision_time() -> None:
    strategy = _strategy()
    for i in range(3):
        assert not _step(strategy, _bar(i))
        assert strategy.last_decision.status == "warmup"
    (forecast,) = _step(strategy, _bar(3))
    features = strategy.last_decision.features
    assert features is not None
    expected = sum(
        value / (features.volatility * math.sqrt(horizon))
        for horizon, value in features.log_returns
    )
    assert forecast.value == pytest.approx(expected)
    assert forecast.confidence is None
    assert forecast.ts_event == _bar(3).available_at + timedelta(seconds=3)
    assert forecast.ts_recv == forecast.ts_event
    assert forecast.source_event_id
    assert dict(forecast.meta)["forecast_scale_calibrated"] == "false"


def test_prefix_invariant_under_changed_future_suffix() -> None:
    left, right = _strategy(), _strategy()
    prefix_left = [_step(left, _bar(i)) for i in range(10)]
    prefix_right = [_step(right, _bar(i)) for i in range(10)]
    for i in range(10, 20):
        _step(left, _bar(i, price="99"))
        _step(right, _bar(i, price="999"))
    assert prefix_left == prefix_right


@pytest.mark.parametrize(
    "flags,spread,reason",
    [
        (("CLOCK_SKEW",), Decimal("0.1"), "quality_flags"),
        (("UNKNOWN_FLAG",), Decimal("0.1"), "quality_flags"),
        ((), None, "missing_spread"),
    ],
)
def test_unusable_bar_resets_history(
    flags: tuple[str, ...], spread: Decimal | None, reason: str
) -> None:
    strategy = _strategy()
    for i in range(6):
        _step(strategy, _bar(i))
    bad = replace(_bar(6), quality_flags=flags, spread_mean=spread)
    assert not _step(strategy, bad)
    assert strategy.last_decision.status == "suppressed"
    assert strategy.last_decision.reason == reason
    assert strategy.state()["history"] == []
    for i in range(7, 10):
        assert not _step(strategy, _bar(i))
        assert strategy.last_decision.status == "warmup"
    assert _step(strategy, _bar(10))


def test_zero_volatility_abstains() -> None:
    strategy = _strategy()
    for i in range(5):
        assert not _step(strategy, _bar(i, price="100"))
    assert strategy.last_decision.reason == "zero_volatility"
    assert strategy.last_decision.features is not None


def test_future_input_and_duplicate_cannot_mutate_state() -> None:
    strategy = _strategy()
    for i in range(4):
        _step(strategy, _bar(i))
    before = strategy.state()
    before_decision = strategy.last_decision
    with pytest.raises(ValueError, match="future"):
        strategy.on_bar(_bar(5), StrategyContext(ReadOnlyClock(_bar(4).available_at)))
    with pytest.raises(ValueError, match=r"increasing|overlap"):
        _step(strategy, _bar(3))
    assert strategy.state() == before
    assert strategy.last_decision == before_decision


@pytest.mark.parametrize("split", [0, 2, 4, 8])
def test_checkpoint_roundtrip_identical_resumed_outputs(split: int) -> None:
    original, resumed = _strategy(), _strategy()
    for i in range(split):
        _step(original, _bar(i))
    checkpoint = json.loads(json.dumps(original.state(), allow_nan=False))
    resumed.restore(checkpoint)
    assert resumed.state() == original.state()
    assert resumed.last_decision == original.last_decision
    for i in range(split, 20):
        assert _step(resumed, _bar(i)) == _step(original, _bar(i))
    assert len(resumed.state()["history"]) == 4  # type: ignore[arg-type]


def test_suppressed_checkpoint_preserves_ordering_anchor() -> None:
    original, resumed = _strategy(), _strategy()
    bad = replace(_bar(4), quality_flags=("BAD",))
    _step(original, bad)
    resumed.restore(json.loads(json.dumps(original.state())))
    assert resumed.last_decision == original.last_decision
    with pytest.raises(ValueError, match=r"increasing|overlap"):
        _step(resumed, bad)
    assert not _step(resumed, _bar(5))


@pytest.mark.parametrize(
    "field,value", [("version", 99), ("instrument", "EUR_USD"), ("config", {}), ("history", [None])]
)
def test_invalid_checkpoint_is_atomic(field: str, value: object) -> None:
    strategy = _strategy()
    _step(strategy, _bar(0))
    before = strategy.state()
    bad = dict(before)
    bad[field] = value
    with pytest.raises((TypeError, ValueError)):
        strategy.restore(bad)
    assert strategy.state() == before


def test_mixed_instrument_rejected_without_mutation() -> None:
    strategy = _strategy()
    before = strategy.state()
    with pytest.raises(ValueError, match="instrument"):
        _step(strategy, replace(_bar(0), instrument="EUR_USD"))
    assert strategy.state() == before


@pytest.mark.parametrize("mutation", ["oversize", "dirty", "future", "mixed", "anchor"])
def test_checkpoint_rejects_incoherent_history(mutation: str) -> None:
    strategy = _strategy()
    for i in range(5):
        _step(strategy, _bar(i))
    before = strategy.state()
    checkpoint = json.loads(json.dumps(before))
    if mutation == "oversize":
        checkpoint["history"].append(checkpoint["history"][-1])
    elif mutation == "dirty":
        checkpoint["history"][0]["quality_flags"] = ["NEW_FLAG"]
    elif mutation == "future":
        checkpoint["history"][0]["ts_recv"] = _bar(10).ts_recv.isoformat()
    elif mutation == "mixed":
        checkpoint["history"][0]["instrument"] = "EUR_USD"
    else:
        checkpoint["last_bar"]["n_ticks"] = 100
    with pytest.raises(ValueError):
        strategy.restore(checkpoint)
    assert strategy.state() == before


def test_returned_checkpoint_cannot_mutate_strategy() -> None:
    strategy = _strategy()
    _step(strategy, _bar(0))
    before = strategy.state()
    exposed = strategy.state()
    exposed_history = exposed["history"]
    assert isinstance(exposed_history, list)
    exposed_history.clear()
    assert strategy.state() == before


@pytest.mark.parametrize("scale", [0.0, -1.0, math.inf, math.nan])
def test_invalid_scale_rejected(scale: float) -> None:
    with pytest.raises(ValueError, match="forecast_scale"):
        MomentumConfig(forecast_scale=scale)


def test_forecast_is_capped_without_changing_unscaled_components() -> None:
    config = MomentumConfig(
        features=FeatureConfig(lookbacks=(1, 3), volatility_lookback=3, atr_lookback=2),
        forecast_scale=1_000.0,
    )
    strategy = MomentumStrategy("GBP_USD", config)
    for index in range(4):
        forecasts = _step(strategy, _bar(index))
    assert forecasts[0].value == 20.0
    assert float(dict(forecasts[0].meta)["raw_forecast"]) > 20
