from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from math import inf, nan

import pytest

from tradebot.core.errors import InvalidEventError, InvalidTimestampError
from tradebot.core.types import (
    Bar,
    Fill,
    Forecast,
    OrderRequest,
    OrderType,
    Side,
    Tick,
    TimeInForce,
    VolumeKind,
)


def _utc(hour: int = 12) -> datetime:
    return datetime(2025, 3, 17, hour, tzinfo=UTC)


def test_bar_is_immutable_and_close_is_market_close_time() -> None:
    bar = Bar(
        instrument="GBP_USD",
        ts_open=_utc(11),
        ts_event=_utc(12),
        ts_recv=_utc(12),
        open=Decimal("1.29000"),
        high=Decimal("1.29100"),
        low=Decimal("1.28900"),
        close=Decimal("1.29050"),
        volume=100,
        volume_kind=VolumeKind.TICK_COUNT,
        spread_mean=Decimal("0.00008"),
        n_ticks=100,
    )

    assert bar.ts_close == bar.ts_event
    with pytest.raises(FrozenInstanceError):
        bar.close = Decimal("1.0")  # type: ignore[misc]


def test_event_rejects_naive_timestamp() -> None:
    with pytest.raises(InvalidTimestampError, match="UTC-aware"):
        Bar(
            instrument="GBP_USD",
            ts_open=datetime(2025, 3, 17, 11),
            ts_event=_utc(12),
            ts_recv=_utc(12),
            open=Decimal("1.29"),
            high=Decimal("1.30"),
            low=Decimal("1.28"),
            close=Decimal("1.29"),
            volume=None,
            spread_mean=None,
            n_ticks=None,
        )


def test_forecast_rejects_value_outside_documented_scale() -> None:
    with pytest.raises(InvalidEventError, match=r"\[-20, 20\]"):
        Forecast(
            strategy_id="hello",
            instrument="GBP_USD",
            ts_event=_utc(),
            ts_recv=_utc(),
            value=20.01,
        )


def test_order_request_requires_attribution() -> None:
    with pytest.raises(ValueError, match="strategy_id"):
        OrderRequest(
            client_order_id="paper-hello-GBPUSD-1-abcd1234",
            instrument="GBP_USD",
            side=Side.BUY,
            qty=Decimal("1000"),
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            strategy_id="",
            run_id="run-1",
            config_hash="abc",
            git_sha="deadbeef",
        )


def test_tick_validates_exact_prices_and_quote_order() -> None:
    tick = Tick(
        instrument="GBP_USD",
        ts_event=_utc(),
        ts_recv=_utc(),
        available_at=_utc(),
        bid=Decimal("1.29000"),
        ask=Decimal("1.29010"),
        bid_size=10,
        ask_size=11,
    )
    assert tick.ask - tick.bid == Decimal("0.00010")

    with pytest.raises(InvalidEventError, match="ask > bid"):
        Tick(
            instrument="GBP_USD",
            ts_event=_utc(),
            ts_recv=_utc(),
            available_at=_utc(),
            bid=Decimal("1.29010"),
            ask=Decimal("1.29010"),
        )
    with pytest.raises(InvalidEventError, match="bid_size"):
        Tick(
            instrument="GBP_USD",
            ts_event=_utc(),
            ts_recv=_utc(),
            available_at=_utc(),
            bid=Decimal("1.29000"),
            ask=Decimal("1.29010"),
            bid_size=-1,
        )
    with pytest.raises(InvalidEventError, match="ask_size"):
        Tick(
            instrument="GBP_USD",
            ts_event=_utc(),
            ts_recv=_utc(),
            available_at=_utc(),
            bid=Decimal("1.29000"),
            ask=Decimal("1.29010"),
            ask_size=-1,
        )
    with pytest.raises(TypeError, match="bid must be Decimal"):
        Tick(
            instrument="GBP_USD",
            ts_event=_utc(),
            ts_recv=_utc(),
            available_at=_utc(),
            bid=1.29,  # type: ignore[arg-type]
            ask=Decimal("1.29010"),
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"high": Decimal("1.28")}, "OHLC"),
        ({"low": Decimal("1.31")}, "OHLC"),
        ({"spread_mean": Decimal("-0.1")}, "spread_mean"),
        ({"volume": -1}, "volume"),
        ({"n_ticks": -1}, "n_ticks"),
    ],
)
def test_bar_rejects_inconsistent_market_values(changes: dict[str, object], message: str) -> None:
    fields: dict[str, object] = {
        "instrument": "GBP_USD",
        "ts_open": _utc(11),
        "ts_event": _utc(12),
        "ts_recv": _utc(12),
        "open": Decimal("1.29"),
        "high": Decimal("1.30"),
        "low": Decimal("1.28"),
        "close": Decimal("1.29"),
        "volume": 10,
        "volume_kind": VolumeKind.TICK_COUNT,
        "spread_mean": Decimal("0.0001"),
        "n_ticks": 10,
    }
    fields.update(changes)
    with pytest.raises(InvalidEventError, match=message):
        Bar(**fields)  # type: ignore[arg-type]


