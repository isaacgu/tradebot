"""Explicit adverse-first bracket sensitivity on invented bid/ask OHLC bars.

This does not reconstruct ticks from midpoint OHLC or invent an intrabar event
timestamp. It can only evaluate a bracket already active before the whole bar.
The result becomes available at the supplied bar availability, not at its open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Literal, cast

from tradebot.backtest.costs import _exact_context
from tradebot.backtest.market import _decimal_parts, _exact_price
from tradebot.backtest.orders import OrderModel, _validate_order
from tradebot.core.timestamps import require_utc
from tradebot.core.types import OrderRequest, Side


@dataclass(frozen=True, slots=True, kw_only=True)
class QuoteRange:
    """One quote side's supplied OHLC, not an inferred executable tick path."""

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        for field in ("open", "high", "low", "close"):
            value = getattr(self, field)
            _decimal_parts(value, field)
            if value <= 0:
                raise ValueError("OHLC prices must be positive")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC range must contain open and close")


@dataclass(frozen=True, slots=True, kw_only=True)
class SyntheticQuoteBar:
    """Separate supplied bid/ask ranges with explicit observability."""

    instrument: str
    ts_open: datetime
    ts_close: datetime
    available_at: datetime
    bid: QuoteRange
    ask: QuoteRange

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str):
            raise TypeError("instrument must be str")
        for field in ("ts_open", "ts_close", "available_at"):
            value = getattr(self, field)
            if not isinstance(value, datetime):
                raise TypeError(f"{field} must be datetime")
            object.__setattr__(self, field, require_utc(value, field=field))
        if self.ts_open >= self.ts_close or self.available_at < self.ts_close:
            raise ValueError("bar requires open < close <= availability")
        if not isinstance(self.bid, QuoteRange) or not isinstance(self.ask, QuoteRange):
            raise TypeError("bid and ask must be QuoteRange")
        if any(
            getattr(self.ask, field) <= getattr(self.bid, field)
            for field in ("open", "high", "low", "close")
        ):
            raise ValueError("ask OHLC must be strictly above corresponding bid OHLC")


@dataclass(frozen=True, slots=True, kw_only=True)
class BarBracketExit:
    """Bar-resolution outcome; exact intrabar execution time is unknown."""

    reason: Literal["STOP_LOSS", "TAKE_PROFIT"]
    price: Decimal
    available_at: datetime
    both_legs_reached: bool
    assumptions: tuple[str, ...]


def match_synthetic_bracket_bar(
    order: OrderRequest,
    *,
    active_at: datetime,
    bar: SyntheticQuoteBar,
    model: OrderModel,
) -> BarBracketExit | None:
    """Evaluate a fully pre-existing bracket, choosing SL when both can hit.

    Gap stops execute at the adverse opening quote; otherwise at the stop plus
    adverse slippage. TP requires executable-side trade-through and range/slip
    eligibility, and receives no improvement beyond the limit. This deliberately
    labels a bar path assumption; only observed ticks can resolve actual order.
    """
    _validate_order(order, model)
    if not isinstance(bar, SyntheticQuoteBar):
        raise TypeError("bar must be SyntheticQuoteBar")
    if not isinstance(active_at, datetime):
        raise TypeError("active_at must be datetime")
    active_at = require_utc(active_at, field="active_at")
    if bar.instrument != order.instrument:
        raise ValueError("bar instrument must match the order")
    if active_at >= bar.ts_open:
        raise ValueError("bracket must already be active before this complete bar opens")
    if order.stop_loss is None and order.take_profit is None:
        raise ValueError("order must specify at least one bracket leg")

    stop_hit = False
    target_hit = False
    stop_quote: Decimal | None = None
    with localcontext(_exact_context()):
        increment = Decimal(0) if model.limit_on_touch else model.price_tick
        if order.side is Side.BUY:
            if order.stop_loss is not None:
                stop_hit = bar.bid.low <= order.stop_loss
                stop_quote = min(bar.bid.open, order.stop_loss)
            if order.take_profit is not None:
                target_hit = (
                    # Separate bid/ask extrema may occur at different times.
                    # Require the executable extreme to satisfy both rules;
                    # do not combine unrelated extrema into a fictional fill.
                    bar.bid.high >= order.take_profit + increment
                    and bar.bid.high - model.market.slippage_price >= order.take_profit
                )
        else:
            if order.stop_loss is not None:
                stop_hit = bar.ask.high >= order.stop_loss
                stop_quote = max(bar.ask.open, order.stop_loss)
            if order.take_profit is not None:
                target_hit = (
                    bar.ask.low <= order.take_profit - increment
                    and bar.ask.low + model.market.slippage_price <= order.take_profit
                )
    if not stop_hit and not target_hit:
        return None
    assumptions: tuple[str, ...] = ("BAR_PATH_ADVERSE_FIRST", "INTRABAR_EXECUTION_TIME_UNKNOWN")
    if model.limit_on_touch:
        assumptions += ("LIMIT_TOUCH_FILLS",)
    if stop_hit:
        # stop_hit implies a supplied stop and therefore a computed quote.
        price = _exact_price(
            cast(Decimal, stop_quote),
            model.market.slippage_price,
            Side.SELL if order.side is Side.BUY else Side.BUY,
        )
        reason: Literal["STOP_LOSS", "TAKE_PROFIT"] = "STOP_LOSS"
    else:
        price = cast(Decimal, order.take_profit)
        reason = "TAKE_PROFIT"
    return BarBracketExit(
        reason=reason,
        price=price,
        available_at=bar.available_at,
        both_legs_reached=stop_hit and target_hit,
        assumptions=assumptions,
    )
