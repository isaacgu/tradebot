"""Bar-resolution outcomes never pretend to establish an intrabar timestamp."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, Inexact, Rounded, localcontext
from typing import cast

import pytest

from tradebot.backtest.bracket_bars import (
    BarBracketExit,
    QuoteRange,
    SyntheticQuoteBar,
    match_synthetic_bracket_bar,
)
from tradebot.backtest.market import MarketModel
from tradebot.backtest.orders import OrderModel
from tradebot.core.types import OrderRequest, OrderType, Side
from tradebot.research.report import canonical_bytes

_START = datetime(2024, 1, 8, 12, tzinfo=UTC)


def _order(side: Side = Side.BUY, **changes: object) -> OrderRequest:
    return replace(
        OrderRequest(
            client_order_id="bar-order",
            instrument="Synthetic/EURUSD",
            side=side,
            qty=Decimal("10000"),
            order_type=OrderType.MARKET,
            strategy_id="known-answer",
            run_id="bar-fixture",
            config_hash="known-config",
            git_sha="UNCOMMITTED",
            stop_loss=Decimal("1.09900") if side is Side.BUY else Decimal("1.10100"),
            take_profit=Decimal("1.10100") if side is Side.BUY else Decimal("1.09900"),
        ),
        **changes,  # type: ignore[arg-type]
    )


def _range(open: str, high: str, low: str, close: str) -> QuoteRange:
    return QuoteRange(
        open=Decimal(open), high=Decimal(high), low=Decimal(low), close=Decimal(close)
    )


def _bar(**changes: object) -> SyntheticQuoteBar:
    return replace(
        SyntheticQuoteBar(
            instrument="Synthetic/EURUSD",
            ts_open=_START + timedelta(minutes=1),
            ts_close=_START + timedelta(minutes=2),
            available_at=_START + timedelta(minutes=3),
            bid=_range("1.10000", "1.10200", "1.09800", "1.10050"),
            ask=_range("1.10020", "1.10220", "1.09820", "1.10070"),
        ),
        **changes,  # type: ignore[arg-type]
    )


def _model(touch: bool = False) -> OrderModel:
    return OrderModel(
        market=MarketModel(
            latency=timedelta(milliseconds=150),
            slippage_price=Decimal("0.00002"),
            max_full_fill_qty=Decimal("10000"),
        ),
        price_tick=Decimal("0.00010"),
        limit_on_touch=touch,
    )


def _match(
    order: OrderRequest | None = None,
    bar: SyntheticQuoteBar | None = None,
    *,
    active_at: datetime = _START,
    touch: bool = False,
) -> BarBracketExit | None:
    return match_synthetic_bracket_bar(
        _order() if order is None else order,
        active_at=active_at,
        bar=_bar() if bar is None else bar,
        model=_model(touch),
    )


@pytest.mark.parametrize("side,price", [(Side.BUY, "1.09898"), (Side.SELL, "1.10102")])
def test_both_touched_is_adverse_first_with_no_fake_timestamp(side: Side, price: str) -> None:
    result = _match(_order(side))
    assert result is not None
    assert result.reason == "STOP_LOSS"
    assert result.price == Decimal(price)
    assert result.both_legs_reached
    assert result.available_at == _bar().available_at
    assert result.assumptions == ("BAR_PATH_ADVERSE_FIRST", "INTRABAR_EXECUTION_TIME_UNKNOWN")


@pytest.mark.parametrize("side,price", [(Side.BUY, "1.09798"), (Side.SELL, "1.10222")])
def test_gap_stop_uses_worse_opening_quote(side: Side, price: str) -> None:
    if side is Side.BUY:
        bar = _bar(
            bid=_range("1.09800", "1.10200", "1.09700", "1.10000"),
            ask=_range("1.09820", "1.10220", "1.09720", "1.10020"),
        )
    else:
        bar = _bar(
            bid=_range("1.10200", "1.10300", "1.09800", "1.10000"),
            ask=_range("1.10220", "1.10320", "1.09820", "1.10020"),
        )
    result = _match(_order(side), bar)
    assert result is not None and result.price == Decimal(price)


@pytest.mark.parametrize("side,price", [(Side.BUY, "1.10100"), (Side.SELL, "1.09900")])
def test_single_take_profit_never_improves_past_limit(side: Side, price: str) -> None:
    result = _match(_order(side, stop_loss=None))
    assert result is not None
    assert result.reason == "TAKE_PROFIT" and result.price == Decimal(price)
    assert not result.both_legs_reached


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_single_stop_and_neither_bracket_reached(side: Side) -> None:
    result = _match(_order(side, take_profit=None))
    assert result is not None and not result.both_legs_reached
    assert result.reason == "STOP_LOSS"
    if side is Side.BUY:
        far = _order(side, stop_loss=Decimal("1.0"), take_profit=Decimal("1.2"))
    else:
        far = _order(side, stop_loss=Decimal("1.2"), take_profit=Decimal("1.0"))
    assert _match(far) is None


def test_touch_only_sensitivity_is_labelled_and_needs_executable_side() -> None:
    order = _order(stop_loss=None, take_profit=Decimal("1.10100"))
    bar = _bar(
        bid=_range("1.10000", "1.10103", "1.09800", "1.10050"),
        ask=_range("1.10002", "1.10105", "1.09802", "1.10052"),
    )
    assert _match(order, bar) is None
    result = _match(order, bar, touch=True)
    assert result is not None and "LIMIT_TOUCH_FILLS" in result.assumptions
    wide = _bar(
        bid=_range("1.10000", "1.10050", "1.09800", "1.10050"),
        ask=_range("1.10020", "1.10110", "1.09820", "1.10070"),
    )
    assert _match(order, wide, touch=True) is None


def test_short_take_profit_touch_and_wide_spread_checks() -> None:
    order = _order(Side.SELL, stop_loss=None, take_profit=Decimal("1.09900"))
    bar = _bar(
        bid=_range("1.10000", "1.10200", "1.09895", "1.10050"),
        ask=_range("1.10002", "1.10202", "1.09897", "1.10052"),
    )
    assert _match(order, bar) is None
    assert _match(order, bar, touch=True) is not None
    wide = _bar(
        bid=_range("1.10000", "1.10200", "1.09890", "1.10050"),
        ask=_range("1.10020", "1.10220", "1.09920", "1.10070"),
    )
    assert _match(order, wide) is None


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_unrelated_bid_ask_extrema_cannot_manufacture_simultaneous_limit_fill(side: Side) -> None:
    if side is Side.BUY:
        order = _order(side, stop_loss=None, take_profit=Decimal("1.10100"))
        # Ask high satisfies sell trade-through, but at a different instant
        # from bid high. Neither executable extreme proves both conditions.
        bar = _bar(
            bid=_range("1.10000", "1.10103", "1.09800", "1.10050"),
            ask=_range("1.10020", "1.10120", "1.09820", "1.10070"),
        )
    else:
        order = _order(side, stop_loss=None, take_profit=Decimal("1.09900"))
        bar = _bar(
            bid=_range("1.10000", "1.10200", "1.09880", "1.10050"),
            ask=_range("1.10020", "1.10220", "1.09897", "1.10070"),
        )
    assert _match(order, bar) is None


def test_decimal_precision_and_deterministic_output() -> None:
    expected = canonical_bytes(_match())
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = context.traps[Rounded] = True
        assert canonical_bytes(_match()) == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"open": Decimal("0")},
        {"low": Decimal("NaN")},
        {"high": Decimal("1.0")},
        {"low": Decimal("1.2")},
        {"close": 1.1},
    ],
)
def test_invalid_quote_range_rejected(changes: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(_bar().bid, **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"instrument": 1},
        {"ts_open": "invalid"},
        {"ts_open": _START.replace(tzinfo=None)},
        {"ts_open": _START + timedelta(minutes=2)},
        {"available_at": _START},
        {"bid": "invalid"},
        {"ask": _range("1.10000", "1.10200", "1.09800", "1.10050")},
    ],
)
def test_invalid_bar_rejected(changes: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        _bar(**changes)


def test_invalid_matching_scope_or_activation_is_rejected() -> None:
    for order in (_order(stop_loss=None, take_profit=None), _order(instrument="FBS-Demo/EURUSD")):
        with pytest.raises(ValueError):
            _match(order)
    with pytest.raises(ValueError, match="instrument"):
        _match(bar=_bar(instrument="Synthetic/GBPUSD"))
    with pytest.raises(ValueError, match="already be active"):
        _match(active_at=_bar().ts_open)
    with pytest.raises(TypeError, match="active_at"):
        _match(active_at=cast(datetime, "invalid"))
    with pytest.raises(TypeError, match="bar"):
        match_synthetic_bracket_bar(
            _order(), active_at=_START, bar=cast(SyntheticQuoteBar, None), model=_model()
        )