def test_bar_rejects_non_finite_decimal_and_invalid_interval() -> None:
    with pytest.raises(ValueError, match="open must be finite"):
        Bar(
            instrument="GBP_USD",
            ts_open=_utc(11),
            ts_event=_utc(12),
            ts_recv=_utc(12),
            open=Decimal("NaN"),
            high=Decimal("1.30"),
            low=Decimal("1.28"),
            close=Decimal("1.29"),
            volume=None,
            spread_mean=None,
            n_ticks=None,
        )
    with pytest.raises(InvalidEventError, match="ts_open"):
        Bar(
            instrument="GBP_USD",
            ts_open=_utc(12),
            ts_event=_utc(12),
            ts_recv=_utc(12),
            open=Decimal("1.29"),
            high=Decimal("1.30"),
            low=Decimal("1.28"),
            close=Decimal("1.29"),
            volume=None,
            spread_mean=None,
            n_ticks=None,
        )


@pytest.mark.parametrize("value", [nan, inf, -inf, -20.01, 20.01])
def test_forecast_rejects_non_finite_or_out_of_range_values(value: float) -> None:
    with pytest.raises(InvalidEventError):
        Forecast(
            strategy_id="hello",
            instrument="GBP_USD",
            ts_event=_utc(),
            ts_recv=_utc(),
            value=value,
        )


def test_forecast_accepts_boundaries_and_rejects_non_finite_confidence() -> None:
    assert (
        Forecast(
            strategy_id="hello",
            instrument="GBP_USD",
            ts_event=_utc(),
            ts_recv=_utc(),
            value=-20.0,
        ).ts
        == _utc()
    )
    assert (
        Forecast(
            strategy_id="hello",
            instrument="GBP_USD",
            ts_event=_utc(),
            ts_recv=_utc(),
            value=20.0,
        ).value
        == 20.0
    )
    with pytest.raises(InvalidEventError, match="confidence"):
        Forecast(
            strategy_id="hello",
            instrument="GBP_USD",
            ts_event=_utc(),
            ts_recv=_utc(),
            value=0.0,
            confidence=nan,
        )


def _order(**changes: object) -> OrderRequest:
    fields: dict[str, object] = {
        "client_order_id": "paper-hello-GBPUSD-1-abcd1234",
        "instrument": "GBP_USD",
        "side": Side.BUY,
        "qty": Decimal("1000"),
        "order_type": OrderType.MARKET,
        "strategy_id": "hello",
        "run_id": "run-1",
        "config_hash": "abc",
        "git_sha": "deadbeef",
    }
    fields.update(changes)
    return OrderRequest(**fields)  # type: ignore[arg-type]


