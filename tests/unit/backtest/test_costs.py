"""Known-answer accounting checks, never market-performance evidence."""

from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, localcontext
from fractions import Fraction

import pytest

from tradebot.backtest.costs import (
    ConversionQuote,
    RoundTripInput,
    summarize_round_trip,
)
from tradebot.core.types import Side

D = Decimal


def trade(**changes: object) -> RoundTripInput:
    values: dict[str, object] = {
        "entry_side": Side.BUY,
        "qty": D("10000"),
        "entry_bid": D("1.1000"),
        "entry_ask": D("1.1002"),
        "exit_bid": D("1.1100"),
        "exit_ask": D("1.1104"),
        "entry_slippage": D("0.0001"),
        "exit_slippage": D("0.0002"),
        "entry_commission": D("0.35"),
        "exit_commission": D("0.35"),
        "financing_cashflow": D("-0.20"),
    }
    values.update(changes)
    return RoundTripInput(**values)  # type: ignore[arg-type]


def test_known_long_cashflow_components_to_cent() -> None:
    result = summarize_round_trip(trade(), conversion=None)
    assert result.entry_fill_price == D("1.1003")
    assert result.exit_fill_price == D("1.1098")
    assert result.gross_pnl_quote == D("101.00")
    assert result.spread_cost_quote == D("3.00")
    assert result.slippage_cost_quote == D("3.00")
    assert result.commission_cost_quote == D("0.70")
    assert result.financing_cashflow_quote == D("-0.20")
    assert result.price_pnl_quote == D("95.00")
    assert result.net_pnl_quote == D("94.10")
    assert result.net_pnl_account == D("94.10")
    assert result.total_cost_account == D("6.90")
    assert result.conversion_cost_account == 0
    assert result.cost_ratio == D("0.06831683168316831683168316832")


@pytest.mark.parametrize(
    ("side", "exit_bid", "exit_ask", "gross", "net_quote", "net_account", "fx_cost"),
    [
        (Side.BUY, "1.1100", "1.1104", "101", "94.10", "1693.800", "9.410"),
        (Side.BUY, "1.0900", "1.0904", "-99", "-105.90", "-1927.380", "10.590"),
        (Side.SELL, "1.0900", "1.0904", "99", "92.10", "1657.800", "9.210"),
        (Side.SELL, "1.1100", "1.1104", "-101", "-107.90", "-1963.780", "10.790"),
    ],
)
def test_long_and_short_profit_and_loss_conversion(
    side: Side,
    exit_bid: str,
    exit_ask: str,
    gross: str,
    net_quote: str,
    net_account: str,
    fx_cost: str,
) -> None:
    result = summarize_round_trip(
        trade(entry_side=side, exit_bid=D(exit_bid), exit_ask=D(exit_ask)),
        conversion=ConversionQuote(bid=D("18.0"), ask=D("18.2")),
    )
    assert result.gross_pnl_quote == D(gross)
    assert result.net_pnl_quote == D(net_quote)
    assert result.gross_pnl_quote - result.spread_cost_quote - result.slippage_cost_quote == (
        result.price_pnl_quote
    )
    assert result.net_pnl_account == D(net_account)
    assert result.conversion_cost_account == D(fx_cost)
    assert result.gross_pnl_account == D(gross) * D("18.1")
    assert result.spread_cost_account == D("54.3")
    assert result.slippage_cost_account == D("54.3")
    assert result.commission_cost_account == D("12.67")
    assert result.financing_cashflow_account == D("-3.62")
    assert result.total_cost_account == (
        result.spread_cost_account
        + result.slippage_cost_account
        + result.commission_cost_account
        - result.financing_cashflow_account
        + result.conversion_cost_account
    )
    assert result.gross_pnl_account - result.total_cost_account == result.net_pnl_account
    assert result.cost_ratio is not None
    assert (result.cost_ratio < 0) == (D(gross) < 0)


def test_short_execution_prices_use_bid_then_ask_with_adverse_slippage() -> None:
    result = summarize_round_trip(trade(entry_side=Side.SELL), conversion=None)
    assert result.entry_fill_price == D("1.0999")
    assert result.exit_fill_price == D("1.1106")


def test_spread_is_attribution_not_double_charge() -> None:
    supplied = trade()
    result = summarize_round_trip(supplied, conversion=None)
    actual_entry = supplied.entry_ask + supplied.entry_slippage
    actual_exit = supplied.exit_bid - supplied.exit_slippage
    actual_price_pnl = (actual_exit - actual_entry) * supplied.qty
    assert result.price_pnl_quote == actual_price_pnl
    assert result.net_pnl_quote == (
        actual_price_pnl
        - supplied.entry_commission
        - supplied.exit_commission
        + supplied.financing_cashflow
    )


