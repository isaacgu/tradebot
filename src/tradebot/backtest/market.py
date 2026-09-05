"""Pure, synthetic-only MARKET matching; not a SimBroker, OMS, or trading path.

The caller supplies a finite, single-instrument stream in venue-event order.
Matching is independent of bus delivery: receipt and availability timestamps
are preserved on the result for later admission, never used as extra latency.
Explicit full-fill size and constant adverse slippage are invented engineering
assumptions, not calibrated liquidity or broker execution models.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from tradebot.core.timestamps import require_utc
from tradebot.core.types import Fill, OrderRequest, OrderType, Side, Tick, TimeInForce

# Representation bounds limit arithmetic resources, not capital or trading risk.
# Inputs in this envelope produce sums with at most 3,001 coefficient digits.
_MAX_DIGITS = 1000
_MAX_EXPONENT = 1000


def _decimal_parts(value: Decimal, field: str) -> tuple[int, int]:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")
    parts = value.as_tuple()
    exponent = parts.exponent
    if (
        not isinstance(exponent, int)
        or len(parts.digits) > _MAX_DIGITS
        or abs(exponent) > _MAX_EXPONENT
    ):
        raise ValueError(f"{field} exceeds the synthetic numerical representation envelope")
    coefficient = 0
    for digit in parts.digits:
        coefficient = coefficient * 10 + digit
    return (-coefficient if parts.sign else coefficient), exponent


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketModel:
    """Explicit uncalibrated parameters for one synthetic full-fill assumption."""

    latency: timedelta
    slippage_price: Decimal
    max_full_fill_qty: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.latency, timedelta):
            raise TypeError("latency must be timedelta")
        if self.latency < timedelta(0):
            raise ValueError("latency cannot be negative")
        _decimal_parts(self.slippage_price, "slippage_price")
        _decimal_parts(self.max_full_fill_qty, "max_full_fill_qty")
        if self.slippage_price < 0:
            raise ValueError("slippage_price cannot be negative")
        if self.max_full_fill_qty <= 0:
            raise ValueError("max_full_fill_qty must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketMatch:
    """Immutable result values, not independent certification or durable OMS state."""

    fill: Fill
    tick: Tick
    tick_index: int
    submitted_at: datetime
    eligible_after: datetime
    slippage_price: Decimal


def _exact_price(quote: Decimal, slip: Decimal, side: Side) -> Decimal:
    quote_coefficient, quote_exponent = _decimal_parts(quote, "quote")
    slip_coefficient, slip_exponent = _decimal_parts(slip, "slippage_price")
    exponent = min(quote_exponent, slip_exponent)
    quote_coefficient *= 10 ** (quote_exponent - exponent)
    slip_coefficient *= 10 ** (slip_exponent - exponent)
    coefficient = (
        quote_coefficient + slip_coefficient
        if side is Side.BUY
        else quote_coefficient - slip_coefficient
    )
    if coefficient <= 0:
        raise ValueError("synthetic fill price must be positive after adverse slippage")
    # Tuple construction is exact and ignores the ambient Decimal context.
    digits = tuple(int(digit) for digit in str(coefficient))
    return Decimal((0, digits, exponent))


def _identity_value(value: object) -> str | int:
    if isinstance(value, Decimal):
        parts = value.as_tuple()
        coefficient = "".join(str(digit) for digit in parts.digits)
        return ("-" if parts.sign else "") + coefficient + "e" + str(parts.exponent)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return (value.days * 86400 + value.seconds) * 1000000 + value.microseconds
    raise TypeError(f"unsupported match identity value: {type(value).__name__}")


def match_market_order(
    order: OrderRequest,
    *,
    submitted_at: datetime,
    decision_available_at: datetime,
    ticks: Iterable[Tick],
    model: MarketModel,
) -> MarketMatch | None:
    """Match the first venue tick strictly after submission plus model latency.

    Unsupported requests fail before the iterable is touched. Every feed item
    is validated through EOF before a match is returned; an invalid tail or an
    iterator integrity error cannot be concealed behind an earlier candidate.
    The ID is deterministic for repeat inputs, but this stateless helper does
    not prevent the caller from accounting for the same fill twice.
    """
    if not isinstance(order, OrderRequest):
        raise TypeError("order must be OrderRequest")
    if not isinstance(model, MarketModel):
        raise TypeError("model must be MarketModel")
    if (
        re.fullmatch(r"Synthetic/[A-Z]{6}", order.instrument) is None
        or order.instrument[-6:-3] == order.instrument[-3:]
    ):
        raise ValueError("only Synthetic/<distinct uppercase FX currencies> is supported")
    if order.order_type is not OrderType.MARKET or order.time_in_force is not TimeInForce.GTC:
        raise ValueError("only MARKET orders with GTC are supported")
    if order.stop_loss is not None or order.take_profit is not None:
        raise ValueError("bracket orders are unsupported")
    _decimal_parts(order.qty, "qty")
    if order.qty > model.max_full_fill_qty:
        raise ValueError("qty exceeds the explicitly configured synthetic full-fill size")
    if not isinstance(submitted_at, datetime) or not isinstance(decision_available_at, datetime):
        raise TypeError("submitted_at and decision_available_at must be datetime")
    submitted_at = require_utc(submitted_at, field="submitted_at")
    decision_available_at = require_utc(decision_available_at, field="decision_available_at")
    if submitted_at < decision_available_at:
        raise ValueError("order submission cannot precede decision availability")
    try:
        eligible_after = submitted_at + model.latency
    except OverflowError as error:
        raise ValueError("submission plus latency exceeds the datetime range") from error

    candidate: tuple[int, Tick] | None = None
    previous_event: datetime | None = None
    for tick_index, tick in enumerate(ticks):
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
        if candidate is None and tick.ts_event > eligible_after:
            candidate = tick_index, tick

    if candidate is None:
        return None
    tick_index, tick = candidate
    price = _exact_price(
        tick.ask if order.side is Side.BUY else tick.bid, model.slippage_price, order.side
    )
    identity = {
        "schema": "synthetic-market-match-v1",
        "order": asdict(order),
        "model": asdict(model),
        "submitted_at": submitted_at,
        "decision_available_at": decision_available_at,
        "tick": asdict(tick),
        "tick_index": tick_index,
    }
    encoded = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_identity_value
    ).encode("utf-8")
    fill = Fill(
        broker_fill_id="synthetic-market-" + hashlib.sha256(encoded).hexdigest(),
        client_order_id=order.client_order_id,
        instrument=order.instrument,
        side=order.side,
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
    )
    return MarketMatch(
        fill=fill,
        tick=tick,
        tick_index=tick_index,
        submitted_at=submitted_at,
        eligible_after=eligible_after,
        slippage_price=model.slippage_price,
    )
