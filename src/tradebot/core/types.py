"""Immutable internal messages and exact boundary value types."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
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

    @property
    def available_at(self) -> datetime:
        """Return when the platform could first have acted on the event (UTC)."""
        ...


class QualityFlag(StrEnum):
    """Data-quality markers carried on events and unioned into bars (SPEC 4.4).

    Members are ``str``, so they satisfy the ``tuple[str, ...]`` flag fields while
    giving callers symbols instead of bare literals.
    """

    CLOCK_SKEW = "CLOCK_SKEW"
    TS_RECV_IMPUTED = "TS_RECV_IMPUTED"
    BACKFILLED = "BACKFILLED"


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


def _normalize_event_times(instance: Event) -> tuple[datetime, datetime]:
    """Validate and normalize both stamps to UTC in place, returning them."""
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
    return ts_event, ts_recv


def _platform_event_times(instance: Event) -> None:
    """Validate stamps for an event this platform produced (ADR-0006).

    Both stamps are ours — a bar builder's or a strategy's — so ``ts_recv`` earlier
    than ``ts_event`` is our own bug and stays unconditionally fatal.
    """
    ts_event, ts_recv = _normalize_event_times(instance)
    if ts_recv < ts_event:
        raise InvalidEventError("ts_recv cannot be earlier than ts_event")


def _externally_clocked_event_times(instance: Event) -> None:
    """Validate stamps for an event stamped by a venue or broker (ADR-0006).

    ``ts_event`` is on the venue's clock and ``ts_recv`` on the local host's, so an
    ordering between them can only be MEASURED, never asserted: with sound NTP a
    fast feed still delivers ``ts_recv`` microseconds before ``ts_event``. The
    checkable invariant is therefore ``available_at >= max(ts_event, ts_recv)``,
    which the platform controls. ``ts_recv`` is never normalized to satisfy it —
    doing so would defeat the SPEC 4.5 staleness watchdog, which measures
    ``now - ts_recv``.
    """
    ts_event, ts_recv = _normalize_event_times(instance)
    available_at = instance.available_at
    if not isinstance(available_at, datetime):
        raise TypeError("available_at must be datetime")
    available_at = require_utc(available_at, field="available_at")
    object.__setattr__(instance, "available_at", available_at)
    if available_at < ts_event or available_at < ts_recv:
        raise InvalidEventError("available_at cannot be earlier than either ts_event or ts_recv")


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
    available_at: datetime
    bid: Decimal
    ask: Decimal
    bid_size: int | None = None
    ask_size: int | None = None
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.instrument, "instrument")
        _externally_clocked_event_times(self)
        _positive_decimal(self.bid, "bid")
        _positive_decimal(self.ask, "ask")
        if self.ask <= self.bid:
            raise InvalidEventError("delivered Tick requires ask > bid")
        _optional_nonnegative_int(self.bid_size, "bid_size")
        _optional_nonnegative_int(self.ask_size, "ask_size")
        _string_tuple(self.quality_flags, "quality_flags")

    @property
    def skew_lb(self) -> timedelta:
        """Return ``ts_event - ts_recv``: a LOWER BOUND on venue-minus-local skew.

        Biased downward by network transit and blind to a local clock running
        ahead of the venue, so it is a diagnostic and a quality signal — never on
        its own the trigger for SPEC 7.5's broker-skew halt.
        """
        return self.ts_event - self.ts_recv


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
        _platform_event_times(self)
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

    @property
    def available_at(self) -> datetime:
        """Return when this bar became actionable, which is when the builder sealed it.

        Derived, not stored: a platform-produced event already guarantees
        ``ts_recv >= ts_event``, so the availability maximum collapses to ``ts_recv``
        and cannot drift from it.
        """
        return self.ts_recv


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
        _platform_event_times(self)
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

    @property
    def available_at(self) -> datetime:
        """Return when this forecast became actionable (see ``Bar.available_at``)."""
        return self.ts_recv


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
    available_at: datetime
    quality_flags: tuple[str, ...] = ()

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
        # A Fill is never refused for a timestamp or skew reason (ADR-0006): the
        # execution already happened, and refusing to construct it would leave an
        # unrecorded live position and guarantee an NN-9 mismatch over a clock
        # offset. Attribution (NN-3) above is still unconditionally enforced.
        _externally_clocked_event_times(self)
        _string_tuple(self.quality_flags, "quality_flags")

    @property
    def skew_lb(self) -> timedelta:
        """Return ``ts_event - ts_recv`` (see ``Tick.skew_lb``)."""
        return self.ts_event - self.ts_recv