def test_zero_gross_has_no_ratio_and_still_pays_costs() -> None:
    result = summarize_round_trip(
        trade(exit_bid=D("1.1000"), exit_ask=D("1.1002")), conversion=None
    )
    assert result.gross_pnl_quote == 0
    assert result.net_pnl_quote == D("-5.90")
    assert result.cost_ratio is None


def test_zero_net_has_zero_currency_conversion_cost() -> None:
    result = summarize_round_trip(
        trade(financing_cashflow=D("-94.30")),
        conversion=ConversionQuote(bid=D("18.0"), ask=D("18.2")),
    )
    assert result.net_pnl_quote == 0
    assert result.net_pnl_account == 0
    assert result.conversion_cost_account == 0
    assert result.cost_ratio == 1


def test_explicit_financing_credit_can_exceed_transaction_costs() -> None:
    result = summarize_round_trip(trade(financing_cashflow=D("10.00")), conversion=None)
    assert result.financing_cashflow_quote == D("10.00")
    assert result.total_cost_account == D("-3.30")
    assert result.net_pnl_quote == D("104.30")
    assert result.cost_ratio is not None and result.cost_ratio < 0


def test_small_exact_cash_is_not_rounded_before_aggregation() -> None:
    result = summarize_round_trip(
        trade(qty=D("0.001"), entry_commission=D("0.000001"), exit_commission=D("0.000002")),
        conversion=None,
    )
    assert result.gross_pnl_quote == D("0.0000101")
    assert result.spread_cost_quote == D("0.0000003")
    assert result.slippage_cost_quote == D("0.0000003")
    assert result.net_pnl_quote == D("-0.1999935")


def test_cash_and_ratio_ignore_ambient_decimal_context() -> None:
    supplied = trade()
    quote = ConversionQuote(bid=D("18.0"), ask=D("18.2"))
    expected = summarize_round_trip(supplied, conversion=quote)
    with localcontext() as ambient:
        ambient.prec = 2
        ambient.rounding = ROUND_DOWN
        ambient.Emax = 2
        ambient.Emin = -2
        ambient.traps[Inexact] = True
        ambient.traps[Rounded] = True
        actual = summarize_round_trip(trade(), conversion=quote)
        assert actual == expected
        assert ambient.prec == 2
        assert ambient.rounding == ROUND_DOWN
        assert not any(ambient.flags.values())


def test_supported_extreme_cash_matches_independent_rational_oracle() -> None:
    supplied = trade(
        qty=D("9" * 1000 + "e-1000"),
        entry_bid=D("8" * 1000 + "e1000"),
        entry_ask=D("9" * 1000 + "e1000"),
        exit_bid=D("7" * 1000 + "e1000"),
        exit_ask=D("8" * 1000 + "e1000"),
        entry_slippage=D("1e-1000"),
        exit_slippage=D("1e-1000"),
        entry_commission=D("1e-1000"),
        exit_commission=D("1e-1000"),
        financing_cashflow=D("1e1000"),
    )
    quote = ConversionQuote(bid=D("1e-1000"), ask=D("9" * 1000 + "e1000"))
    result = summarize_round_trip(supplied, conversion=quote)
    entry = Fraction(supplied.entry_ask) + Fraction(supplied.entry_slippage)
    exit_price = Fraction(supplied.exit_bid) - Fraction(supplied.exit_slippage)
    price_pnl = (exit_price - entry) * Fraction(supplied.qty)
    net = (
        price_pnl
        - Fraction(supplied.entry_commission)
        - Fraction(supplied.exit_commission)
        + Fraction(supplied.financing_cashflow)
    )
    assert net < 0
    assert Fraction(result.entry_fill_price) == entry
    assert Fraction(result.exit_fill_price) == exit_price
    assert Fraction(result.price_pnl_quote) == price_pnl
    assert Fraction(result.net_pnl_quote) == net
    assert Fraction(result.net_pnl_account) == net * Fraction(quote.ask)
    assert Fraction(result.gross_pnl_account) - Fraction(result.total_cost_account) == (
        Fraction(result.net_pnl_account)
    )