def test_order_request_validates_quantity_and_required_prices() -> None:
    assert _order().qty == Decimal("1000")
    assert _order(order_type=OrderType.LIMIT, limit_price=Decimal("1.29")).limit_price == Decimal(
        "1.29"
    )
    assert _order(order_type=OrderType.STOP, stop_price=Decimal("1.30")).stop_price == Decimal(
        "1.30"
    )
    assert (
        _order(
            order_type=OrderType.STOP_LIMIT,
            limit_price=Decimal("1.29"),
            stop_price=Decimal("1.30"),
        ).order_type
        is OrderType.STOP_LIMIT
    )
    with pytest.raises(ValueError, match="qty"):
        _order(qty=Decimal("0"))
    with pytest.raises(TypeError, match="qty"):
        _order(qty=1000.0)
    with pytest.raises(ValueError, match="limit_price"):
        _order(order_type=OrderType.LIMIT)
    with pytest.raises(ValueError, match="stop_price"):
        _order(order_type=OrderType.STOP)
    with pytest.raises(ValueError, match="limit_price"):
        _order(order_type=OrderType.STOP_LIMIT, stop_price=Decimal("1.30"))


@pytest.mark.parametrize(
    "changes",
    [
        {"limit_price": Decimal("1.29")},
        {"stop_price": Decimal("1.30")},
        {
            "order_type": OrderType.LIMIT,
            "limit_price": Decimal("1.29"),
            "stop_price": Decimal("1.30"),
        },
        {
            "order_type": OrderType.STOP,
            "limit_price": Decimal("1.29"),
            "stop_price": Decimal("1.30"),
        },
    ],
)
def test_order_request_rejects_irrelevant_execution_prices(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidEventError, match="not allowed"):
        _order(**changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"side": "BUY"}, "side must be Side"),
        ({"order_type": "MARKET"}, "order_type must be OrderType"),
        ({"time_in_force": "GTC"}, "time_in_force must be TimeInForce"),
    ],
)
def test_order_request_rejects_raw_enum_strings(changes: dict[str, object], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        _order(**changes)


@pytest.mark.parametrize("field", ["limit_price", "stop_price", "stop_loss", "take_profit"])
def test_order_request_prices_must_be_positive(field: str) -> None:
    changes: dict[str, object] = {field: Decimal("0")}
    if field == "limit_price":
        changes["order_type"] = OrderType.LIMIT
    elif field == "stop_price":
        changes["order_type"] = OrderType.STOP
    with pytest.raises(InvalidEventError, match=field):
        _order(**changes)


def test_fill_is_attributed_and_exact() -> None:
    fill = Fill(
        broker_fill_id="fill-1",
        client_order_id="order-1",
        instrument="GBP_USD",
        side=Side.BUY,
        qty=Decimal("1000"),
        price=Decimal("1.29010"),
        strategy_id="hello",
        run_id="run-1",
        config_hash="abc",
        git_sha="deadbeef",
        ts_event=_utc(),
        ts_recv=_utc(),
        available_at=_utc(),
    )
    assert fill.qty * fill.price == Decimal("1290.10000")
    with pytest.raises(InvalidEventError, match="qty"):
        Fill(
            broker_fill_id="fill-1",
            client_order_id="order-1",
            instrument="GBP_USD",
            side=Side.BUY,
            qty=Decimal("0"),
            price=Decimal("1.29010"),
            strategy_id="hello",
            run_id="run-1",
            config_hash="abc",
            git_sha="deadbeef",
            ts_event=_utc(),
            ts_recv=_utc(),
            available_at=_utc(),
        )


def test_fill_rejects_raw_enum_and_nonpositive_price() -> None:
    fields: dict[str, object] = {
        "broker_fill_id": "fill-1",
        "client_order_id": "order-1",
        "instrument": "GBP_USD",
        "side": Side.BUY,
        "qty": Decimal("1000"),
        "price": Decimal("1.29010"),
        "strategy_id": "hello",
        "run_id": "run-1",
        "config_hash": "abc",
        "git_sha": "deadbeef",
        "ts_event": _utc(),
        "ts_recv": _utc(),
        "available_at": _utc(),
    }
    with pytest.raises(TypeError, match="side must be Side"):
        Fill(**(fields | {"side": "BUY"}))  # type: ignore[arg-type]
    with pytest.raises(InvalidEventError, match="fill price"):
        Fill(**(fields | {"price": Decimal("0")}))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bid_size", True),
        ("ask_size", 1.0),
    ],
)
def test_tick_sizes_must_be_exact_integers(field: str, value: object) -> None:
    fields: dict[str, object] = {
        "instrument": "GBP_USD",
        "ts_event": _utc(),
        "ts_recv": _utc(),
        "available_at": _utc(),
        "bid": Decimal("1.29"),
        "ask": Decimal("1.30"),
        field: value,
    }
    with pytest.raises(TypeError, match=field):
        Tick(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"volume": 10, "volume_kind": None}, InvalidEventError, "volume_kind is required"),
        (
            {"volume": None, "volume_kind": VolumeKind.TICK_COUNT},
            InvalidEventError,
            "volume_kind must be None",
        ),
        ({"volume": 10, "volume_kind": "TICK_COUNT"}, TypeError, "VolumeKind"),
        ({"volume": True, "volume_kind": VolumeKind.TICK_COUNT}, TypeError, "volume"),
        ({"n_ticks": False}, TypeError, "n_ticks"),
    ],
)
def test_bar_validates_volume_semantics_and_exact_counts(
    changes: dict[str, object], error: type[Exception], message: str
) -> None:
    fields: dict[str, object] = {
        "instrument": "GBP_USD",
        "ts_open": _utc(11),
        "ts_event": _utc(12),
        "ts_recv": _utc(12),
        "open": Decimal("1.29"),
        "high": Decimal("1.30"),
        "low": Decimal("1.28"),
        "close": Decimal("1.29"),
        "volume": None,
        "volume_kind": None,
        "spread_mean": Decimal("0.0001"),
        "n_ticks": 10,
    }
    fields.update(changes)
    with pytest.raises(error, match=message):
        Bar(**fields)  # type: ignore[arg-type]


