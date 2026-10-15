"""Conservative, synthetic-only tick matching for one bounded full-fill order.

This is an engineering model, not a broker adapter or a real-data backtester.
The market helper remains unchanged. This state machine adds limit, stop,
stop-limit and server-resident bracket legs without silently inferring costs,
liquidity, risk, sessions or a broker's order-trigger convention.

Venue time controls execution; receipt/availability are preserved on fills, not
added to execution latency. The complete feed is checked before returning any
result, including after both bracket legs have resolved. Callers must still
admit returned fills by availability and prevent duplicate accounting.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Literal

from tradebot.backtest.costs import _exact_context
from tradebot.backtest.market import MarketModel, _decimal_parts, _exact_price, _identity_value
from tradebot.core.timestamps import require_utc
from tradebot.core.types import Fill, OrderRequest, OrderType, Side, Tick, TimeInForce

ExecutionReason = Literal["MARKET", "LIMIT", "STOP", "STOP_LIMIT", "STOP_LOSS", "TAKE_PROFIT"]
_ENTRY_REASONS: dict[OrderType, ExecutionReason] = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP: "STOP",
    OrderType.STOP_LIMIT: "STOP_LIMIT",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderModel:
    """All assumptions explicit; touch sensitivity is always labelled optimistic.

    ``market`` supplies transport latency, constant adverse slippage and an
    invented full-fill size envelope. ``price_tick`` is an explicit fixture
    increment, not an inferred broker contract. Brackets are resident at the
    venue and activate atomically after entry; their exits incur slippage but
    no second transport latency. See ADR-0015 for the deliberate limitations.
    """

    market: MarketModel
    price_tick: Decimal
    limit_on_touch: bool

    def __post_init__(self) -> None:
        if not isinstance(self.market, MarketModel):
            raise TypeError("market must be MarketModel")
        _decimal_parts(self.price_tick, "price_tick")
        if self.price_tick <= 0:
            raise ValueError("price_tick must be positive")
        if type(self.limit_on_touch) is not bool:
            raise TypeError("limit_on_touch must be bool")


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderMatch:
    """One exact-price fill and the source quote that made it eligible."""

    fill: Fill
    tick: Tick
    tick_index: int
    reason: ExecutionReason
    adverse_price_from_quote: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderExecution:
    """Measured matching outcome; no gate, economic or account claims."""

    submitted_at: datetime
    eligible_after: datetime
    ticks_processed: int
    entry: OrderMatch | None
    exit: OrderMatch | None
    stop_trigger_tick_index: int | None
    optimistic_assumptions: tuple[str, ...]


def _validate_order(order: OrderRequest, model: OrderModel) -> None:
    if not isinstance(order, OrderRequest):
        raise TypeError("order must be OrderRequest")
    if not isinstance(model, OrderModel):
        raise TypeError("model must be OrderModel")
    if (
        re.fullmatch(r"Synthetic/[A-Z]{6}", order.instrument) is None
        or order.instrument[-6:-3] == order.instrument[-3:]
    ):
        raise ValueError("only Synthetic/<distinct uppercase FX currencies> is supported")
    if order.time_in_force is not TimeInForce.GTC:
        raise ValueError("only GTC is supported")
    for field in ("qty", "limit_price", "stop_price", "stop_loss", "take_profit"):
        value = getattr(order, field)
        if value is not None:
            _decimal_parts(value, field)
    if order.qty > model.market.max_full_fill_qty:
        raise ValueError("qty exceeds the explicitly configured synthetic full-fill size")
    if order.stop_loss is not None and order.take_profit is not None:
        if (order.side is Side.BUY and order.stop_loss >= order.take_profit) or (
            order.side is Side.SELL and order.stop_loss <= order.take_profit
        ):
            raise ValueError("bracket stop_loss and take_profit must have directional ordering")


def _stop_reached(side: Side, tick: Tick, stop: Decimal) -> bool:
    # A buy consumes the ask; a sell consumes the bid. The other side of the
    # spread cannot trigger an executable stop in this explicit convention.
    return tick.ask >= stop if side is Side.BUY else tick.bid <= stop


def _limit_fill(side: Side, tick: Tick, limit: Decimal, model: OrderModel) -> Decimal | None:
    """Require trade-through plus executable quote/slip within the limit.

    SPEC 6.2's buy trade-through uses bid; it is insufficient by itself when
    the ask remains above the limit. The second condition excludes that
    optimistic wide-spread case. Fill at the limit, foregoing improvement,
    never beyond it. The actual quote-to-fill difference is retained as cost.
    """
    with localcontext(_exact_context()):
        increment = Decimal(0) if model.limit_on_touch else model.price_tick
        if side is Side.BUY:
            through = tick.bid <= limit - increment
            executable = tick.ask + model.market.slippage_price <= limit
        else:
            through = tick.ask >= limit + increment
            executable = tick.bid - model.market.slippage_price >= limit
    return limit if through and executable else None


def _make_match(
    order: OrderRequest,
    *,
    model: OrderModel,
    submitted_at: datetime,
    decision_available_at: datetime,
    tick: Tick,
    tick_index: int,
    side: Side,
    price: Decimal,
    reason: ExecutionReason,
) -> OrderMatch:
    identity = {
        "schema": "synthetic-order-match-v1",
        "order": asdict(order),
        "model": asdict(model),
        "submitted_at": submitted_at,
        "decision_available_at": decision_available_at,
        "tick": asdict(tick),
        "tick_index": tick_index,
        "reason": reason,
        "price": price,
    }
    encoded = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_identity_value
    ).encode("utf-8")
    exit_leg = reason in ("STOP_LOSS", "TAKE_PROFIT")
    with localcontext(_exact_context()):
        adverse = price - tick.ask if side is Side.BUY else tick.bid - price
    return OrderMatch(
        fill=Fill(
            broker_fill_id="synthetic-order-" + hashlib.sha256(encoded).hexdigest(),
            client_order_id=order.client_order_id + ("/" + reason if exit_leg else ""),
            instrument=order.instrument,
            side=side,
            qty=order.qty,
            price=price,
            strategy_id=order.strategy_id,
            run_id=order.run_id,
            config_hash=order.config_hash,
            git_sha=order.git_sha,
            ts_event=tick.ts_event,
            ts_recv=tick.ts_recv,
            available_at=tick.available_at,
            quality_flags=tick.quality_flags,
        ),
        tick=tick,
        tick_index=tick_index,
        reason=reason,
        adverse_price_from_quote=adverse,
    )


def match_synthetic_order(
    order: OrderRequest,
    *,
    submitted_at: datetime,
    decision_available_at: datetime,
    ticks: Iterable[Tick],
    model: OrderModel,
) -> OrderExecution:
    """Match a GTC parent and optional SL/TP; fully validate through true EOF.

    Parent activation is strictly after submission plus latency. Stop orders
    trigger against the first eligible executable-side quote and fill at that
    quote with adverse slippage: a gap never receives the missing stop price.
    Stop-limit activation is sticky even if the market later recedes from its
    stop. A limit can fill on its triggering quote if both conservative limit
    conditions hold. There are no partial fills, cancellation or expiry here.

    SL/TP are server-resident and evaluated after entry on the same quote and
    each later input ordinal, including equal venue timestamps. A gapped entry
    can therefore immediately hit its SL. Stop exits take precedence; the
    sibling leg is cancelled. No returned result attests real-data admission.
    """
    _validate_order(order, model)
    if not isinstance(submitted_at, datetime) or not isinstance(decision_available_at, datetime):
        raise TypeError("submitted_at and decision_available_at must be datetime")
    submitted_at = require_utc(submitted_at, field="submitted_at")
    decision_available_at = require_utc(decision_available_at, field="decision_available_at")
    if submitted_at < decision_available_at:
        raise ValueError("order submission cannot precede decision availability")
    try:
        eligible_after = submitted_at + model.market.latency
    except OverflowError as error:
        raise ValueError("submission plus latency exceeds the datetime range") from error

    entry: OrderMatch | None = None
    exit_fill: OrderMatch | None = None
    trigger_index: int | None = None
    ticks_processed = 0
    previous_event: datetime | None = None
    for tick_index, tick in enumerate(ticks):
        ticks_processed += 1
        if not isinstance(tick, Tick):
            raise TypeError("every feed item must be Tick")
        if tick.instrument != order.instrument:
            raise ValueError("feed instrument must match the order instrument throughout")
        if tick.quality_flags:
            raise ValueError("synthetic execution rejects all tick quality flags")
        _decimal_parts(tick.bid, "tick bid")
        _decimal_parts(tick.ask, "tick ask")
        if previous_event is not None and tick.ts_event < previous_event:
            raise ValueError("feed venue event time must be nondecreasing")
        previous_event = tick.ts_event
        if tick.ts_event <= eligible_after or exit_fill is not None:
            continue

        if entry is None:
            if order.stop_price is not None and trigger_index is None:
                if not _stop_reached(order.side, tick, order.stop_price):
                    continue
                trigger_index = tick_index
            if order.limit_price is not None:
                price = _limit_fill(order.side, tick, order.limit_price, model)
                if price is None:
                    continue
            else:
                price = _exact_price(
                    tick.ask if order.side is Side.BUY else tick.bid,
                    model.market.slippage_price,
                    order.side,
                )
            reason: ExecutionReason = _ENTRY_REASONS[order.order_type]
            entry = _make_match(
                order,
                model=model,
                submitted_at=submitted_at,
                decision_available_at=decision_available_at,
                tick=tick,
                tick_index=tick_index,
                side=order.side,
                price=price,
                reason=reason,
            )

        exit_side = Side.SELL if order.side is Side.BUY else Side.BUY
        exit_price: Decimal | None = None
        if order.stop_loss is not None and _stop_reached(exit_side, tick, order.stop_loss):
            exit_price = _exact_price(
                tick.ask if exit_side is Side.BUY else tick.bid,
                model.market.slippage_price,
                exit_side,
            )
            reason = "STOP_LOSS"
        elif order.take_profit is not None:
            exit_price = _limit_fill(exit_side, tick, order.take_profit, model)
            reason = "TAKE_PROFIT"
        if exit_price is not None:
            exit_fill = _make_match(
                order,
                model=model,
                submitted_at=submitted_at,
                decision_available_at=decision_available_at,
                tick=tick,
                tick_index=tick_index,
                side=exit_side,
                price=exit_price,
                reason=reason,
            )

    return OrderExecution(
        submitted_at=submitted_at,
        eligible_after=eligible_after,
        ticks_processed=ticks_processed,
        entry=entry,
        exit=exit_fill,
        stop_trigger_tick_index=trigger_index,
        optimistic_assumptions=("LIMIT_TOUCH_FILLS",) if model.limit_on_touch else (),
    )
