"""Known-answer and fail-closed tests for the synthetic market-fill helper."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
from typing import cast

import pytest

from tradebot.backtest.market import MarketMatch, MarketModel, match_market_order
from tradebot.core.types import OrderRequest, OrderType, Side, Tick, TimeInForce

_BASE = datetime(2026, 1, 5, 12, tzinfo=UTC)


def _order(**changes: object) -> OrderRequest:
    order = OrderRequest(
        client_order_id="synthetic-order-1",
        instrument="Synthetic/GBPUSD",
        side=Side.BUY,
        qty=Decimal("1000"),
        order_type=OrderType.MARKET,
        strategy_id="scripted-fixture",
        run_id="fixture-run",
        config_hash="fixture-config",
        git_sha="UNCOMMITTED",
    )
    return replace(order, **changes)  # type: ignore[arg-type]


def _model(**changes: object) -> MarketModel:
    return replace(
        MarketModel(
            latency=timedelta(milliseconds=150),
            slippage_price=Decimal("0.00003"),
            max_full_fill_qty=Decimal("1000"),
        ),
        **changes,  # type: ignore[arg-type]
    )


def _tick(
    micros: int = 150001,
    *,
    receipt_micros: int | None = None,
    available_micros: int | None = None,
    **changes: object,
) -> Tick:
    event = _BASE + timedelta(microseconds=micros)
    receipt = event if receipt_micros is None else _BASE + timedelta(microseconds=receipt_micros)
    available = (
        max(event, receipt)
        if available_micros is None
        else _BASE + timedelta(microseconds=available_micros)
    )
    return replace(
        Tick(
            instrument="Synthetic/GBPUSD",
            ts_event=event,
            ts_recv=receipt,
            available_at=available,
            bid=Decimal("1.25000"),
            ask=Decimal("1.25020"),
        ),
        **changes,  # type: ignore[arg-type]
    )


def _match(
    ticks: Iterator[Tick] | tuple[Tick, ...],
    *,
    order: OrderRequest | None = None,
    model: MarketModel | None = None,
    submitted_at: datetime = _BASE,
    decision_available_at: datetime = _BASE,
) -> MarketMatch | None:
    return match_market_order(
        _order() if order is None else order,
        submitted_at=submitted_at,
        decision_available_at=decision_available_at,
        ticks=ticks,
        model=_model() if model is None else model,
    )


@pytest.mark.parametrize("side,price", [(Side.BUY, "1.25023"), (Side.SELL, "1.24997")])
def test_side_correct_known_answer_preserves_attribution(side: Side, price: str) -> None:
    order = _order(side=side)
    tick = _tick()
    match = _match((tick,), order=order)
    assert match is not None
    assert match.fill.price == Decimal(price)
    assert match.fill.qty == order.qty
    assert match.fill.side is side
    for name in (
        "client_order_id",
        "instrument",
        "strategy_id",
        "run_id",
        "config_hash",
        "git_sha",
    ):
        assert getattr(match.fill, name) == getattr(order, name)
    assert match.tick is tick
    assert match.tick_index == 0
    assert match.submitted_at == _BASE
    assert match.eligible_after == _BASE + timedelta(milliseconds=150)
    assert match.slippage_price == Decimal("0.00003")
    assert match.fill.quality_flags == ()


def test_strict_boundary_selects_first_later_tick_without_sorting() -> None:
    ticks = (_tick(-1), _tick(0), _tick(149999), _tick(150000), _tick(150001), _tick(200000))
    match = _match(ticks)
    assert match is not None
    assert match.tick is ticks[4]
    assert match.tick_index == 4


def test_zero_latency_does_not_fill_at_submission_tick() -> None:
    match = _match((_tick(0), _tick(1)), model=_model(latency=timedelta(0)))
    assert match is not None
    assert match.tick_index == 1


def test_equal_venue_times_keep_input_ordinal_as_tie_break() -> None:
    first = _tick(150001, ask=Decimal("1.26000"))
    second = _tick(150001, ask=Decimal("1.27000"))
    match = _match((first, second))
    assert match is not None
    assert match.tick is first
    assert match.fill.price == Decimal("1.26003")


def test_receipt_availability_is_not_an_extra_execution_latency() -> None:
    first = _tick(150001, receipt_micros=1000000, available_micros=2000000)
    later = _tick(200000, receipt_micros=200000, available_micros=200000)
    match = _match((first, later))
    assert match is not None
    assert match.tick is first
    assert match.fill.ts_event == first.ts_event
    assert match.fill.ts_recv == first.ts_recv
    assert match.fill.available_at == first.available_at


def test_venue_clock_ahead_of_receipt_does_not_rewrite_either_stamp() -> None:
    tick = _tick(150001, receipt_micros=150000)
    match = _match((tick,))
    assert match is not None
    assert match.fill.ts_recv == _BASE + timedelta(microseconds=150000)
    assert match.fill.ts_event == tick.ts_event
    assert match.fill.available_at == tick.ts_event


def test_backfilled_decision_cannot_match_its_old_market_price() -> None:
    available = _BASE + timedelta(minutes=5)
    old = _tick(0, receipt_micros=300000000, bid=Decimal("1.10000"), ask=Decimal("1.10020"))
    future = _tick(300150001, bid=Decimal("1.30000"), ask=Decimal("1.30020"))
    match = _match((old, future), submitted_at=available, decision_available_at=available)
    assert match is not None
    assert match.tick is future
    assert match.fill.price == Decimal("1.30023")


@pytest.mark.parametrize("ticks", [(), (_tick(0),), (_tick(150000),)])
def test_no_eligible_tick_returns_no_fill(ticks: tuple[Tick, ...]) -> None:
    assert _match(ticks) is None


def test_entire_iterator_is_consumed_after_candidate() -> None:
    visited: list[int] = []

    def ticks() -> Iterator[Tick]:
        for index in range(5):
            visited.append(index)
            yield _tick(150001 + index)

    match = _match(ticks())
    assert match is not None
    assert match.tick_index == 0
    assert visited == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("candidate", [True, False])
def test_feed_exception_is_not_hidden_by_candidate(candidate: bool) -> None:
    def ticks() -> Iterator[Tick]:
        yield _tick(150001 if candidate else 0)
        raise OSError("source integrity failed at EOF")

    with pytest.raises(OSError, match="integrity failed"):
        _match(ticks())


@pytest.mark.parametrize("first", [0, 150001])
def test_decreasing_tail_rejected_even_after_candidate(first: int) -> None:
    with pytest.raises(ValueError, match="event time"):
        _match((_tick(first), _tick(first - 1)))


@pytest.mark.parametrize("flags", [("BACKFILLED",), ("CLOCK_SKEW",), ("UNKNOWN",)])
@pytest.mark.parametrize("position", [0, 1])
def test_any_quality_flag_rejected_in_any_position(flags: tuple[str, ...], position: int) -> None:
    ticks = [_tick(150001), _tick(150002)]
    ticks[position] = replace(ticks[position], quality_flags=flags)
    with pytest.raises(ValueError, match="quality flags"):
        _match(tuple(ticks))


@pytest.mark.parametrize("instrument", ["Synthetic/EURUSD", "FBS/GBPUSD", "Synthetic/US500"])
@pytest.mark.parametrize("position", [0, 1])
def test_mixed_instrument_rejected_in_any_position(instrument: str, position: int) -> None:
    ticks = [_tick(150001), _tick(150002)]
    ticks[position] = replace(ticks[position], instrument=instrument)
    with pytest.raises(ValueError, match="instrument"):
        _match(tuple(ticks))


@pytest.mark.parametrize("position", [0, 1])
def test_non_tick_value_rejected_in_any_position(position: int) -> None:
    ticks: list[object] = [_tick(150001), _tick(150002)]
    ticks[position] = object()
    with pytest.raises(TypeError, match="Tick"):
        _match(cast(tuple[Tick, ...], tuple(ticks)))


class _UntouchedFeed(Iterator[Tick]):
    def __iter__(self) -> Iterator[Tick]:
        raise AssertionError("unsupported request accessed its input")

    def __next__(self) -> Tick:
        raise AssertionError("unsupported request consumed its input")


def _untouched_feed() -> Iterator[Tick]:
    return _UntouchedFeed()


@pytest.mark.parametrize(
    "order",
    [
        _order(order_type=OrderType.LIMIT, limit_price=Decimal("1.2")),
        _order(order_type=OrderType.STOP, stop_price=Decimal("1.3")),
        _order(
            order_type=OrderType.STOP_LIMIT,
            limit_price=Decimal("1.2"),
            stop_price=Decimal("1.3"),
        ),
        _order(time_in_force=TimeInForce.IOC),
        _order(time_in_force=TimeInForce.FOK),
        _order(time_in_force=TimeInForce.DAY),
        _order(stop_loss=Decimal("1.2")),
        _order(take_profit=Decimal("1.3")),
        _order(qty=Decimal("1000.00001")),
    ],
)
def test_unsupported_orders_fail_before_feed_access(order: OrderRequest) -> None:
    with pytest.raises(ValueError):
        _match(_untouched_feed(), order=order)


@pytest.mark.parametrize(
    "instrument",
    [
        "FBS/GBPUSD",
        "Synthetic/US500",
        "Synthetic/GBPGBP",
        "Synthetic/gbpusd",
        "Synthetic/GBP_USD",
        "Synthetic/GBPUSD.extra",
        "Synthetic/GBPUSD\n",
    ],
)
def test_non_synthetic_fx_request_rejected_before_input_access(instrument: str) -> None:
    with pytest.raises(ValueError, match="Synthetic"):
        _match(_untouched_feed(), order=_order(instrument=instrument))


@pytest.mark.parametrize("instrument", ["Synthetic/GBPUSD", "Synthetic/EURUSD", "Synthetic/USDZAR"])
def test_synthetic_distinct_currency_pair_can_match(instrument: str) -> None:
    assert _match((_tick(instrument=instrument),), order=_order(instrument=instrument)) is not None


@pytest.mark.parametrize(
    "submitted,available",
    [
        (_BASE, _BASE + timedelta(microseconds=1)),
        (_BASE.replace(tzinfo=None), _BASE),
        (_BASE, _BASE.replace(tzinfo=None)),
        (_BASE.astimezone(timezone(timedelta(hours=2))), _BASE),
        (_BASE, _BASE.astimezone(timezone(timedelta(hours=2)))),
    ],
)
def test_invalid_submission_or_decision_clock_rejected_before_feed(
    submitted: datetime, available: datetime
) -> None:
    with pytest.raises(ValueError):
        _match(_untouched_feed(), submitted_at=submitted, decision_available_at=available)


@pytest.mark.parametrize("field", ["submitted_at", "decision_available_at"])
def test_nondatetime_clock_rejected_before_feed(field: str) -> None:
    with pytest.raises(TypeError, match="datetime"):
        if field == "submitted_at":
            _match(_untouched_feed(), submitted_at=cast(datetime, "2026-01-05"))
        else:
            _match(_untouched_feed(), decision_available_at=cast(datetime, "2026-01-05"))


def test_order_after_decision_is_valid() -> None:
    match = _match((_tick(),), decision_available_at=_BASE - timedelta(seconds=1))
    assert match is not None


def test_eligible_datetime_overflow_fails_before_feed() -> None:
    maximum = datetime.max.replace(tzinfo=UTC)
    with pytest.raises(ValueError, match="latency"):
        _match(_untouched_feed(), submitted_at=maximum, decision_available_at=maximum)


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"latency": -timedelta(microseconds=1)}, ValueError),
        ({"latency": 0.15}, TypeError),
        ({"slippage_price": 0.0}, TypeError),
        ({"slippage_price": Decimal("-0.001")}, ValueError),
        ({"slippage_price": Decimal("NaN")}, ValueError),
        ({"slippage_price": Decimal("Infinity")}, ValueError),
        ({"max_full_fill_qty": 1000}, TypeError),
        ({"max_full_fill_qty": Decimal("0")}, ValueError),
        ({"max_full_fill_qty": Decimal("-1")}, ValueError),
        ({"max_full_fill_qty": Decimal("sNaN")}, ValueError),
        ({"slippage_price": Decimal("1e1001")}, ValueError),
        ({"slippage_price": Decimal("1e-1001")}, ValueError),
        ({"max_full_fill_qty": Decimal("9" * 1001)}, ValueError),
    ],
)
def test_invalid_model_configuration(changes: dict[str, object], error: type[Exception]) -> None:
    with pytest.raises(error):
        _model(**changes)


def test_zero_slippage_is_explicit_valid_synthetic_parameter() -> None:
    match = _match((_tick(),), model=_model(slippage_price=Decimal("0")))
    assert match is not None
    assert match.fill.price == Decimal("1.25020")


@pytest.mark.parametrize("slippage", ["1.25000", "1.25001"])
def test_nonpositive_sell_fill_price_rejected(slippage: str) -> None:
    with pytest.raises(ValueError, match="positive"):
        _match(
            (_tick(),), order=_order(side=Side.SELL), model=_model(slippage_price=Decimal(slippage))
        )


def test_late_tick_outside_numerical_envelope_is_not_ignored() -> None:
    late = _tick(150002, bid=Decimal("1e1001"), ask=Decimal("2e1001"))
    with pytest.raises(ValueError, match="numerical"):
        _match((_tick(), late))


def test_qty_outside_numerical_envelope_rejected_before_feed() -> None:
    with pytest.raises(ValueError, match="numerical"):
        _match(_untouched_feed(), order=_order(qty=Decimal("1e-1001")))


def test_arithmetic_is_exact_and_ambient_context_independent() -> None:
    tick = _tick(bid=Decimal("123456789.123456789"), ask=Decimal("123456789.123456799"))
    model = _model(slippage_price=Decimal("0.000000002"))
    normal = _match((tick,), model=model)
    assert normal is not None
    with localcontext() as context:
        context.prec = 2
        context.Emax = 2
        context.Emin = -2
        context.capitals = 0
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        changed = _match((tick,), model=model)
        assert context.prec == 2
        assert not context.flags[Inexact]
        assert not context.flags[Rounded]
    assert changed == normal
    assert normal.fill.price == Decimal("123456789.123456801")


def test_scientific_notation_identity_ignores_ambient_capitals() -> None:
    tick = _tick(bid=Decimal("1e20"), ask=Decimal("2e20"))
    expected = _match((tick,))
    with localcontext() as context:
        context.capitals = 0
        actual = _match((tick,))
    assert expected == actual


def test_extreme_supported_exponents_add_exactly() -> None:
    match = _match(
        (_tick(bid=Decimal("1e1000"), ask=Decimal("2e1000")),),
        model=_model(slippage_price=Decimal("1e-1000")),
    )
    assert match is not None
    assert match.fill.price == Decimal("2" + "0" * 1999 + "1e-1000")


def test_model_and_result_are_frozen() -> None:
    model = _model()
    with pytest.raises(FrozenInstanceError):
        model.slippage_price = Decimal("1")  # type: ignore[misc]
    match = _match((_tick(),))
    assert match is not None
    with pytest.raises(FrozenInstanceError):
        match.tick_index = 99  # type: ignore[misc]


def test_identical_retry_is_deterministic_but_does_not_claim_durable_deduplication() -> None:
    first = _match((_tick(),))
    second = _match(iter((_tick(),)))
    assert first == second
    assert first is not None
    assert first.fill.broker_fill_id.startswith("synthetic-market-")
    assert len(first.fill.broker_fill_id.removeprefix("synthetic-market-")) == 64


@pytest.mark.parametrize(
    "field", ["client_order_id", "strategy_id", "run_id", "config_hash", "git_sha"]
)
def test_fill_identity_binds_each_order_provenance_field(field: str) -> None:
    first = _match((_tick(),))
    changed = _match((_tick(),), order=_order(**{field: "different"}))
    assert first is not None and changed is not None
    assert first.fill.broker_fill_id != changed.fill.broker_fill_id


def test_fill_identity_binds_ordinal_side_size_model_and_timestamps() -> None:
    first = _match((_tick(),))
    assert first is not None
    variants = [
        _match((_tick(0), _tick())),
        _match((_tick(),), order=_order(side=Side.SELL)),
        _match((_tick(),), order=_order(qty=Decimal("999"))),
        _match((_tick(),), model=_model(latency=timedelta(milliseconds=149))),
        _match((_tick(),), model=_model(slippage_price=Decimal("0.00004"))),
        _match((_tick(),), model=_model(max_full_fill_qty=Decimal("2000"))),
        _match(
            (_tick(),),
            submitted_at=_BASE - timedelta(microseconds=1),
            decision_available_at=_BASE - timedelta(microseconds=1),
        ),
        _match((_tick(),), decision_available_at=_BASE - timedelta(microseconds=1)),
        _match((_tick(receipt_micros=150002),)),
        _match((_tick(available_micros=150002),)),
        _match((_tick(bid_size=100, ask_size=200),)),
        _match((_tick(bid=Decimal("1.24999")),)),
    ]
    ids = []
    for variant in variants:
        assert variant is not None
        ids.append(variant.fill.broker_fill_id)
    assert first.fill.broker_fill_id not in ids
    assert len(set(ids)) == len(ids)


def test_nonsupported_input_objects_rejected_before_feed_access() -> None:
    with pytest.raises(TypeError, match="OrderRequest"):
        _match(_untouched_feed(), order=cast(OrderRequest, object()))
    with pytest.raises(TypeError, match="MarketModel"):
        _match(_untouched_feed(), model=cast(MarketModel, object()))
