"""Measure a broker's MT5 history depth and symbol facts, for ADR-0007.

ADR-0007 requires the broker's retrievable depth to be MEASURED before the index
route is decided, because deciding first would settle the largest question in the P1
plan on an assumption. This script is that measurement.

RUN THIS ON WINDOWS, not in the project's Linux venv. The `MetaTrader5` package
publishes Windows-only wheels with no source distribution, and it talks by IPC to a
running terminal — so it cannot be installed or run where the rest of the suite runs.
It is deliberately standalone: nothing in `src/` imports it, and CI never executes it.

    py -m pip install MetaTrader5
    py scripts\\fbs_depth_probe.py --output docs/reports/fbs-depth-probe.json

CREDENTIALS: this script never asks for, reads, or stores a password. It calls
`mt5.initialize()` with no login arguments, which attaches to the terminal session
you have already signed into by hand. Sign in to the DEMO account first, confirm
"Algo Trading" is enabled, and leave the terminal running.

Every number it prints is a per-broker fact that was marked [VERIFY] and could not be
asserted from documentation. Paste the JSON into ADR-0007 rather than transcribing.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import MetaTrader5 as mt5  # type: ignore[import-not-found]  # Windows-only, see module docstring

# SPEC 1.2's instruments, by the names this document uses. The broker's own strings
# differ (a broker may call the DAX contract DE30 rather than GER40) and may carry an
# account-type suffix, so every one of these is RESOLVED against the terminal's own
# symbol list rather than assumed.
WANTED = ("GBPUSD", "EURUSD", "US500", "UK100", "DE30", "GER40", "US30", "US100")

EARLIEST_YEAR = 2000
PROBE_WINDOW = timedelta(days=7)


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"{message}: {mt5.last_error()}")


def _resolve_symbols() -> dict[str, list[str]]:
    """Return, per wanted name, every broker symbol whose name contains it."""
    catalogue = mt5.symbols_get()
    if catalogue is None:
        _fail("symbols_get() returned nothing")
    names = sorted(symbol.name for symbol in catalogue)
    resolved: dict[str, list[str]] = {}
    for wanted in WANTED:
        resolved[wanted] = [name for name in names if wanted in name.upper()]
    resolved["__total_symbols__"] = [str(len(names))]
    return resolved


def _has_ticks(symbol: str, start: datetime) -> int:
    ticks = mt5.copy_ticks_range(symbol, start, start + PROBE_WINDOW, mt5.COPY_TICKS_ALL)
    return 0 if ticks is None else len(ticks)


def _has_bars(symbol: str, start: datetime) -> int:
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start, start + PROBE_WINDOW)
    return 0 if rates is None else len(rates)


def _earliest_year(symbol: str, probe: Any) -> dict[str, Any]:
    """Scan year by year, then month by month inside the first year with data.

    A coarse-then-fine scan rather than a binary search: an empty window can mean
    "no history" OR "market closed", and a bisection would happily converge on a
    false negative. Mid-June is probed to dodge holiday weeks.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    first_year: int | None = None
    per_year: dict[str, int] = {}
    for year in range(EARLIEST_YEAR, now.year + 1):
        count = probe(symbol, datetime(year, 6, 10))
        per_year[str(year)] = count
        if count and first_year is None:
            first_year = year
    if first_year is None:
        return {"earliest": None, "per_year": per_year}

    per_month: dict[str, int] = {}
    for year in (first_year - 1, first_year):
        if year < EARLIEST_YEAR:
            continue
        for month in range(1, 13):
            start = datetime(year, month, 1)
            if start > now:
                break
            per_month[f"{year}-{month:02d}"] = probe(symbol, start)
    earliest = min((label for label, count in per_month.items() if count), default=None)
    return {"earliest": earliest, "first_year_with_data": first_year, "per_month": per_month}


def _tick_fields(symbol: str) -> dict[str, Any]:
    """Dump what a tick array actually contains — settles several [VERIFY] items."""
    now = datetime.now(UTC).replace(tzinfo=None)
    ticks = mt5.copy_ticks_range(symbol, now - timedelta(days=3), now, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"available": False, "note": "no recent ticks; retry during market hours"}
    names = list(ticks.dtype.names)
    sample = ticks[-1]
    return {
        "available": True,
        "dtype_names": names,
        "has_time_msc": "time_msc" in names,
        "sample": {name: str(sample[name]) for name in names},
        "both_sides_populated": bool(sample["bid"]) and bool(sample["ask"]),
    }


