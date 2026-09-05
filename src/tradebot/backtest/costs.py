"""Exact supplied-cost attribution for synthetic market-order round trips.

This is not a broker commission, financing, rollover, or currency-settlement
model. Every adjustment must be supplied explicitly, including zero. Quotes are
quote currency per base unit; quantity is in base units; commissions and the
signed financing cash flow are in quote currency. Slippage is an adverse price
increment, not money. No parameter here is calibrated or a real-broker fact.

``conversion=None`` explicitly declares the quote and account currency equal.
A conversion quote is direct: account currency per quote-currency unit. The
single terminal net cash flow converts at bid when positive and ask when
negative. Component translation at mid is attribution only; its difference
from the executable conversion appears as a separate conversion cost. This
does not model converting commissions or accruals at their individual dates.

Cash is exact and never rounded to account-currency minor units. An explicit
implementation envelope allows at most 1,000 coefficient digits and exponents
from -1,000 through 1,000 per input; unsupported values fail instead of silently
rounding. All operations use an independent, exact Decimal context. Only the
dimensionless ratio rounds (28 significant digits, ROUND_HALF_EVEN). A ratio
with negative gross PnL is arithmetic, not a meaningful fragility indicator.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Clamped,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    localcontext,
)

from tradebot.core.types import Side

_MAX_INPUT_DIGITS = 1000
_MAX_INPUT_EXPONENT = 1000
# A cash line has at most three factors (quote difference, quantity, FX rate).
# Their exponent span and coefficients fit well inside this exact bound, even
# after independently scaled additions. Traps make a bound mistake fail closed.
_EXACT_PRECISION = 16384


def _exact_context() -> Context:
    return Context(
        prec=_EXACT_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[
            Clamped,
            DivisionByZero,
            Inexact,
            InvalidOperation,
            Overflow,
            Rounded,
            Subnormal,
            Underflow,
        ],
    )


def _decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    parts = value.as_tuple()
    exponent = parts.exponent
    if not isinstance(exponent, int):  # Defensive; finite Decimal has integer exponent.
        raise ValueError(f"{name} must have a finite exponent")
    if len(parts.digits) > _MAX_INPUT_DIGITS or abs(exponent) > _MAX_INPUT_EXPONENT:
        raise ValueError(f"{name} is outside the exact arithmetic implementation envelope")


def _positive(value: Decimal, name: str) -> None:
    _decimal(value, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _nonnegative(value: Decimal, name: str) -> None:
    _decimal(value, name)
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")


@dataclass(frozen=True, slots=True, kw_only=True)
class RoundTripInput:
    """Explicit two-fill, one-quantity inputs, without inferred missing costs.

    Financing is signed: a negative cash flow is a charge, a positive cash flow
    a credit. Its timing and completeness are the caller's evidence obligation;
    this calculator does not derive a rollover or broker schedule.
    """

    entry_side: Side
    qty: Decimal
    entry_bid: Decimal
    entry_ask: Decimal
    exit_bid: Decimal
    exit_ask: Decimal
    entry_slippage: Decimal
    exit_slippage: Decimal
    entry_commission: Decimal
    exit_commission: Decimal
    financing_cashflow: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.entry_side, Side):
            raise TypeError("entry_side must be Side")
        for name in ("qty", "entry_bid", "entry_ask", "exit_bid", "exit_ask"):
            _positive(getattr(self, name), name)
        for name in ("entry_slippage", "exit_slippage", "entry_commission", "exit_commission"):
            _nonnegative(getattr(self, name), name)
        _decimal(self.financing_cashflow, "financing_cashflow")
        if self.entry_ask <= self.entry_bid:
            raise ValueError("entry_ask must be strictly greater than entry_bid")
        if self.exit_ask <= self.exit_bid:
            raise ValueError("exit_ask must be strictly greater than exit_bid")
        with localcontext(_exact_context()):
            sell_fill = (
                self.exit_bid - self.exit_slippage
                if self.entry_side == Side.BUY
                else self.entry_bid - self.entry_slippage
            )
        if sell_fill <= 0:
            raise ValueError("slippage must leave the sell fill price positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversionQuote:
    """Direct account-currency per foreign quote-currency conversion quote."""

    bid: Decimal
    ask: Decimal

    def __post_init__(self) -> None:
        _positive(self.bid, "bid")
        _positive(self.ask, "ask")
        if self.ask <= self.bid:
            raise ValueError("ask must be strictly greater than bid")


@dataclass(frozen=True, slots=True, kw_only=True)
class CostBreakdown:
    """Midpoint gross less each cost reconciles to executable net cash flow.

    Costs are positive charges except that a financing credit can make total
    costs negative. ``price_pnl_quote`` already includes spread and slippage;
    those lines must not be subtracted from price PnL again. A zero gross PnL
    produces ``cost_ratio=None`` rather than an invented finite ratio.
    """

    entry_fill_price: Decimal
    exit_fill_price: Decimal
    gross_pnl_quote: Decimal
    spread_cost_quote: Decimal
    slippage_cost_quote: Decimal
    commission_cost_quote: Decimal
    financing_cashflow_quote: Decimal
    price_pnl_quote: Decimal
    net_pnl_quote: Decimal
    gross_pnl_account: Decimal
    spread_cost_account: Decimal
    slippage_cost_account: Decimal
    commission_cost_account: Decimal
    financing_cashflow_account: Decimal
    conversion_cost_account: Decimal
    net_pnl_account: Decimal
    total_cost_account: Decimal
    cost_ratio: Decimal | None


def summarize_round_trip(
    trade: RoundTripInput, *, conversion: ConversionQuote | None
) -> CostBreakdown:
    """Attribute a supplied synthetic round trip; never infer absent charges.

    A negative gross denominator is preserved in the ratio, but must not be
    interpreted as passing a positive-gross cost-fragility check. This function
    does not certify that the caller supplied all broker cash adjustments.
    """
    if not isinstance(trade, RoundTripInput):
        raise TypeError("trade must be RoundTripInput")
    if conversion is not None and not isinstance(conversion, ConversionQuote):
        raise TypeError("conversion must be ConversionQuote or None")

    with localcontext(_exact_context()) as arithmetic:
        entry_mid = (trade.entry_bid + trade.entry_ask) / 2
        exit_mid = (trade.exit_bid + trade.exit_ask) / 2
        direction = 1 if trade.entry_side == Side.BUY else -1
        entry_fill = (
            trade.entry_ask + trade.entry_slippage
            if trade.entry_side == Side.BUY
            else trade.entry_bid - trade.entry_slippage
        )
        exit_fill = (
            trade.exit_bid - trade.exit_slippage
            if trade.entry_side == Side.BUY
            else trade.exit_ask + trade.exit_slippage
        )
        gross = (exit_mid - entry_mid) * trade.qty * direction
        spread = (
            (trade.entry_ask - trade.entry_bid + trade.exit_ask - trade.exit_bid) / 2 * trade.qty
        )
        slippage = (trade.entry_slippage + trade.exit_slippage) * trade.qty
        commission = trade.entry_commission + trade.exit_commission
        financing = trade.financing_cashflow
        price_pnl = (exit_fill - entry_fill) * trade.qty * direction
        net_quote = price_pnl - commission + financing

        conversion_mid = Decimal(1) if conversion is None else (conversion.bid + conversion.ask) / 2
        if conversion is None or net_quote == 0:
            net_account = net_quote
        elif net_quote > 0:
            net_account = net_quote * conversion.bid
        else:
            net_account = net_quote * conversion.ask

        gross_account = gross * conversion_mid
        spread_account = spread * conversion_mid
        slippage_account = slippage * conversion_mid
        commission_account = commission * conversion_mid
        financing_account = financing * conversion_mid
        conversion_cost = net_quote * conversion_mid - net_account
        total_cost = (
            spread_account
            + slippage_account
            + commission_account
            - financing_account
            + conversion_cost
        )

        # Only this dimensionless output is allowed to round, never cash lines.
        arithmetic.prec = 28
        arithmetic.traps[Inexact] = False
        arithmetic.traps[Rounded] = False
        ratio = None if gross_account == 0 else total_cost / gross_account

    return CostBreakdown(
        entry_fill_price=entry_fill,
        exit_fill_price=exit_fill,
        gross_pnl_quote=gross,
        spread_cost_quote=spread,
        slippage_cost_quote=slippage,
        commission_cost_quote=commission,
        financing_cashflow_quote=financing,
        price_pnl_quote=price_pnl,
        net_pnl_quote=net_quote,
        gross_pnl_account=gross_account,
        spread_cost_account=spread_account,
        slippage_cost_account=slippage_account,
        commission_cost_account=commission_account,
        financing_cashflow_account=financing_account,
        conversion_cost_account=conversion_cost,
        net_pnl_account=net_account,
        total_cost_account=total_cost,
        cost_ratio=ratio,
    )
