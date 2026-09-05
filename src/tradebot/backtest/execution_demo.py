"""Run four fixed invented execution/accounting cases, never a market-data backtest.

This runner has no data-source, strategy, account-connection or trading switch.
It does not implement SimBroker, OMS, risk, paper/live execution or full costs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tradebot.backtest.costs import ConversionQuote, RoundTripInput, summarize_round_trip
from tradebot.backtest.market import MarketMatch, MarketModel, match_market_order
from tradebot.core.ids import new_client_order_id
from tradebot.core.types import OrderRequest, OrderType, Side, Tick
from tradebot.data.storage import FileDigest, sha256_path
from tradebot.research.registry import Registry
from tradebot.research.report import (
    SPEC_SHA256,
    canonical_bytes,
    implementation_identity,
    json_value,
)

_PAIR = "Synthetic/EURUSD"
_STRATEGY = "scripted-known-answer-not-a-strategy"


@dataclass(frozen=True, slots=True)
class _Case:
    name: str
    side: Side
    signal_event_time: datetime
    entry_submitted_at: datetime
    exit_submitted_at: datetime
    entry_ticks: tuple[Tick, ...]
    exit_ticks: tuple[Tick, ...]


def _ticks(submitted: datetime, bid: str, ask: str) -> tuple[Tick, ...]:
    # Quotes at submission and exactly at the latency boundary cannot fill.
    # Quote receipt/availability is deliberately later than venue execution.
    return tuple(
        Tick(
            instrument=_PAIR,
            ts_event=submitted + timedelta(milliseconds=offset),
            ts_recv=submitted + timedelta(milliseconds=offset, seconds=1),
            available_at=submitted + timedelta(milliseconds=offset, seconds=2),
            bid=Decimal(bid),
            ask=Decimal(ask),
        )
        for offset in (0, 150, 151)
    )


def _cases() -> tuple[_Case, ...]:
    start = datetime(2024, 1, 8, 10, tzinfo=UTC)
    cases: list[_Case] = []
    for ordinal, (name, side, bid, ask) in enumerate(
        (
            ("long-profit", Side.BUY, "1.10100", "1.10130"),
            ("long-loss", Side.BUY, "1.09900", "1.09930"),
            ("short-profit", Side.SELL, "1.09900", "1.09930"),
            ("short-loss", Side.SELL, "1.10100", "1.10130"),
        )
    ):
        signal_event = start + timedelta(minutes=ordinal)
        submitted = signal_event + timedelta(seconds=10)
        exit_submitted = submitted + timedelta(seconds=30)
        cases.append(
            _Case(
                name,
                side,
                signal_event,
                submitted,
                exit_submitted,
                _ticks(submitted, "1.10000", "1.10020"),
                _ticks(exit_submitted, bid, ask),
            )
        )
    return tuple(cases)


def _config() -> dict[str, object]:
    return {
        "latency_ms": 150,
        "slippage_price_per_fill": "0.00002",
        "max_full_fill_qty": "10000",
        "qty_base_units": "10000",
        "commission_quote_per_fill": "0.35",
        "financing_cashflow_quote_per_round_trip": "0",
        "foreign_currency": "USD",
        "account_currency": "ZAR",
        "direct_conversion_bid": "18.00",
        "direct_conversion_ask": "18.02",
        "calibration": "INVENTED_UNCALIBRATED",
        "financing_policy": "SUPPLIED_ZERO_IN_FIXED_INTRADAY_FIXTURE_NOT_BROKER_SCHEDULE",
        "settlement_policy": "SINGLE_SIGNED_NET_CASHFLOW_CONVERSION_AT_ROUND_TRIP_END",
    }


def _identity() -> tuple[FileDigest, ...]:
    identity = list(implementation_identity())
    for name in (
        "tradebot.core.ids",
        "tradebot.research.registry",
        "tradebot.backtest",
        "tradebot.backtest.market",
        "tradebot.backtest.costs",
        "tradebot.backtest.execution_demo",
    ):
        module = importlib.util.find_spec(name)
        if module is None or module.origin is None:
            raise RuntimeError(f"missing implementation source: {name}")
        identity.append(FileDigest(name, sha256_path(Path(module.origin))))
    return tuple(sorted(identity, key=lambda item: item.path))


def _git_sha(value: str) -> None:
    if value != "UNCOMMITTED" and re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("git_sha must be a full lowercase commit or UNCOMMITTED")


def synthetic_declaration(git_sha: str = "UNCOMMITTED") -> dict[str, object]:
    """Bind known fixtures and source bytes before an engineering attempt, not economic research."""
    _git_sha(git_sha)
    declaration = {
        "schema_version": 1,
        "evidence_class": "ENGINEERING_ONLY",
        "name": "fixed-synthetic-market-execution-accounting-v1",
        "hypothesis": "Venue-time fills and exact signed cashflow attribution reconcile.",
        "acceptance": "Four known answers; identical reports; retain failed attempts.",
        "economic_hypothesis": "NOT_PROPOSED",
        "financial_variants_tried": 0,
        "engineering_variants_declared": 1,
        "spec_sha256": SPEC_SHA256,
        "git_sha": git_sha,
        "implementation": json_value(_identity()),
        "config": _config(),
        "config_sha256": hashlib.sha256(canonical_bytes(_config())).hexdigest(),
        "fixture_sha256": hashlib.sha256(canonical_bytes(_cases())).hexdigest(),
        "fixture_access": "KNOWN_INVENTED_FIXTURE_NOT_BLIND_MARKET_RESEARCH",
        "python": platform.python_version(),
        "platform": platform.system(),
    }
    result = json.loads(canonical_bytes(declaration))
    if not isinstance(result, dict):
        raise TypeError("declaration must encode an object")
    return result


def _match(
    *,
    side: Side,
    submitted: datetime,
    ticks: tuple[Tick, ...],
    run_id: str,
    sequence: int,
    git_sha: str,
) -> MarketMatch:
    order = OrderRequest(
        client_order_id=new_client_order_id(
            "synthetic", _STRATEGY, _PAIR, submitted, run_id=run_id, sequence=sequence
        ),
        instrument=_PAIR,
        side=side,
        qty=Decimal("10000"),
        order_type=OrderType.MARKET,
        strategy_id=_STRATEGY,
        run_id=run_id,
        config_hash=hashlib.sha256(canonical_bytes(_config())).hexdigest(),
        git_sha=git_sha,
    )
    result = match_market_order(
        order,
        submitted_at=submitted,
        decision_available_at=submitted,
        ticks=ticks,
        model=MarketModel(
            latency=timedelta(milliseconds=150),
            slippage_price=Decimal("0.00002"),
            max_full_fill_qty=Decimal("10000"),
        ),
    )
    if result is None:
        raise RuntimeError("fixed known-answer fixture unexpectedly produced no fill")
    return result


def build_report(experiment_id: str, *, git_sha: str = "UNCOMMITTED") -> dict[str, object]:
    """Calculate the known fixture; a standalone result does not attest registration."""
    _git_sha(git_sha)
    if re.fullmatch(r"[0-9a-f]{64}", experiment_id) is None:
        raise ValueError("experiment_id must be a lowercase SHA-256 digest")
    fixture = _cases()
    cases: list[dict[str, object]] = []
    for ordinal, case in enumerate(fixture):
        entry = _match(
            side=case.side,
            submitted=case.entry_submitted_at,
            ticks=case.entry_ticks,
            run_id=experiment_id,
            sequence=ordinal * 2,
            git_sha=git_sha,
        )
        exit_fill = _match(
            side=Side.SELL if case.side == Side.BUY else Side.BUY,
            submitted=case.exit_submitted_at,
            ticks=case.exit_ticks,
            run_id=experiment_id,
            sequence=ordinal * 2 + 1,
            git_sha=git_sha,
        )
        amounts = summarize_round_trip(
            RoundTripInput(
                entry_side=case.side,
                qty=entry.fill.qty,
                entry_bid=entry.tick.bid,
                entry_ask=entry.tick.ask,
                exit_bid=exit_fill.tick.bid,
                exit_ask=exit_fill.tick.ask,
                entry_slippage=entry.slippage_price,
                exit_slippage=exit_fill.slippage_price,
                entry_commission=Decimal("0.35"),
                exit_commission=Decimal("0.35"),
                financing_cashflow=Decimal("0"),
            ),
            conversion=ConversionQuote(bid=Decimal("18.00"), ask=Decimal("18.02")),
        )
        if (entry.fill.price, exit_fill.fill.price) != (
            amounts.entry_fill_price,
            amounts.exit_fill_price,
        ):
            raise RuntimeError("executed fill prices differ from accounted prices")
        cases.append(
            {
                "name": case.name,
                "signal_event_time": json_value(case.signal_event_time),
                "decision_available_at": json_value(case.entry_submitted_at),
                "entry": json_value(entry),
                "exit": json_value(exit_fill),
                "accounting": json_value(amounts),
            }
        )
    return {
        "schema_version": 1,
        "evidence_class": "ENGINEERING_ONLY",
        "report_kind": "synthetic-execution-accounting-smoke",
        "experiment_id": experiment_id,
        "git_sha": git_sha,
        "spec_sha256": SPEC_SHA256,
        "implementation": json_value(_identity()),
        "status": "COMPLETED",
        "source_kind": "synthetic",
        "config": _config(),
        "fixture_sha256": hashlib.sha256(canonical_bytes(fixture)).hexdigest(),
        "cases": cases,
        "simulated_orders": 8,
        "simulated_fills": 8,
        "broker_orders": 0,
        "execution_enabled": False,
        "costs_modelled": False,
        "synthetic_cost_components_calculated": True,
        "synthetic_pnl_reported": True,
        "economic_evaluation": "NOT_PERFORMED",
        "data_acceptance": "NOT_ASSERTED",
        "gate_approvals_claimed": [],
        "caveats": [
            "Invented known answers, not signals, observed market data or strategy performance.",
            "MARKET/GTC only; no stops, limits, brackets, partials, sessions or margin.",
            "Full fills below the explicit synthetic size bound are an unverified assumption.",
            "Latency, slippage, commission and conversion quotes are invented and uncalibrated.",
            "Financing is explicitly supplied zero for this intraday fixture, not a swap model.",
            "No complete spread/size/ATR/jump model, broker schedule, dividends or futures rolls.",
            "Conversion is one direct-quote net settlement, not a broker multicurrency ledger.",
            "No risk, OMS, reconciliation, broker adapter or arrival-driven code parity.",
            "A returned historical fill is not permission to deliver it before availability.",
            "Cost ratio uses signed gross; undefined at zero and not a fragility test for losses.",
            "Only ratio is rounded (28 significant digits); money is not prematurely rounded.",
            "Local registry detects accidental corruption, not coordinated rewrite or time proof.",
        ],
    }


def _publish(report: dict[str, object], output_root: Path) -> dict[str, str]:
    payload = canonical_bytes(report)
    digest = hashlib.sha256(payload).hexdigest()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / digest
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=output_root) as temporary:
        pending = Path(temporary)
        with (pending / "report.json").open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            pending.rename(target)
        except OSError:
            artifact = target / "report.json"
            if target.is_symlink() or not target.is_dir():
                raise
            if artifact.is_symlink() or not artifact.is_file() or sha256_path(artifact) != digest:
                raise FileExistsError("immutable execution report differs") from None
    return {"report.json": digest}


def run_synthetic_attempt(
    registry: Registry,
    experiment_id: str,
    attempt_id: str,
    *,
    output_root: Path,
    git_sha: str = "UNCOMMITTED",
) -> dict[str, str]:
    """Retain START before calculation and FAILED on exceptions; never advance a latest pointer."""
    registry.start_attempt(
        experiment_id, attempt_id, metadata={"runner": "fixed-synthetic-market-accounting-v1"}
    )
    try:
        declaration = synthetic_declaration(git_sha)
        if registry.audit(experiment_id)["declaration"] != declaration:
            raise ValueError("current declaration differs from preregistration")
        report = build_report(experiment_id, git_sha=git_sha)
        if report["fixture_sha256"] != declaration["fixture_sha256"]:
            raise RuntimeError("consumed fixture differs from preregistration")
        if synthetic_declaration(git_sha) != declaration:
            raise RuntimeError("implementation or fixture changed during the attempt")
        artifacts = _publish(report, output_root)
        registry.finish_attempt(experiment_id, attempt_id, status="COMPLETED", artifacts=artifacts)
        return artifacts
    except Exception as exc:
        registry.finish_attempt(
            experiment_id, attempt_id, status="FAILED", error=f"{type(exc).__name__}: {exc}"
        )
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--git-sha", default="UNCOMMITTED")
    args = parser.parse_args(argv)
    registry = Registry(args.output_root / "registry.sqlite")
    experiment = registry.register(synthetic_declaration(args.git_sha))
    artifacts = run_synthetic_attempt(
        registry,
        experiment,
        args.attempt_id,
        output_root=args.output_root / "artifacts",
        git_sha=args.git_sha,
    )
    print(
        canonical_bytes(
            {
                "evidence_class": "ENGINEERING_ONLY",
                "artifacts": artifacts,
                "audit": registry.audit(experiment),
            }
        ).decode(),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
