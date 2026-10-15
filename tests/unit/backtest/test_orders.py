"""Known answers and closed-boundary tests for synthetic order state transitions."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, Inexact, Rounded, localcontext
from typing import cast

import pytest

from tradebot.backtest.costs import RoundTripInput, summarize_round_trip
from tradebot.backtest.market import MarketModel, match_market_order
from tradebot.backtest.orders import OrderExecution, OrderModel, match_synthetic_order
from tradebot.core.types import OrderRequest, OrderType, Side, Tick, TimeInForce
from tradebot.research.report import canonical_bytes

_START = datetime(2024, 1, 8, 12, tzinfo=UTC)


def _order(**changes: object) -> OrderRequest:
    return replace(
        OrderRequest(
            client_order_id="known-order",
            instrument="Synthetic/EURUSD",
            side=Side.BUY,
            qty=Decimal("10000"),
            order_type=OrderType.MARKET,
            strategy_id="known-answer",
            run_id="invented",
            config_hash="known-config",
            git_sha="UNCOMMITTED",
        ),
        **changes,  # type: ignore[arg-type]
    )


def _model(**changes: object) -> OrderModel:
    return replace(
        OrderModel(
            market=MarketModel(
                latency=timedelta(milliseconds=150),
                slippage_price=Decimal("0.00002"),
                max_full_fill_qty=Decimal("10000"),
            ),
            price_tick=Decimal("0.00010"),
            limit_on_touch=False,
        ),
        **changes,  # type: ignore[arg-type]
    )


def _tick(ms: int = 151, bid: str = "1.10000", ask: str = "1.10020") -> Tick:
    event = _START + timedelta(milliseconds=ms)
    return Tick(
        instrument="Synthetic/EURUSD",
        ts_event=event,
        ts_recv=event + timedelta(seconds=1),
        available_at=event + timedelta(seconds=2),
        bid=Decimal(bid),
        ask=Decimal(ask),
    )


def _match(
    ticks: Iterable[Tick],
    order: OrderRequest | None = None,
    model: OrderModel | None = None,
    /,
    **changes: datetime,
) -> OrderExecution:
    params = {"submitted_at": _START, "decision_available_at": _START} | changes
    return match_synthetic_order(
        _order() if order is None else order,
        ticks=ticks,
        model=_model() if model is None else model,
        **params,
    )


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_market_compatibility_strict_latency_and_full_tail_measurement(side: Side) -> None:
    ticks = (_tick(0), _tick(150), _tick(151), _tick(152))
    order = _order(side=side)
    result = _match(ticks, order)
    old = match_market_order(
        order,
        submitted_at=_START,
        decision_available_at=_START,
        ticks=ticks,
        model=_model().market,
    )
    assert result.entry is not None and old is not None
    assert result.entry.fill.price == old.fill.price
    assert result.entry.tick_index == 2
    assert result.entry.fill.ts_event == ticks[2].ts_event
    assert result.entry.fill.ts_recv == ticks[2].ts_recv
    assert result.entry.fill.available_at == ticks[2].available_at
    assert result.entry.fill.client_order_id == order.client_order_id
    assert result.entry.fill.strategy_id == order.strategy_id
    assert result.entry.fill.run_id == order.run_id
    assert result.entry.fill.config_hash == order.config_hash
    assert result.entry.fill.git_sha == order.git_sha
    assert result.entry.fill.instrument == order.instrument
    assert result.entry.fill.qty == order.qty
    assert result.entry.reason == "MARKET"
    assert result.entry.adverse_price_from_quote == Decimal("0.00002")
    assert result.ticks_processed == 4
    assert result.exit is None and result.stop_trigger_tick_index is None


@pytest.mark.parametrize(
    "side,price,ticks",
    [
        (
            Side.BUY,
            "1.10020",
            (
                _tick(151, "1.10020", "1.10040"),
                _tick(152, "1.10000", "1.10030"),
                _tick(153, "1.10000", "1.10010"),
            ),
        ),
        (
            Side.SELL,
            "1.10000",
            (
                _tick(151, "1.09980", "1.10000"),
                _tick(152, "1.09990", "1.10020"),
                _tick(153, "1.10010", "1.10020"),
            ),
        ),
    ],
)
def test_limit_touch_and_wide_spread_do_not_fill(
    side: Side,
    price: str,
    ticks: tuple[Tick, ...],
) -> None:
    result = _match(
        ticks, _order(side=side, order_type=OrderType.LIMIT, limit_price=Decimal(price))
    )
    assert result.entry is not None
    assert result.entry.tick_index == 2
    assert result.entry.fill.price == Decimal(price)
    assert result.entry.reason == "LIMIT"
    assert result.entry.adverse_price_from_quote == Decimal("0.00010")


@pytest.mark.parametrize(
    "side,bid,ask,limit",
    [(Side.BUY, "1.10015", "1.10017", "1.10020"), (Side.SELL, "1.10003", "1.10005", "1.10000")],
)
def test_touch_sensitivity_must_be_explicit_and_reported(
    side: Side,
    bid: str,
    ask: str,
    limit: str,
) -> None:
    tick = _tick(bid=bid, ask=ask)
    order = _order(side=side, order_type=OrderType.LIMIT, limit_price=Decimal(limit))
    assert _match((tick,), order).entry is None
    result = _match((tick,), order, _model(limit_on_touch=True))
    assert result.entry is not None
    assert result.optimistic_assumptions == ("LIMIT_TOUCH_FILLS",)
    assert _match((), order, _model(limit_on_touch=True)).optimistic_assumptions


@pytest.mark.parametrize(
    "side,stop,ticks,price",
    [
        (
            Side.BUY,
            "1.10100",
            (_tick(151, "1.10070", "1.10090"), _tick(152, "1.10180", "1.10200")),
            "1.10202",
        ),
        (
            Side.SELL,
            "1.09900",
            (_tick(151, "1.09910", "1.09930"), _tick(152, "1.09800", "1.09820")),
            "1.09798",
        ),
    ],
)
def test_gap_stop_uses_first_executable_quote_not_stop_price(
    side: Side,
    stop: str,
    ticks: tuple[Tick, ...],
    price: str,
) -> None:
    result = _match(ticks, _order(side=side, order_type=OrderType.STOP, stop_price=Decimal(stop)))
    assert result.entry is not None
    assert result.stop_trigger_tick_index == 1
    assert result.entry.tick_index == 1
    assert result.entry.fill.price == Decimal(price)
    assert result.entry.reason == "STOP"


@pytest.mark.parametrize("side,stop", [(Side.BUY, "1.10020"), (Side.SELL, "1.10000")])
def test_stop_trigger_includes_equality_on_executable_quote(side: Side, stop: str) -> None:
    result = _match(
        (_tick(),), _order(side=side, order_type=OrderType.STOP, stop_price=Decimal(stop))
    )
    assert result.entry is not None and result.stop_trigger_tick_index == 0


def test_stop_limit_activation_sticks_and_waits_for_fillable_limit() -> None:
    ticks = (_tick(151, "1.10180", "1.10200"), _tick(152, "1.10000", "1.10020"))
    order = _order(
        order_type=OrderType.STOP_LIMIT,
        stop_price=Decimal("1.10100"),
        limit_price=Decimal("1.10050"),
    )
    result = _match(ticks, order)
    assert result.stop_trigger_tick_index == 0
    assert result.entry is not None and result.entry.tick_index == 1
    assert result.entry.fill.price == Decimal("1.10050")
    assert result.entry.reason == "STOP_LIMIT"
    assert _match(ticks[:1], order).entry is None


def test_stop_limit_can_fill_on_trigger_and_cannot_fill_before_trigger() -> None:
    order = _order(
        order_type=OrderType.STOP_LIMIT,
        stop_price=Decimal("1.10010"),
        limit_price=Decimal("1.10050"),
    )
    result = _match((_tick(),), order)
    assert result.entry is not None and result.entry.tick_index == 0
    assert result.stop_trigger_tick_index == 0
    assert _match((_tick(151, "1.09900", "1.09920"),), order).entry is None


@pytest.mark.parametrize(
    "side,sl,tp,second,reason,price",
    [
        (Side.BUY, "1.09900", "1.10100", _tick(152, "1.09800", "1.09820"), "STOP_LOSS", "1.09798"),
        (
            Side.BUY,
            "1.09900",
            "1.10100",
            _tick(152, "1.10110", "1.10130"),
            "TAKE_PROFIT",
            "1.10100",
        ),
        (Side.SELL, "1.10100", "1.09900", _tick(152, "1.10200", "1.10220"), "STOP_LOSS", "1.10222"),
        (
            Side.SELL,
            "1.10100",
            "1.09900",
            _tick(152, "1.09870", "1.09890"),
            "TAKE_PROFIT",
            "1.09900",
        ),
    ],
)
def test_bracket_legs_use_correct_side_then_cancel_sibling(
    side: Side,
    sl: str,
    tp: str,
    second: Tick,
    reason: str,
    price: str,
) -> None:
    order = _order(side=side, stop_loss=Decimal(sl), take_profit=Decimal(tp))
    result = _match((_tick(), second, _tick(153, "1.00000", "1.20000")), order)
    assert result.entry is not None and result.exit is not None
    assert result.exit.reason == reason
    assert result.exit.fill.price == Decimal(price)
    assert result.exit.fill.side is (Side.SELL if side is Side.BUY else Side.BUY)
    assert result.exit.fill.client_order_id == "known-order/" + reason
    assert result.exit.tick_index == 1 and result.ticks_processed == 3


def test_bracket_can_exit_immediately_on_gapped_entry_and_keep_available_time() -> None:
    result = _match((_tick(),), _order(stop_loss=Decimal("1.10010")))
    assert result.entry is not None and result.exit is not None
    assert result.entry.tick_index == result.exit.tick_index == 0
    assert result.exit.fill.price == Decimal("1.09998")
    assert result.exit.fill.available_at == _tick().available_at
    assert result.entry.fill.broker_fill_id != result.exit.fill.broker_fill_id


def test_equal_timestamp_ordinals_are_not_resorted() -> None:
    result = _match(
        (_tick(), _tick(151, "1.10110", "1.10130")), _order(take_profit=Decimal("1.10100"))
    )
    assert result.entry is not None and result.exit is not None
    assert result.entry.tick_index == 0 and result.exit.tick_index == 1
    assert result.entry.fill.ts_event == result.exit.fill.ts_event


@pytest.mark.parametrize(
    "kind,prices,entry_price,net",
    [
        (OrderType.MARKET, {}, "1.10012", "7.85"),
        (OrderType.LIMIT, {"limit_price": Decimal("1.10020")}, "1.10020", "7.05"),
        (OrderType.STOP, {"stop_price": Decimal("1.10010")}, "1.10012", "7.85"),
        (
            OrderType.STOP_LIMIT,
            {"stop_price": Decimal("1.10010"), "limit_price": Decimal("1.10020")},
            "1.10020",
            "7.05",
        ),
    ],
)
def test_hand_computed_round_trip_all_order_types_to_the_cent(
    kind: OrderType,
    prices: dict[str, Decimal],
    entry_price: str,
    net: str,
) -> None:
    order = _order(order_type=kind, take_profit=Decimal("1.10100"), **prices)
    result = _match((_tick(151, "1.10000", "1.10010"), _tick(152, "1.10110", "1.10130")), order)
    assert result.entry is not None and result.exit is not None
    entry, exit_fill = result.entry, result.exit
    amounts = summarize_round_trip(
        RoundTripInput(
            entry_side=order.side,
            qty=order.qty,
            entry_bid=entry.tick.bid,
            entry_ask=entry.tick.ask,
            exit_bid=exit_fill.tick.bid,
            exit_ask=exit_fill.tick.ask,
            entry_slippage=entry.adverse_price_from_quote,
            exit_slippage=exit_fill.adverse_price_from_quote,
            entry_commission=Decimal("0.35"),
            exit_commission=Decimal("0.35"),
            financing_cashflow=Decimal("-0.25"),
        ),
        conversion=None,
    )
    assert amounts.entry_fill_price == entry.fill.price == Decimal(entry_price)
    assert amounts.exit_fill_price == exit_fill.fill.price == Decimal("1.10100")
    assert amounts.gross_pnl_account == Decimal("11.50")
    assert amounts.spread_cost_account == Decimal("1.50")
    assert amounts.commission_cost_account == Decimal("0.70")
    assert amounts.financing_cashflow_account == Decimal("-0.25")
    assert amounts.net_pnl_account == Decimal(net)
    assert amounts.gross_pnl_account - amounts.total_cost_account == amounts.net_pnl_account


def test_ambient_decimal_context_cannot_change_prices_or_report_bytes() -> None:
    order = _order(
        order_type=OrderType.LIMIT, limit_price=Decimal("1.10020"), take_profit=Decimal("1.10100")
    )
    ticks = (_tick(151, "1.10000", "1.10010"), _tick(152, "1.10110", "1.10130"))
    expected = canonical_bytes(_match(ticks, order))
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = context.traps[Rounded] = True
        assert canonical_bytes(_match(ticks, order)) == expected
    assert canonical_bytes(_match(iter(ticks), order)) == expected


@pytest.mark.parametrize(
    "invalid",
    [
        replace(_tick(152), instrument="Synthetic/GBPUSD"),
        replace(_tick(152), quality_flags=("TS_RECV_IMPUTED",)),
        _tick(150),
        "not a tick",
    ],
)
def test_invalid_tail_cannot_hide_behind_completed_bracket(invalid: object) -> None:
    ticks = (_tick(), _tick(152, "1.10110", "1.10130"), invalid)
    with pytest.raises((TypeError, ValueError)):
        _match(cast(Iterable[Tick], ticks), _order(take_profit=Decimal("1.10100")))


def test_true_eof_failure_never_returns_partial_result() -> None:
    def failing() -> Iterator[Tick]:
        yield _tick()
        raise OSError("feed integrity failed")

    with pytest.raises(OSError, match="feed integrity"):
        _match(failing())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"instrument": "FBS-Demo/EURUSD"},
        {"instrument": "Synthetic/USDUSD"},
        {"qty": Decimal("10001")},
        {"time_in_force": TimeInForce.DAY},
        {"stop_loss": Decimal("1.2"), "take_profit": Decimal("1.1")},
        {"side": Side.SELL, "stop_loss": Decimal("1.1"), "take_profit": Decimal("1.2")},
    ],
)
def test_invalid_requests_fail_before_touching_feed(kwargs: dict[str, object]) -> None:
    accessed = False

    def forbidden() -> Iterator[Tick]:
        nonlocal accessed
        accessed = True
        yield _tick()

    with pytest.raises(ValueError):
        _match(forbidden(), _order(**kwargs))
    assert not accessed


@pytest.mark.parametrize(
    "changes",
    [
        {"price_tick": Decimal("0")},
        {"price_tick": Decimal("NaN")},
        {"price_tick": 0.1},
        {"limit_on_touch": 1},
        {"market": "invalid"},
    ],
)
def test_invalid_model_rejected(changes: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        _model(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"submitted_at": _START - timedelta(seconds=1)},
        {"submitted_at": _START.replace(tzinfo=None)},
        {"submitted_at": datetime.max.replace(tzinfo=UTC)},
        {"decision_available_at": cast(datetime, "bad")},
    ],
)
def test_invalid_time_boundary_rejected(changes: dict[str, datetime]) -> None:
    with pytest.raises((TypeError, ValueError)):
        _match((), **changes)


def test_unfilled_and_frozen_results_and_invalid_argument_types() -> None:
    result = _match(())
    assert result.entry is None and result.exit is None and result.ticks_processed == 0
    with pytest.raises(FrozenInstanceError):
        result.ticks_processed = 1  # type: ignore[misc]
    for bad_order, bad_model in [
        (cast(OrderRequest, None), _model()),
        (_order(), cast(OrderModel, None)),
    ]:
        with pytest.raises(TypeError):
            match_synthetic_order(
                bad_order,
                submitted_at=_START,
                decision_available_at=_START,
                ticks=(),
                model=bad_model,
            )
