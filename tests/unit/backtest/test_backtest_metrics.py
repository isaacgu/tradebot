"""Hand-known-answer backtest metrics; invented values are not financial evidence."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from typing import cast

import pytest

from tradebot.backtest.metrics import (
    BacktestMetrics,
    EquityPoint,
    MetricEstimate,
    summarize_metrics,
)
from tradebot.research.report import canonical_bytes

_START = datetime(2020, 1, 1, tzinfo=UTC)
_YEAR = timedelta(days=365, hours=6)
_DAY_MICROSECONDS = 86400 * 1000000


def _points(
    values: tuple[str, ...], *, step: timedelta = timedelta(days=1)
) -> tuple[EquityPoint, ...]:
    return tuple(
        EquityPoint(_START + index * step, Decimal(value)) for index, value in enumerate(values)
    )


def _metrics(
    values: tuple[str, ...] = ("100", "90", "90", "99"),
    *,
    pnls: tuple[Decimal, ...] = (),
    oos: bool = False,
    frequency: Decimal = Decimal(4),
    risk_free: Decimal = Decimal(0),
    costs: Decimal | None = None,
    gross: Decimal | None = None,
) -> BacktestMetrics:
    return summarize_metrics(
        equity=_points(values, step=_YEAR / 4),
        net_trade_pnls=pnls,
        periods_per_year=frequency,
        out_of_sample=oos,
        annual_risk_free_rate=risk_free,
        total_costs=costs,
        gross_pnl=gross,
    )


def test_period_returns_sample_volatility_and_sharpe_known_answer() -> None:
    result = _metrics(pnls=(Decimal(1),) * 200, oos=True)
    assert result.period_returns == (Decimal("-0.1"), Decimal(0), Decimal("0.1"))
    # Sample variance = (.01 + 0 + .01) / 2 = .01; sqrt(4) annualisation.
    assert result.annualized_volatility == MetricEstimate(Decimal("0.2"))
    assert result.sharpe == MetricEstimate(Decimal(0))
    assert result.total_return == Decimal("-0.01")
    assert result.elapsed_years == Decimal("0.75")
    assert result.minimum_oos_trades == 200


def test_effective_annual_risk_free_converts_geometrically() -> None:
    # (1.1)^4 - 1 = .4641, so the per-quarter risk-free return is exactly .1.
    result = _metrics(pnls=(Decimal(1),) * 200, oos=True, risk_free=Decimal("0.4641"))
    assert result.period_risk_free_rate == Decimal("0.1")
    assert result.annualized_volatility.value == Decimal("0.2")
    assert result.sharpe.value == Decimal(-2)  # (0 - .1) / .1 * sqrt(4).


def test_negative_risk_free_rate_is_allowed_above_minus_one() -> None:
    result = _metrics(
        pnls=(Decimal(1),) * 200, oos=True, frequency=Decimal(1), risk_free=Decimal("-0.1")
    )
    assert result.sharpe.value == Decimal(1)


def test_large_finite_risk_free_rate_cannot_erase_return_volatility() -> None:
    baseline = _metrics(frequency=Decimal(1))
    huge_rate = _metrics(frequency=Decimal(1), risk_free=Decimal("1e1000"))
    assert huge_rate.annualized_volatility == baseline.annualized_volatility
    assert huge_rate.annualized_volatility.value == Decimal("0.1")


def test_cagr_uses_actual_duration_not_frequency_or_mark_count() -> None:
    equity = _points(("100", "110", "121"), step=_YEAR)
    first = summarize_metrics(
        equity=equity, net_trade_pnls=(), out_of_sample=False, periods_per_year=Decimal(252)
    )
    second = summarize_metrics(
        equity=equity, net_trade_pnls=(), out_of_sample=False, periods_per_year=Decimal(12)
    )
    assert first.elapsed_years == Decimal(2)
    assert first.cagr.value == Decimal("0.1")
    assert second.cagr == first.cagr
    assert second.total_return == first.total_return == Decimal("0.21")


def test_short_partial_year_cagr_uses_actual_elapsed_microseconds() -> None:
    result = summarize_metrics(
        equity=_points(("100", "110"), step=_YEAR / 2),
        net_trade_pnls=(),
        periods_per_year=Decimal(2),
        out_of_sample=False,
    )
    assert result.elapsed_years == Decimal("0.5")
    assert result.cagr.value == Decimal("0.21")
    assert result.annualized_volatility.value is None
    assert result.annualized_volatility.reason == "AT_LEAST_TWO_PERIOD_RETURNS_REQUIRED"


@pytest.mark.parametrize(
    ("count", "oos", "reason"),
    [
        (0, True, "FEWER_THAN_200_OOS_TRADES"),
        (199, True, "FEWER_THAN_200_OOS_TRADES"),
        (200, False, "OUT_OF_SAMPLE_NOT_DECLARED"),
    ],
)
def test_sharpe_withheld_without_oos_trade_count_power(count: int, oos: bool, reason: str) -> None:
    result = _metrics(pnls=(Decimal(1),) * count, oos=oos)
    assert result.sharpe == MetricEstimate(None, reason)


def test_constant_period_returns_do_not_invent_infinite_sharpe() -> None:
    result = _metrics(("100", "110", "121"), pnls=(Decimal(1),) * 200, oos=True)
    assert result.annualized_volatility.value == 0
    assert result.sharpe == MetricEstimate(None, "ZERO_RETURN_VOLATILITY")


def test_recovered_drawdown_known_answer_and_longest_duration_are_separate() -> None:
    # The deepest excursion is 100 -> 80 -> 100 (two days). A later, shallower
    # excursion from 110 lasts four days, and must determine longest duration.
    result = summarize_metrics(
        equity=_points(("100", "80", "100", "110", "100", "105", "109", "110")),
        net_trade_pnls=(),
        periods_per_year=Decimal(365),
        out_of_sample=False,
    ).drawdown
    assert result.depth == Decimal("0.2")
    assert result.peak_at == _START
    assert result.trough_at == _START + timedelta(days=1)
    assert result.recovered_at == _START + timedelta(days=2)
    assert result.decline_duration_microseconds == _DAY_MICROSECONDS
    assert result.recovery_duration_microseconds == _DAY_MICROSECONDS
    assert result.episode_duration_microseconds == 2 * _DAY_MICROSECONDS
    assert not result.episode_duration_censored
    assert result.longest_underwater_duration_microseconds == 4 * _DAY_MICROSECONDS
    assert result.recovery_status == "RECOVERED"


def test_unrecovered_drawdown_does_not_invent_a_recovery_time() -> None:
    result = summarize_metrics(
        equity=_points(("100", "90", "80", "85")),
        net_trade_pnls=(),
        periods_per_year=Decimal(365),
        out_of_sample=False,
    ).drawdown
    assert result.depth == Decimal("0.2")
    assert result.decline_duration_microseconds == 2 * _DAY_MICROSECONDS
    assert result.recovery_duration_microseconds is None
    assert result.recovered_at is None
    assert result.episode_duration_microseconds == 3 * _DAY_MICROSECONDS
    assert result.episode_duration_censored
    assert result.recovery_status == "UNRECOVERED_AT_END"


def test_equal_high_recovers_and_resets_peak_while_depth_ties_choose_first() -> None:
    result = summarize_metrics(
        equity=_points(("100", "100", "80", "100", "80", "100")),
        net_trade_pnls=(),
        periods_per_year=Decimal(365),
        out_of_sample=False,
    ).drawdown
    assert result.peak_at == _START + timedelta(days=1)
    assert result.trough_at == _START + timedelta(days=2)
    assert result.recovered_at == _START + timedelta(days=3)
    assert result.longest_underwater_duration_microseconds == 2 * _DAY_MICROSECONDS


def test_no_drawdown_has_true_zero_depth_but_no_fabricated_dates() -> None:
    result = _metrics(("100", "100", "110")).drawdown
    assert result.depth == 0
    assert result.peak_at is result.trough_at is result.recovered_at is None
    assert result.decline_duration_microseconds is None
    assert result.recovery_duration_microseconds is None
    assert result.episode_duration_microseconds is None
    assert result.longest_underwater_duration_microseconds == 0
    assert result.recovery_status == "NO_DRAWDOWN"


def test_trade_metrics_hand_example_includes_breakeven_in_denominator() -> None:
    result = _metrics(pnls=tuple(map(Decimal, ("30", "10", "-10", "0")))).trades
    assert (result.count, result.winning_count, result.losing_count, result.breakeven_count) == (
        4,
        2,
        1,
        1,
    )
    assert result.total_net_pnl == 30
    assert result.winning_net_pnl == 40
    assert result.losing_net_pnl_magnitude == 10
    assert result.win_rate.value == Decimal("0.5")
    assert result.average_win.value == 20
    assert result.average_loss_magnitude.value == 10
    assert result.profit_factor.value == 4
    assert result.payoff_ratio.value == 2
    assert result.expectancy.value == Decimal("7.5")


@pytest.mark.parametrize("pnls", [(), (Decimal(0),), (Decimal(1),), (Decimal(-1),)])
def test_undefined_trade_ratios_carry_reasons_not_zero_or_infinity(
    pnls: tuple[Decimal, ...],
) -> None:
    result = _metrics(pnls=pnls).trades
    assert result.payoff_ratio.value is None
    assert result.payoff_ratio.reason == "WIN_AND_LOSS_REQUIRED"
    if not pnls:
        assert result.win_rate == MetricEstimate(None, "NO_TRADES")
        assert result.expectancy == MetricEstimate(None, "NO_TRADES")
    if not any(value < 0 for value in pnls):
        assert result.profit_factor == MetricEstimate(None, "NO_LOSING_TRADES")
    else:
        assert result.profit_factor == MetricEstimate(Decimal(0))


@pytest.mark.parametrize(
    ("costs", "gross", "expected"),
    [
        ("20", "100", "0.2"),
        ("20", "-100", "-0.2"),
        ("-10", "100", "-0.1"),
    ],
)
def test_signed_cost_ratio_is_supplied_not_inferred(costs: str, gross: str, expected: str) -> None:
    result = _metrics(costs=Decimal(costs), gross=Decimal(gross))
    assert result.cost_ratio.value == Decimal(expected)
    assert result.supplied_total_costs == Decimal(costs)


def test_missing_costs_and_zero_gross_have_distinct_undefined_reasons() -> None:
    assert _metrics().cost_ratio == MetricEstimate(None, "COSTS_AND_GROSS_PNL_NOT_SUPPLIED")
    assert _metrics(costs=Decimal(10), gross=Decimal(0)).cost_ratio == MetricEstimate(
        None, "ZERO_GROSS_PNL"
    )
    with pytest.raises(ValueError, match="supplied together"):
        _metrics(costs=Decimal(0))


def test_private_arithmetic_context_preserves_determinism_and_exact_cash() -> None:
    # Decimal cancellation must not lose the one-unit net profit.
    pnls = (Decimal("1e100"), Decimal(1), Decimal("-1e100"))
    expected = _metrics(pnls=pnls)
    with localcontext() as ambient:
        ambient.prec = 2
        ambient.rounding = ROUND_DOWN
        ambient.traps[Inexact] = True
        observed = _metrics(pnls=pnls)
    assert observed == expected
    assert observed.trades.total_net_pnl == 1
    assert canonical_bytes(observed) == canonical_bytes(expected)


@pytest.mark.parametrize(
    "value", [Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity"), Decimal("1e1001")]
)
def test_invalid_equity_is_rejected(value: Decimal) -> None:
    with pytest.raises(ValueError):
        EquityPoint(_START, value)


def test_equity_requires_exact_decimal_and_utc_datetime() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        EquityPoint(_START, cast(Decimal, 1.0))
    with pytest.raises(TypeError, match="datetime"):
        EquityPoint(cast(datetime, "2020-01-01"), Decimal(1))
    for stamp in (_START.replace(tzinfo=None), _START.astimezone(timezone(timedelta(hours=2)))):
        with pytest.raises(ValueError, match="UTC"):
            EquityPoint(stamp, Decimal(1))


def test_input_sequence_type_and_strict_chronology_are_required() -> None:
    for marks in (
        (),
        _points(("100",)),
        tuple(reversed(_points(("100", "110")))),
        (_points(("100",))[0],) * 2,
    ):
        with pytest.raises(ValueError):
            summarize_metrics(
                equity=marks, net_trade_pnls=(), periods_per_year=Decimal(1), out_of_sample=False
            )
    with pytest.raises(TypeError, match="EquityPoint"):
        summarize_metrics(
            equity=cast(tuple[EquityPoint, ...], (None, None)),
            net_trade_pnls=(),
            periods_per_year=Decimal(1),
            out_of_sample=False,
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        summarize_metrics(
            equity=_points(("100", "101")),
            net_trade_pnls=cast(tuple[Decimal, ...], []),
            periods_per_year=Decimal(1),
            out_of_sample=False,
        )


@pytest.mark.parametrize(
    "frequency", [Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity")]
)
def test_invalid_annualisation_factors_fail(frequency: Decimal) -> None:
    with pytest.raises(ValueError):
        _metrics(frequency=frequency)


@pytest.mark.parametrize("rate", [Decimal(-1), Decimal(-2), Decimal("NaN")])
def test_invalid_risk_free_rate_fails(rate: Decimal) -> None:
    with pytest.raises(ValueError):
        _metrics(risk_free=rate)


def test_oos_declaration_must_be_bool_and_pnls_must_be_finite_decimal() -> None:
    with pytest.raises(TypeError, match="bool"):
        _metrics(oos=cast(bool, 1))
    with pytest.raises(TypeError, match="Decimal"):
        _metrics(pnls=(cast(Decimal, 1.0),))
    with pytest.raises(ValueError, match="finite"):
        _metrics(pnls=(Decimal("NaN"),))


def test_unsupported_cagr_range_fails_instead_of_publishing_infinity() -> None:
    with pytest.raises(ValueError, match="numerical calculation range"):
        summarize_metrics(
            equity=_points(("1", "2"), step=timedelta(microseconds=1)),
            net_trade_pnls=(),
            periods_per_year=Decimal(1),
            out_of_sample=False,
        )


def test_estimates_and_results_are_frozen_and_missing_suite_is_explicit() -> None:
    result = _metrics()
    assert "SPEC_6_5_statistical_validation_suite" in result.unimplemented_metrics
    assert "sortino" in result.unimplemented_metrics
    with pytest.raises(FrozenInstanceError):
        result.total_return = Decimal(100)  # type: ignore[misc]
    with pytest.raises(ValueError, match="requires a reason"):
        MetricEstimate(None)
    with pytest.raises(ValueError, match="finite Decimal"):
        MetricEstimate(Decimal("NaN"))
    with pytest.raises(ValueError, match="undefined reason"):
        MetricEstimate(Decimal(0), "missing")