NUMERIC_FIELDS = (
    "qty",
    "entry_bid",
    "entry_ask",
    "exit_bid",
    "exit_ask",
    "entry_slippage",
    "exit_slippage",
    "entry_commission",
    "exit_commission",
    "financing_cashflow",
)


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
@pytest.mark.parametrize("bad", [1, 1.0, "1", True, None])
def test_non_decimal_inputs_fail(field: str, bad: object) -> None:
    with pytest.raises(TypeError, match=field):
        trade(**{field: bad})


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
@pytest.mark.parametrize("bad", [D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity")])
def test_non_finite_inputs_fail(field: str, bad: Decimal) -> None:
    with pytest.raises(ValueError, match=field):
        trade(**{field: bad})


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
@pytest.mark.parametrize("bad", [D("1E1001"), D("1E-1001"), D("1" * 1001)])
def test_unsupported_precision_and_exponents_fail(field: str, bad: Decimal) -> None:
    with pytest.raises(ValueError, match=field):
        trade(**{field: bad})


@pytest.mark.parametrize("field", ["qty", "entry_bid", "entry_ask", "exit_bid", "exit_ask"])
@pytest.mark.parametrize("bad", [D("0"), D("-1")])
def test_positive_boundary_inputs(field: str, bad: Decimal) -> None:
    with pytest.raises(ValueError, match=field):
        trade(**{field: bad})


@pytest.mark.parametrize(
    "field", ["entry_slippage", "exit_slippage", "entry_commission", "exit_commission"]
)
def test_negative_cost_inputs_fail(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        trade(**{field: D("-0.01")})


@pytest.mark.parametrize("prefix", ["entry", "exit"])
@pytest.mark.parametrize("ask", [D("1.0"), D("0.9")])
def test_locked_or_crossed_quotes_fail(prefix: str, ask: Decimal) -> None:
    with pytest.raises(ValueError, match=prefix):
        trade(**{f"{prefix}_bid": D("1.0"), f"{prefix}_ask": ask})


@pytest.mark.parametrize("side", ["BUY", None, 1])
def test_side_must_be_enum(side: object) -> None:
    with pytest.raises(TypeError, match="entry_side"):
        trade(entry_side=side)


@pytest.mark.parametrize(
    ("side", "entry_slippage", "exit_slippage"),
    [(Side.BUY, "0", "1.1100"), (Side.SELL, "1.1000", "0")],
)
def test_nonpositive_resultant_fill_rejected(
    side: Side, entry_slippage: str, exit_slippage: str
) -> None:
    with pytest.raises(ValueError, match="fill"):
        trade(
            entry_side=side,
            entry_slippage=D(entry_slippage),
            exit_slippage=D(exit_slippage),
        )


@pytest.mark.parametrize("bad", [D("0"), D("-1"), D("NaN"), D("1e1001"), 1])
@pytest.mark.parametrize("field", ["bid", "ask"])
def test_conversion_quote_validation(field: str, bad: object) -> None:
    values: dict[str, object] = {"bid": D("18.0"), "ask": D("18.2"), field: bad}
    with pytest.raises((ValueError, TypeError), match=field):
        ConversionQuote(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("ask", [D("18.0"), D("17.9")])
def test_conversion_quote_requires_strict_spread(ask: Decimal) -> None:
    with pytest.raises(ValueError, match="ask"):
        ConversionQuote(bid=D("18.0"), ask=ask)


def test_no_missing_cost_or_currency_defaults() -> None:
    supplied = trade()
    with pytest.raises(TypeError, match="financing_cashflow"):
        RoundTripInput(  # type: ignore[call-arg]
            entry_side=supplied.entry_side,
            qty=supplied.qty,
            entry_bid=supplied.entry_bid,
            entry_ask=supplied.entry_ask,
            exit_bid=supplied.exit_bid,
            exit_ask=supplied.exit_ask,
            entry_slippage=supplied.entry_slippage,
            exit_slippage=supplied.exit_slippage,
            entry_commission=supplied.entry_commission,
            exit_commission=supplied.exit_commission,
        )
    with pytest.raises(TypeError, match="conversion"):
        summarize_round_trip(supplied)  # type: ignore[call-arg]


def test_public_container_and_summarizer_types() -> None:
    supplied = trade()
    quote = ConversionQuote(bid=D("18.0"), ask=D("18.2"))
    result = summarize_round_trip(supplied, conversion=quote)
    for value, name in [(supplied, "qty"), (quote, "bid"), (result, "net_pnl_quote")]:
        with pytest.raises(FrozenInstanceError):
            setattr(value, name, D("2"))
    with pytest.raises(TypeError, match="trade"):
        summarize_round_trip(None, conversion=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="conversion"):
        summarize_round_trip(supplied, conversion=D("1"))  # type: ignore[arg-type]
    assert replace(supplied, financing_cashflow=D("0")).financing_cashflow == 0
