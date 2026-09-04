"""Strictly typed ports that keep domain logic independent of adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from tradebot.core.clock import ReadClock
from tradebot.core.types import Bar, Fill, Forecast, OrderRequest, Tick

OrderAckT_co = TypeVar("OrderAckT_co", covariant=True)
PositionT_co = TypeVar("PositionT_co", covariant=True)
OrderStateT_co = TypeVar("OrderStateT_co", covariant=True)
AccountSnapshotT_co = TypeVar("AccountSnapshotT_co", covariant=True)
InstrumentSpecT_co = TypeVar("InstrumentSpecT_co", covariant=True)
BrokerEventT_co = TypeVar("BrokerEventT_co", covariant=True)


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Capabilities visible to strategy code; deliberately excludes feed and broker."""

    clock: ReadClock


class BarStrategy(Protocol):
    """Causal strategy capability for completed bars."""

    @property
    def id(self) -> str:
        """Return stable strategy identifier."""
        ...

    @property
    def instruments(self) -> Sequence[str]:
        """Return subscribed instrument identifiers."""
        ...

    @property
    def warmup_bars(self) -> int:
        """Return required closed bars before forecasts are emitted."""
        ...

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> Sequence[Forecast]:
        """Return forecasts derived only from *bar* and prior state."""
        ...

    def state(self) -> Mapping[str, object]:
        """Return immutable-compatible checkpoint state."""
        ...

    def restore(self, state: Mapping[str, object]) -> None:
        """Restore a prior checkpoint."""
        ...


class TickStrategy(Protocol):
    """Optional causal strategy capability for ticks."""

    def on_tick(self, tick: Tick, ctx: StrategyContext) -> Sequence[Forecast]:
        """Return forecasts derived only from *tick* and prior state."""
        ...


class FillAwareStrategy(Protocol):
    """Optional capability for observing attributed fills."""

    def on_fill(self, fill: Fill, ctx: StrategyContext) -> None:
        """Update checkpointable strategy state from a fill."""
        ...


class DataFeed(Protocol):
    """Finite P0 bar feed; streaming I/O adapters arrive in later phases."""

    def bars(self) -> Iterator[Bar]:
        """Yield closed bars in deterministic source order."""
        ...


class Broker(
    Protocol[
        OrderAckT_co,
        PositionT_co,
        OrderStateT_co,
        AccountSnapshotT_co,
        InstrumentSpecT_co,
        BrokerEventT_co,
    ]
):
    """Typed broker port; result schemas are supplied by the later OMS domain model."""

    async def connect(self) -> None:
        """Connect and authenticate without exposing credentials to callers."""
        ...

    async def submit(self, req: OrderRequest) -> OrderAckT_co:
        """Submit one attributed order intent idempotently."""
        ...

    async def cancel(self, client_order_id: str) -> OrderAckT_co:
        """Request cancellation by stable client order ID."""
        ...

    async def replace(self, client_order_id: str, **changes: object) -> OrderAckT_co:
        """Request an attributed order replacement."""
        ...

    async def positions(self) -> Sequence[PositionT_co]:
        """Return the broker's current position snapshot."""
        ...

    async def open_orders(self) -> Sequence[OrderStateT_co]:
        """Return the broker's current open-order snapshot."""
        ...

    async def account(self) -> AccountSnapshotT_co:
        """Return the broker's current account snapshot."""
        ...

    async def instrument_spec(self, instrument: str) -> InstrumentSpecT_co:
        """Return the broker contract specification for *instrument*."""
        ...

    def stream_events(self) -> AsyncIterator[BrokerEventT_co]:
        """Yield broker fills, rejects, states, and prices in observed order."""
        ...