def _server_offset(symbol: str) -> dict[str, Any]:
    """Estimate the server's UTC offset by the MINIMUM of (server - ours).

    Per ADR-0006 the minimum is the unbiased estimator: one-way delay is additive and
    non-negative, so a median is biased by the delay distribution. Quantised to whole
    hours, because MT5 broker offsets are whole hours in practice and a coarser
    quantum needs a larger local clock error to pick the wrong bucket.
    """
    samples: list[float] = []
    for _ in range(12):
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            continue
        ours = datetime.now(UTC).timestamp()
        samples.append(tick.time - ours)
    if not samples:
        return {"estimated": False, "note": "no live tick; run during market hours"}
    smallest = min(samples)
    return {
        "estimated": True,
        "raw_min_delta_seconds": smallest,
        "raw_max_delta_seconds": max(samples),
        "implied_offset_hours": round(smallest / 3600),
        "samples": len(samples),
        "note": "compare against 17:00 New York; a mismatch means the broker's day is not ours",
    }


def _specification(symbol: str) -> dict[str, Any]:
    """Capture the startup-verification fields SPEC 2.3 requires us to compare."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return {"available": False}
    wanted = (
        "name",
        "description",
        "path",
        "expiration_time",
        "expiration_mode",
        "trade_mode",
        "trade_calc_mode",
        "trade_exemode",
        "filling_mode",
        "swap_mode",
        "swap_long",
        "swap_short",
        "swap_rollover3days",
        "trade_contract_size",
        "point",
        "digits",
        "tick_size",
        "tick_value",
        "volume_min",
        "volume_max",
        "volume_step",
        "trade_stops_level",
        "trade_freeze_level",
        "currency_base",
        "currency_profit",
        "currency_margin",
        "margin_initial",
        "session_open",
        "session_close",
        "spread",
        "spread_float",
    )
    return {"available": True} | {
        field: str(getattr(info, field)) for field in wanted if hasattr(info, field)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("fbs-depth-probe.json"))
    args = parser.parse_args()

    if not mt5.initialize():
        _fail("initialize() failed — is the terminal running and signed in?")

    terminal = mt5.terminal_info()
    account = mt5.account_info()
    if terminal is None or account is None:
        _fail("terminal_info()/account_info() returned nothing")

    report: dict[str, Any] = {
        "probed_at_utc": datetime.now(UTC).isoformat(),
        "package_version": mt5.__version__,
        "terminal": {
            "build": terminal.build,
            "name": terminal.name,
            "company": terminal.company,
            "connected": terminal.connected,
            "trade_allowed": terminal.trade_allowed,
            "path": terminal.path,
        },
        "account": {
            # Deliberately NOT the login number: it is an account identifier, and
            # NN-5 keeps those out of artifacts.
            "server": account.server,
            "currency": account.currency,
            "company": account.company,
            "margin_mode": str(account.margin_mode),
            "margin_mode_is_netting": account.margin_mode == mt5.ACCOUNT_MARGIN_MODE_RETAIL_NETTING,
            "margin_mode_is_hedging": account.margin_mode == mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
            "is_demo": account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
        },
        "symbols": _resolve_symbols(),
        "depth": {},
    }

    for name in WANTED:
        candidates = report["symbols"].get(name, [])
        if not candidates:
            continue
        symbol = candidates[0]
        if not mt5.symbol_select(symbol, True):
            report["depth"][symbol] = {"selected": False, "error": str(mt5.last_error())}
            continue
        report["depth"][symbol] = {
            "selected": True,
            "resolved_from": name,
            "tick_history": _earliest_year(symbol, _has_ticks),
            "m1_bar_history": _earliest_year(symbol, _has_bars),
            "tick_fields": _tick_fields(symbol),
            "server_offset": _server_offset(symbol),
            "specification": _specification(symbol),
        }

    mt5.shutdown()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {args.output}")
    for symbol, found in report["depth"].items():
        if not found.get("selected"):
            continue
        ticks = found["tick_history"].get("earliest") or "NONE"
        bars = found["m1_bar_history"].get("earliest") or "NONE"
        offset = found["server_offset"].get("implied_offset_hours")
        # The depth figures are the headline result and print unconditionally; an
        # unknown offset only blanks its own column.
        server = "unknown" if offset is None else f"UTC{offset:+d}"
        print(f"{symbol:12} ticks from {ticks:>8}  M1 from {bars:>8}  server {server}")


if __name__ == "__main__":
    main()
