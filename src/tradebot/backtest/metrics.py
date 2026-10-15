"""Deterministic arithmetic metrics, not a complete statistical validation suite.

Inputs are explicit positive account-equity marks and closed-trade net cash PnLs
in one account currency. They are caller-supplied arithmetic evidence: this module
does not open data, verify out-of-sample lineage, certify costs or approve a gate.

Period returns are E[t] / E[t-1] - 1, including genuine zero returns. Annualised
volatility uses sample standard deviation (n-1) and sqrt(periods_per_year).
Sharpe uses mean simple excess return / sample standard deviation of excess
returns, annualised by the same factor. The configurable risk-free input is an
effective annual rate, converted geometrically to one declared period. No Sharpe
is supplied without the explicit OOS declaration AND at least 200 closed trades;
there is no waiver switch. The square-root annualisation convention is not a
serial-correlation adjustment or a statistical significance claim.

CAGR uses actual elapsed microseconds divided by exactly 365.25 days per year,
not the number of marks or the annualisation frequency. Cash sums are exact;
ratios and roots use an independent 50-significant-digit HALF_EVEN context.
Unsupported numerical ranges fail rather than returning nonfinite results.

Definitions: SPEC 6.5/6.6; William F. Sharpe, "The Sharpe Ratio" (1994),
https://stanford.edu/~wfsharpe/art/sr/sr.htm ; sample standard deviation:
https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/trimsd.htm .
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Underflow,
    localcontext,
)
from itertools import pairwise

from tradebot.core.timestamps import require_utc

_YEAR_MICROSECONDS = Decimal("31557600000000")  # Exactly 365.25 * 86400 * 1e6.
_MINIMUM_OOS_TRADES = 200
_UNIMPLEMENTED = (
    "sortino",
    "calmar",
    "ulcer_index",
    "expectancy_per_unit_risk",
    "return_skew_and_excess_kurtosis",
    "tail_ratio",
    "turnover",
    "average_holding_time",
    "exposure",
    "mean_return_t_statistic",
    "sqn",
    "SPEC_6_5_statistical_validation_suite",
)


def _context(*, exact_cash: bool = False) -> Context:
    return Context(
        prec=16384 if exact_cash else 50,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN if exact_cash else -10000,
        Emax=MAX_EMAX if exact_cash else 10000,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow]
        + ([Inexact, Rounded] if exact_cash else []),
    )


def _decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    parts = value.as_tuple()
    if (
        not isinstance(parts.exponent, int)
        or len(parts.digits) > 1000
        or abs(parts.exponent) > 1000
    ):
        raise ValueError(f"{name} exceeds the numerical representation envelope")
    return value


def _cash_sum(values: tuple[Decimal, ...]) -> Decimal:
    with localcontext(_context(exact_cash=True)):
        return sum(values, Decimal(0))


def _microseconds(duration: timedelta) -> int:
    return ((duration.days * 86400) + duration.seconds) * 1000000 + duration.microseconds


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """A UTC mark of strictly positive account equity, not a deposit/flow record.

    Cash flows are not adjusted here: callers must supply a consistent equity
    series with no external deposits/withdrawals or a correctly unitised series.
    A nonpositive-equity/bankrupt path requires a separate ruin-aware analysis.
    """

    timestamp: datetime
    equity: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be datetime")
        require_utc(self.timestamp, field="equity timestamp")
        if _decimal(self.equity, "equity") <= 0:
            raise ValueError("equity must be strictly positive")


@dataclass(frozen=True, slots=True)
class MetricEstimate:
    """A measured finite value OR an explicit reason why it is undefined/withheld."""

    value: Decimal | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("an undefined metric requires a reason")
        elif not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError("metric value must be a finite Decimal")
        elif self.reason is not None:
            raise ValueError("a defined metric cannot carry an undefined reason")


@dataclass(frozen=True, slots=True)
class DrawdownMetrics:
    """Fractional depth and integer elapsed microseconds, never observation counts.

    Ties choose the first deepest excursion. An equal high-water mark recovers
    the current excursion and becomes the next peak timestamp. Duration for the
    deepest excursion runs from its peak to recovery, or to the last observation
    when censored. Recovery time is trough-to-recovery, None if not recovered.
    Longest underwater duration is independent of which excursion was deepest;
    it includes a still-open excursion up to the final observed timestamp.
    """

    depth: Decimal
    peak_at: datetime | None
    trough_at: datetime | None
    recovered_at: datetime | None
    decline_duration_microseconds: int | None
    recovery_duration_microseconds: int | None
    episode_duration_microseconds: int | None
    episode_duration_censored: bool
    longest_underwater_duration_microseconds: int
    recovery_status: str


@dataclass(frozen=True, slots=True)
class TradeMetrics:
    """Closed-trade statistics: zero-PnL trades count, but are neither wins nor losses.

    Win rate is winning trades / all trades, including breakevens. Profit factor
    is total winning net PnL / absolute total losing net PnL. Average loss is
    reported as a positive magnitude. Payoff is average win / average loss;
    expectancy is arithmetic mean net PnL across all trades, not per unit risk.
    """

    count: int
    winning_count: int
    losing_count: int
    breakeven_count: int
    total_net_pnl: Decimal
    winning_net_pnl: Decimal
    losing_net_pnl_magnitude: Decimal
    win_rate: MetricEstimate
    average_win: MetricEstimate
    average_loss_magnitude: MetricEstimate
    profit_factor: MetricEstimate
    payoff_ratio: MetricEstimate
    expectancy: MetricEstimate


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    """Arithmetic subset only; none of these values authenticates input provenance."""

    equity_points: int
    period_returns: tuple[Decimal, ...]
    elapsed_years: Decimal
    total_return: Decimal
    cagr: MetricEstimate
    periods_per_year: Decimal
    annual_risk_free_rate: Decimal
    period_risk_free_rate: Decimal
    annualized_volatility: MetricEstimate
    sharpe: MetricEstimate
    out_of_sample_declared: bool
    minimum_oos_trades: int
    drawdown: DrawdownMetrics
    trades: TradeMetrics
    supplied_total_costs: Decimal | None
    supplied_gross_pnl: Decimal | None
    cost_ratio: MetricEstimate
    unimplemented_metrics: tuple[str, ...] = _UNIMPLEMENTED
    caveats: tuple[str, ...] = (
        "Arithmetic only: no data authorization, OOS verification or gate approval.",
        "Annualisation assumes equally spaced trading periods at the supplied frequency; "
        "no calendar validation, gap filling or serial-correlation adjustment is performed.",
        "Equity must be unitised or free of external cash flows; marks and closed trades "
        "need not reconcile because this module does not maintain a portfolio ledger.",
        "Supplied costs are not verified for completeness; a signed cost ratio with "
        "negative gross PnL is arithmetic, not a positive-gross fragility indicator.",
        "Passing the 200-trade counting guard alone does not establish statistical power.",
    )


def _drawdown(points: tuple[EquityPoint, ...]) -> DrawdownMetrics:
    peak = 0
    episode_peak: int | None = None
    deepest_peak: int | None = None
    trough: int | None = None
    recovery: int | None = None
    depth = Decimal(0)
    longest = timedelta(0)
    for index, point in enumerate(points[1:], 1):
        if point.equity >= points[peak].equity:
            if episode_peak is not None:
                longest = max(longest, point.timestamp - points[episode_peak].timestamp)
                if deepest_peak == episode_peak and recovery is None:
                    recovery = index
            episode_peak = None
            peak = index
            continue
        if episode_peak is None:
            episode_peak = peak
        longest = max(longest, point.timestamp - points[episode_peak].timestamp)
        current = (points[peak].equity - point.equity) / points[peak].equity
        if current > depth:
            depth, deepest_peak, trough, recovery = current, peak, index, None
    if deepest_peak is None or trough is None:
        return DrawdownMetrics(
            Decimal(0), None, None, None, None, None, None, False, 0, "NO_DRAWDOWN"
        )
    start, low = points[deepest_peak].timestamp, points[trough].timestamp
    end = points[recovery].timestamp if recovery is not None else points[-1].timestamp
    return DrawdownMetrics(
        depth,
        start,
        low,
        end if recovery is not None else None,
        _microseconds(low - start),
        _microseconds(end - low) if recovery is not None else None,
        _microseconds(end - start),
        recovery is None,
        _microseconds(longest),
        "RECOVERED" if recovery is not None else "UNRECOVERED_AT_END",
    )


def _trade_metrics(pnls: tuple[Decimal, ...]) -> TradeMetrics:
    wins = tuple(value for value in pnls if value > 0)
    losses = tuple(value.copy_abs() for value in pnls if value < 0)
    winning, losing = _cash_sum(wins), _cash_sum(losses)
    average_win = (
        MetricEstimate(winning / len(wins)) if wins else MetricEstimate(None, "NO_WINNING_TRADES")
    )
    average_loss = (
        MetricEstimate(losing / len(losses)) if losses else MetricEstimate(None, "NO_LOSING_TRADES")
    )
    if not wins or not losses:
        payoff = MetricEstimate(None, "WIN_AND_LOSS_REQUIRED")
    else:
        payoff = MetricEstimate((winning / len(wins)) / (losing / len(losses)))
    return TradeMetrics(
        len(pnls),
        len(wins),
        len(losses),
        len(pnls) - len(wins) - len(losses),
        _cash_sum(pnls),
        winning,
        losing,
        MetricEstimate(Decimal(len(wins)) / len(pnls))
        if pnls
        else MetricEstimate(None, "NO_TRADES"),
        average_win,
        average_loss,
        MetricEstimate(winning / losing) if losses else MetricEstimate(None, "NO_LOSING_TRADES"),
        payoff,
        MetricEstimate(_cash_sum(pnls) / len(pnls)) if pnls else MetricEstimate(None, "NO_TRADES"),
    )


def summarize_metrics(
    *,
    equity: tuple[EquityPoint, ...],
    net_trade_pnls: tuple[Decimal, ...],
    periods_per_year: Decimal,
    out_of_sample: bool,
    annual_risk_free_rate: Decimal = Decimal(0),
    total_costs: Decimal | None = None,
    gross_pnl: Decimal | None = None,
) -> BacktestMetrics:
    """Calculate the stated subset with no implicit frequency, costs or OOS claim.

    At least two marks with strictly increasing UTC timestamps are required.
    Two returns are needed for sample volatility. Costs and gross PnL must be
    supplied together; both absent means an undefined cost ratio, never zero
    costs. With zero gross PnL the ratio is undefined; negative gross and net
    credits retain their arithmetic signs, with the stated interpretation caveat.
    """
    if type(equity) is not tuple or len(equity) < 2:
        raise ValueError("equity must be an immutable tuple of at least two marks")
    if any(not isinstance(point, EquityPoint) for point in equity):
        raise TypeError("equity must contain EquityPoint values")
    if any(left.timestamp >= right.timestamp for left, right in pairwise(equity)):
        raise ValueError("equity timestamps must be strictly increasing")
    if type(net_trade_pnls) is not tuple:
        raise TypeError("net_trade_pnls must be an immutable tuple")
    for pnl in net_trade_pnls:
        _decimal(pnl, "net trade PnL")
    if _decimal(periods_per_year, "periods_per_year") <= 0:
        raise ValueError("periods_per_year must be positive")
    if _decimal(annual_risk_free_rate, "annual_risk_free_rate") <= -1:
        raise ValueError("annual_risk_free_rate must exceed -1")
    if type(out_of_sample) is not bool:
        raise TypeError("out_of_sample must be an explicit bool")
    if (total_costs is None) != (gross_pnl is None):
        raise ValueError("total_costs and gross_pnl must be supplied together")
    if total_costs is not None and gross_pnl is not None:
        _decimal(total_costs, "total_costs")
        _decimal(gross_pnl, "gross_pnl")
    try:
        with localcontext(_context()):
            return _summarize(
                equity,
                net_trade_pnls,
                periods_per_year,
                out_of_sample,
                annual_risk_free_rate,
                total_costs,
                gross_pnl,
            )
    except DecimalException as exc:
        raise ValueError("metrics exceed the supported numerical calculation range") from exc


def _summarize(
    points: tuple[EquityPoint, ...],
    pnls: tuple[Decimal, ...],
    frequency: Decimal,
    out_of_sample: bool,
    annual_risk_free: Decimal,
    costs: Decimal | None,
    gross: Decimal | None,
) -> BacktestMetrics:
    returns = tuple(right.equity / left.equity - 1 for left, right in pairwise(points))
    years = Decimal(_microseconds(points[-1].timestamp - points[0].timestamp)) / _YEAR_MICROSECONDS
    ratio = points[-1].equity / points[0].equity
    cagr = ratio ** (1 / years) - 1
    risk_free = (1 + annual_risk_free) ** (1 / frequency) - 1
    scale = frequency.sqrt()
    volatility = MetricEstimate(None, "AT_LEAST_TWO_PERIOD_RETURNS_REQUIRED")
    sharpe = MetricEstimate(None, "AT_LEAST_TWO_PERIOD_RETURNS_REQUIRED")
    if len(returns) >= 2:
        mean = sum(returns, Decimal(0)) / len(returns)
        # A constant risk-free return changes the mean, not sample volatility.
        # Subtracting a very large constant before centring would lose precision.
        deviation = (
            sum(((value - mean) ** 2 for value in returns), Decimal(0)) / (len(returns) - 1)
        ).sqrt()
        volatility = MetricEstimate(deviation * scale)
        if not deviation:
            sharpe = MetricEstimate(None, "ZERO_RETURN_VOLATILITY")
        elif out_of_sample and len(pnls) >= _MINIMUM_OOS_TRADES:
            sharpe = MetricEstimate((mean - risk_free) / deviation * scale)
    if not out_of_sample:
        sharpe = MetricEstimate(None, "OUT_OF_SAMPLE_NOT_DECLARED")
    elif len(pnls) < _MINIMUM_OOS_TRADES:
        sharpe = MetricEstimate(None, "FEWER_THAN_200_OOS_TRADES")
    if costs is None or gross is None:
        cost_ratio = MetricEstimate(None, "COSTS_AND_GROSS_PNL_NOT_SUPPLIED")
    else:
        cost_ratio = (
            MetricEstimate(costs / gross) if gross else MetricEstimate(None, "ZERO_GROSS_PNL")
        )
    return BacktestMetrics(
        len(points),
        returns,
        years,
        ratio - 1,
        MetricEstimate(cagr),
        frequency,
        annual_risk_free,
        risk_free,
        volatility,
        sharpe,
        out_of_sample,
        _MINIMUM_OOS_TRADES,
        _drawdown(points),
        _trade_metrics(pnls),
        costs,
        gross,
        cost_ratio,
    )