def test_collection_fields_require_deeply_immutable_tuples() -> None:
    bar_fields: dict[str, object] = {
        "instrument": "GBP_USD",
        "ts_open": _utc(11),
        "ts_event": _utc(12),
        "ts_recv": _utc(12),
        "open": Decimal("1.29"),
        "high": Decimal("1.30"),
        "low": Decimal("1.28"),
        "close": Decimal("1.29"),
        "volume": None,
        "spread_mean": None,
        "n_ticks": None,
    }
    with pytest.raises(TypeError, match="quality_flags"):
        Bar(**(bar_fields | {"quality_flags": ["gap"]}))  # type: ignore[arg-type]

    forecast_fields: dict[str, object] = {
        "strategy_id": "hello",
        "instrument": "GBP_USD",
        "ts_event": _utc(),
        "ts_recv": _utc(),
        "value": 1.0,
    }
    with pytest.raises(TypeError, match="meta"):
        Forecast(**(forecast_fields | {"meta": [("regime", "trend")]}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="meta"):
        Forecast(**(forecast_fields | {"meta": (("regime", 1),)}))  # type: ignore[arg-type]


def test_platform_events_reject_receipt_time_before_market_time() -> None:
    """Bar and Forecast carry two stamps that are BOTH ours, so the order is our bug."""
    with pytest.raises(InvalidEventError, match="ts_recv"):
        Bar(
            instrument="GBP_USD",
            ts_open=_utc(10),
            ts_event=_utc(12),
            ts_recv=_utc(11),
            open=Decimal("1.29"),
            high=Decimal("1.30"),
            low=Decimal("1.28"),
            close=Decimal("1.29"),
            volume=None,
            spread_mean=None,
            n_ticks=None,
        )
    with pytest.raises(InvalidEventError, match="ts_recv"):
        Forecast(
            strategy_id="hello",
            instrument="GBP_USD",
            ts_event=_utc(12),
            ts_recv=_utc(11),
            value=1.0,
        )


def test_externally_clocked_events_admit_measured_cross_clock_skew() -> None:
    """ADR-0006: a venue stamp ahead of our receipt stamp is skew, not corruption.

    ts_event is on the venue's clock and ts_recv on ours; an ordering between two
    uncontrolled clocks can only be measured. Rejecting it would halt a healthy
    feed and, for a Fill, refuse to record an execution that already happened.
    """
    tick = Tick(
        instrument="GBP_USD",
        ts_event=_utc(12),
        ts_recv=_utc(11),
        available_at=_utc(12),
        bid=Decimal("1.29"),
        ask=Decimal("1.30"),
        quality_flags=("CLOCK_SKEW",),
    )
    assert tick.skew_lb.total_seconds() == 3600
    assert tick.available_at == _utc(12)

    fill = Fill(
        broker_fill_id="fill-1",
        client_order_id="order-1",
        instrument="GBP_USD",
        side=Side.BUY,
        qty=Decimal("1000"),
        price=Decimal("1.29"),
        strategy_id="hello",
        run_id="run-1",
        config_hash="abc",
        git_sha="deadbeef",
        ts_event=_utc(12),
        ts_recv=_utc(11),
        available_at=_utc(12),
    )
    assert fill.skew_lb.total_seconds() == 3600


def test_externally_clocked_events_require_a_dominating_availability_key() -> None:
    """The key is the invariant the platform CAN check, so it is checked strictly."""
    with pytest.raises(InvalidEventError, match="available_at"):
        Tick(
            instrument="GBP_USD",
            ts_event=_utc(12),
            ts_recv=_utc(11),
            available_at=_utc(11),
            bid=Decimal("1.29"),
            ask=Decimal("1.30"),
        )
    with pytest.raises(InvalidEventError, match="available_at"):
        Tick(
            instrument="GBP_USD",
            ts_event=_utc(11),
            ts_recv=_utc(12),
            available_at=_utc(11),
            bid=Decimal("1.29"),
            ask=Decimal("1.30"),
        )


def test_platform_events_derive_their_availability_key_from_receipt_time() -> None:
    bar = Bar(
        instrument="GBP_USD",
        ts_open=_utc(10),
        ts_event=_utc(11),
        ts_recv=_utc(12),
        open=Decimal("1.29"),
        high=Decimal("1.30"),
        low=Decimal("1.28"),
        close=Decimal("1.29"),
        volume=None,
        spread_mean=None,
        n_ticks=None,
    )
    assert bar.available_at == bar.ts_recv == _utc(12)

    forecast = Forecast(
        strategy_id="hello",
        instrument="GBP_USD",
        ts_event=_utc(11),
        ts_recv=_utc(12),
        value=1.0,
    )
    assert forecast.available_at == forecast.ts_recv == _utc(12)


def test_prices_must_be_positive() -> None:
    with pytest.raises(InvalidEventError, match="bid"):
        Tick(
            instrument="GBP_USD",
            ts_event=_utc(),
            ts_recv=_utc(),
            available_at=_utc(),
            bid=Decimal("0"),
            ask=Decimal("1.30"),
        )
    with pytest.raises(InvalidEventError, match="low"):
        Bar(
            instrument="GBP_USD",
            ts_open=_utc(11),
            ts_event=_utc(12),
            ts_recv=_utc(12),
            open=Decimal("1.29"),
            high=Decimal("1.30"),
            low=Decimal("0"),
            close=Decimal("1.29"),
            volume=None,
            spread_mean=None,
            n_ticks=None,
        )
