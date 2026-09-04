"""Immutable internal messages and exact boundary value types."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from tradebot.core.errors import InvalidEventError
from tradebot.core.timestamps import require_utc


class Event(Protocol):
    """Structural contract for an event eligible for bus publication."""

    @property
    def ts_event(self) -> datetime:
        """Return when the event happened in market time (UTC)."""
        ...

    @property
    def ts_recv(self) -> datetime:
        """Return when the platform observed the event (UTC)."""
        ...


class Side(StrEnum):
    """Order or fill direction."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """Supported order instruction types."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(StrEnum):
    """Supported order lifetime policies."""

    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    DAY = "DAY"


class VolumeKind(StrEnum):
    """Meaning of a bar's volume field."""

    TICK_COUNT = "TICK_COUNT"
    TRADED = "TRADED"


def _nonempty(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be str")
    if not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _decimal(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _positive_decimal(value: Decimal, field: str) -> None:
    _decimal(value, field)
    if value <= 0:
        raise InvalidEventError(f"{field} must be greater than zero")


def _enum(value: object, expected: type[StrEnum], field: str) -> None:
    if not isinstance(value, expected):
        raise TypeError(f"{field} must be {expected.__name__}")


def _optional_nonnegative_int(value: int | None, field: str) -> None:
    if value is None:
        return
    if type(value) is not int:
        raise TypeError(f"{field} must be int or None")
    if value < 0:
        raise InvalidEventError(f"{field} cannot be negative")


def _event_times(instance: Event) -> None:
    ts_event = instance.ts_event
    ts_recv = instance.ts_recv
    if not isinstance(ts_event, datetime):
        raise TypeError("ts_event must be datetime")
    if not isinstance(ts_recv, datetime):
        raise TypeError("ts_recv must be datetime")
    ts_event = require_utc(ts_event, field="ts_event")
    ts_recv = require_utc(ts_recv, field="ts_recv")
    object.__setattr__(instance, "ts_event", ts_event)
    object.__setattr__(instance, "ts_recv", ts_recv)
    if ts_recv < ts_event:
        raise InvalidEventError("ts_recv cannot be earlier than ts_event")


def _string_tuple(value: object, field: str) -> None:
    if type(value) is not tuple or any(not isinstance(item, str) for item in value):
        raise TypeError(f"{field} must be tuple[str, ...]")


def _string_pair_tuple(value: object, field: str) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be tuple[tuple[str, str], ...]")
    for item in value:
        if (
            type(item) is not tuple
            or len(item) != 2
            or not all(isinstance(part, str) for part in item)
        ):
            raise TypeError(f"{field} must be tuple[tuple[str, str], ...]")


@dataclass(frozen=True, slots=True, kw_only=True)
class Tick:
    """Observed bid/ask tick; prices are quote-currency units."""

    instrument: str
    ts_event: datetime
    ts_recv: datetime
    bid: Decimal
    ask: Decimal
    bid_size: int | None = None
    ask_size: int | None = None

    def __post_init__(self) -> None:
        _nonempty(self.instrument, "instrument")
        _event_times(self)
        _positive_decimal(self.bid, "bid")
        _positive_decimal(self.ask, "ask")
        if self.ask <= self.bid:
            raise InvalidEventError("delivered Tick requires ask > bid")
        _optional_nonnegative_int(self.bid_size, "bid_size")
        _optional_nonnegative_int(self.ask_size, "ask_size")


@dataclass(frozen=True, slots=True, kw_only=True)
class Bar:
    """Closed OHLC bar; prices are quote-currency units and ``ts_event`` is close time."""

    instrument: str
    ts_open: datetime
    ts_event: datetime
    ts_recv: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None
    spread_mean: Decimal | None
    n_ticks: int | None
    volume_kind: VolumeKind | None = None
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.instrument, "instrument")
        if not isinstance(self.ts_open, datetime):
            raise TypeError("ts_open must be datetime")
        object.__setattr__(self, "ts_open", require_utc(self.ts_open, field="ts_open"))
        _event_times(self)
        if self.ts_open >= self.ts_event:
            raise InvalidEventError("ts_open must be earlier than the closed bar's ts_event")
        for field in ("open", "high", "low", "close"):
            _positive_decimal(getattr(self, field), field)
        if self.spread_mean is not None:
            _decimal(self.spread_mean, "spread_mean")
            if self.spread_mean < 0:
                raise InvalidEventError("spread_mean cannot be negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise InvalidEventError("OHLC values are inconsistent")
        if self.high < self.low:
            raise InvalidEventError("high cannot be below low")
        _optional_nonnegative_int(self.volume, "volume")
        _optional_nonnegative_int(self.n_ticks, "n_ticks")
        if self.volume is None:
            if self.volume_kind is not None:
                raise InvalidEventError("volume_kind must be None when volume is None")
        else:
            if self.volume_kind is None:
                raise InvalidEventError("volume_kind is required when volume is present")
            _enum(self.volume_kind, VolumeKind, "volume_kind")
        _string_tuple(self.quality_flags, "quality_flags")

    @property
    def ts_close(self) -> datetime:
        """Return the bar's market close timestamp in UTC."""
        return self.ts_event


@dataclass(frozen=True, slots=True, kw_only=True)
class Forecast:
    """Causal strategy forecast on the documented scale ``[-20, +20]``."""

    strategy_id: str
    instrument: str
    ts_event: datetime
    ts_recv: datetime
    value: float
    source_event_id: str = ""
    confidence: float | None = None
    meta: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.strategy_id, "strategy_id")
        _nonempty(self.instrument, "instrument")
        if not isinstance(self.source_event_id, str):
            raise TypeError("source_event_id must be str")
        _event_times(self)
        if type(self.value) is not float:
            raise TypeError("forecast value must be float")
        if not math.isfinite(self.value) or not -20.0 <= self.value <= 20.0:
            raise InvalidEventError("forecast value must be finite and inside [-20, 20]")
        if self.confidence is not None:
            if type(self.confidence) is not float:
                raise TypeError("forecast confidence must be float or None")
            if not math.isfinite(self.confidence):
                raise InvalidEventError("forecast confidence must be finite")
        _string_pair_tuple(self.meta, "meta")

    @property
    def ts(self) -> datetime:
        """Return the strategy decision timestamp in UTC."""
        return self.ts_event


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderRequest:
    """Attributed order intent; quantity is positive base units or contracts by instrument."""

    client_order_id: str
    instrument: str
    side: Side
    qty: Decimal
    order_type: OrderType
    strategy_id: str
    run_id: str
    config_hash: str
    git_sha: str
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None

    def __post_init__(self) -> None:
        for field in (
            "client_order_id",
            "instrument",
            "strategy_id",
            "run_id",
            "config_hash",
            "git_sha",
        ):
            _nonempty(getattr(self, field), field)
        _enum(self.side, Side, "side")
        _enum(self.order_type, OrderType, "order_type")
        _enum(self.time_in_force, TimeInForce, "time_in_force")
        _positive_decimal(self.qty, "qty")
        for field in ("limit_price", "stop_price", "stop_loss", "take_profit"):
            value = getattr(self, field)
            if value is not None:
                _positive_decimal(value, field)

        required_prices = {
            OrderType.MARKET: (False, False),
            OrderType.LIMIT: (True, False),
            OrderType.STOP: (False, True),
            OrderType.STOP_LIMIT: (True, True),
        }
        requires_limit, requires_stop = required_prices[self.order_type]
        if (self.limit_price is not None) is not requires_limit:
            state = "required" if requires_limit else "not allowed"
            raise InvalidEventError(f"limit_price is {state} for {self.order_type.value} orders")
        if (self.stop_price is not None) is not requires_stop:
            state = "required" if requires_stop else "not allowed"
            raise InvalidEventError(f"stop_price is {state} for {self.order_type.value} orders")


@dataclass(frozen=True, slots=True, kw_only=True)
class Fill:
    """Attributed execution fill; quantity and price retain exact broker units."""

    broker_fill_id: str
    client_order_id: str
    instrument: str
    side: Side
    qty: Decimal
    price: Decimal
    strategy_id: str
    run_id: str
    config_hash: str
    git_sha: str
    ts_event: datetime
    ts_recv: datetime

    def __post_init__(self) -> None:
        for field in (
            "broker_fill_id",
            "client_order_id",
            "instrument",
            "strategy_id",
            "run_id",
            "config_hash",
            "git_sha",
        ):
            _nonempty(getattr(self, field), field)
        _enum(self.side, Side, "side")
        _positive_decimal(self.qty, "fill qty")
        _positive_decimal(self.price, "fill price")
        _event_times(self)
