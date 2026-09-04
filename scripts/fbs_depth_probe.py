"""Measure a broker's MT5 history depth and trading-day boundary, for ADR-0007.

RUN ON WINDOWS. The `MetaTrader5` package ships Windows-only wheels with no source
distribution and talks by IPC to a running terminal, so it cannot run where the rest
of the suite runs. Nothing in `src/` imports this; CI never executes it.

    py -m pip install MetaTrader5 tzdata
    py scripts\\fbs_depth_probe.py ^
        --terminal "C:\\Program Files\\MetaTrader 5\\terminal64.exe" ^
        --output docs/reports/fbs-depth-probe.json

CREDENTIALS: never asked for, read or stored. `--terminal` names the executable and
`mt5.initialize(path=...)` attaches to the session you signed into by hand. The run
ABORTS unless the attached account reports itself as a demo.

METHOD, and what it cannot do.

*Ticks:* `copy_ticks_from(date, count=1)` returns the first tick at or after `date`,
so one call from 2000 yields the earliest retrievable tick. Confirmed working.

*Bars: NOT the same semantics.* `copy_rates_from(date, count)` counts BACKWARDS from
`date` and is bounded by the terminal's chart history, so a one-record request does
NOT reveal earliest bar depth — against this broker it fails outright at 2000, 2020
and 2026 alike. What this script reports is therefore the earliest bar the TERMINAL
can currently show, walked back window by window, and that is a floor on broker depth,
not a measurement of it. Resolving true M1 depth needs chart history built out with
`Max. bars in chart` unlimited; it remains open in ADR-0007.

*The boundary:* derived from D1 open instants, cross-checked against tick prices. A
histogram alone is ambiguous — 00:00 UTC opens are equally consistent with a genuine
UTC-midnight boundary and with server-local time encoded as UTC. Only the price anchor
in `_boundary_price_anchor` separates them. Do NOT re-add a claim that the histogram
settles it, and do not re-add a tick-epoch offset estimator: tick epochs are UTC, so
comparing them against the local clock measures nothing about the session offset.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5  # type: ignore[import-not-found]  # Windows-only, see docstring

# The session-week and DST-window logic is PRODUCTION code (SPEC 4.4 check 10(c)) and
# lives in the package with its own tests. Importing it from source keeps one
# implementation; the data package is stdlib-only, so no install is needed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tradebot.data.session_weeks import (
    audit_weekly_bars,
    dst_mismatch_windows,
)

NEW_YORK = ZoneInfo("America/New_York")

# Explicit aliases. No substring fallback: a suffixed variant is a DIFFERENT contract
# with its own spread, depth and specification, and silently probing one in place of
# the intended symbol would attribute the wrong numbers to the wrong instrument.
ALIASES: dict[str, str] = {
    "GBPUSD": "GBPUSD",
    "EURUSD": "EURUSD",
    "US500": "US500",
    "UK100": "UK100",
    "GER40": "DE30",
    "US30": "US30",
    "US100": "US100",
}

HISTORY_FLOOR = datetime(2000, 1, 1, tzinfo=UTC)
BOUNDARY_YEARS = 4
BAR_WALK_YEARS = 20
PRICE_ANCHOR_SAMPLES = 12
CANDIDATE_OFFSETS_HOURS = (0, -1, -2, -3, 1, 2, 3)
PROBE_VERSION = "boundary-pass-v3"


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"{message}: {mt5.last_error()}")


def _error() -> dict[str, Any]:
    code, text = mt5.last_error()
    return {"code": code, "message": text}


def _utc(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


def _resolve_symbols() -> dict[str, Any]:
    catalogue = mt5.symbols_get()
    if catalogue is None:
        _fail("symbols_get() returned nothing")
    names = {symbol.name for symbol in catalogue}
    resolved = {logical: broker for logical, broker in ALIASES.items() if broker in names}
    return {
        "total": len(names),
        "logical_to_broker": resolved,
        "aliases_declared": ALIASES,
        "unavailable": sorted(set(ALIASES) - set(resolved)),
        "near_matches": {
            logical: sorted(n for n in names if broker in n.upper() and n != broker)
            for logical, broker in ALIASES.items()
        },
    }


def _earliest_tick(symbol: str) -> dict[str, Any]:
    """Earliest retrievable tick. `copy_ticks_from` reads FORWARD, so count=1 works."""
    ticks = mt5.copy_ticks_from(symbol, HISTORY_FLOOR, 1, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"earliest": None, "error": _error(), "method": "copy_ticks_from(count=1)"}
    first = ticks[0]
    return {
        "earliest": _utc(int(first["time"])).isoformat(),
        "earliest_time_msc": int(first["time_msc"]),
        "method": "copy_ticks_from(count=1)",
    }


def _bar_reach(symbol: str, timeframe: int, label: str) -> dict[str, Any]:
    """How far back the TERMINAL can currently show bars — a floor, not broker depth.

    `copy_rates_from(date, count)` counts backwards and is bounded by chart history, so
    a one-record probe cannot find the earliest bar. This walks one-year windows back
    from now and stops at the first that yields nothing, recording why.
    """
    now = datetime.now(UTC)
    windows: list[dict[str, Any]] = []
    earliest: str | None = None
    for years_back in range(BAR_WALK_YEARS):
        end = now - timedelta(days=365 * years_back)
        start = end - timedelta(days=365)
        rates = mt5.copy_rates_range(symbol, timeframe, start, end)
        outcome: dict[str, Any] = {"from": start.date().isoformat(), "bars": 0}
        if rates is None:
            outcome["error"] = _error()
            windows.append(outcome)
            break
        outcome["bars"] = len(rates)
        if len(rates) == 0:
            windows.append(outcome)
            break
        earliest = _utc(int(rates[0]["time"])).isoformat()
        windows.append(outcome)
    return {
        "timeframe": label,
        "terminal_visible_earliest": earliest,
        "is_broker_depth": False,
        "note": "floor only; copy_rates_* is bounded by chart history and reads backwards",
        "windows": windows,
    }


def _boundary_price_anchor(symbol: str, rates: Any) -> dict[str, Any]:
    """Separate a true UTC boundary from server-local-encoded-as-UTC, using prices.

    For a sample of D1 bars, compare the bar's open price against the first tick at or
    after each candidate boundary instant (`epoch + offset`). The offset whose first
    tick reproduces the bar open is the real boundary. This is the only way to break
    the histogram's ambiguity, because both interpretations produce identical epochs.
    """
    sample = list(rates)[-PRICE_ANCHOR_SAMPLES:]
    scores: Counter[int] = Counter()
    checked = 0
    for rate in sample:
        opened = _utc(int(rate["time"]))
        bar_open = float(rate["open"])
        for offset in CANDIDATE_OFFSETS_HOURS:
            candidate = opened + timedelta(hours=offset)
            ticks = mt5.copy_ticks_from(symbol, candidate, 1, mt5.COPY_TICKS_ALL)
            if ticks is None or len(ticks) == 0:
                continue
            if abs(float(ticks[0]["bid"]) - bar_open) < 1e-9:
                scores[offset] += 1
        checked += 1
    if not scores:
        return {
            "resolved": False,
            "samples_checked": checked,
            "note": "no candidate offset reproduced a bar open; boundary unresolved",
        }
    best, hits = scores.most_common(1)[0]
    return {
        "resolved": hits >= max(1, checked // 2),
        "samples_checked": checked,
        "offset_hours_matching_bar_open": best,
        "matches": dict(scores),
        "epochs_are_true_utc": best == 0,
        "note": (
            "offset 0 means the reported epoch IS the boundary instant; a non-zero "
            "offset means the epoch is server-local wall time presented as UTC"
        ),
    }


def _d1_boundary(symbol: str) -> dict[str, Any]:
    """Measure the trading-day boundary from D1 open instants, then disambiguate it."""
    now = datetime.now(UTC)
    start = now - timedelta(days=365 * BOUNDARY_YEARS)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_D1, start, now)
    if rates is None or len(rates) == 0:
        return {"measured": False, "error": _error()}

    opens = [_utc(int(rate["time"])) for rate in rates]
    utc_open: Counter[str] = Counter(moment.strftime("%H:%M") for moment in opens)
    ny_open: Counter[str] = Counter(
        moment.astimezone(NEW_YORK).strftime("%H:%M") for moment in opens
    )
    audit = audit_weekly_bars(opens, zone=NEW_YORK)

    windows: list[dict[str, Any]] = []
    for year in range(start.year, now.year + 1):
        for window in dst_mismatch_windows(year):
            inside = {
                week: audit.counts.get(week, 0)
                for week in window.weeks
                if week in audit.interior_weeks
            }
            if inside:
                windows.append(
                    {
                        "label": window.label,
                        "first_session": window.first_session.isoformat(),
                        "last_session": window.last_session.isoformat(),
                        "weeks": inside,
                        "anomalous": {w: c for w, c in inside.items() if c != 5},
                    }
                )

    return {
        "measured": True,
        "bars": len(rates),
        "span": {"first": opens[0].isoformat(), "last": opens[-1].isoformat()},
        "utc_open_histogram": dict(sorted(utc_open.items())),
        "new_york_open_histogram": dict(sorted(ny_open.items())),
        "boundary_is_1700_new_york": set(ny_open) == {"17:00"},
        "histogram_alone_is_ambiguous": True,
        "price_anchor": _boundary_price_anchor(symbol, rates),
        "weekly_audit": {
            "interior_weeks": len(audit.interior_weeks),
            "missing_weeks": list(audit.missing_weeks),
            "weeks_not_five_bars": dict(audit.weeks_not_five),
            "duplicate_opens": list(audit.duplicate_opens),
            "clean": audit.clean,
        },
        "dst_mismatch_windows": windows,
    }


def _tick_fields(symbol: str, count: int = 10_000) -> dict[str, Any]:
    now = datetime.now(UTC)
    ticks = mt5.copy_ticks_from(symbol, now - timedelta(days=5), count, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"available": False, "error": _error()}
    two_sided = sum(1 for tick in ticks if tick["bid"] > 0 and tick["ask"] > 0)
    crossed = sum(
        1 for tick in ticks if tick["bid"] > 0 and tick["ask"] > 0 and tick["ask"] <= tick["bid"]
    )
    names = list(ticks.dtype.names)
    return {
        "available": True,
        "count": len(ticks),
        "dtype_names": names,
        "has_time_msc": "time_msc" in names,
        "both_sides_populated_count": two_sided,
        "both_sides_populated_fraction": two_sided / len(ticks),
        "crossed_or_locked_count": crossed,
        "sample": {name: str(ticks[-1][name]) for name in names},
        "note": "recent sample only; historical density is not established",
    }


def _specification(symbol: str) -> dict[str, Any]:
    """Capture the fields SPEC 2.3's startup verification compares.

    `session_open` / `session_close` are session open/close **PRICES**, not times, so
    they do not answer the trading-hours question. `trade_tick_value` is derived from
    the live conversion rate and moves daily: SPEC 2.4 requires it be read live and it
    MUST NOT be equality-compared against a cached config value.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        return {"available": False, "error": _error()}
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
        "trade_tick_size",
        "trade_tick_value",
        "volume_min",
        "volume_max",
        "volume_step",
        "trade_stops_level",
        "trade_freeze_level",
        "currency_base",
        "currency_profit",
        "currency_margin",
        "margin_initial",
        "spread",
        "spread_float",
        "session_open",
        "session_close",
    )
    return {"available": True} | {
        field: str(getattr(info, field)) for field in wanted if hasattr(info, field)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal", required=True, help="path to terminal64.exe (no credentials)")
    parser.add_argument("--output", type=Path, default=Path("fbs-depth-probe.json"))
    args = parser.parse_args()

    terminal_path = Path(args.terminal)
    if not terminal_path.is_file():
        raise SystemExit(f"terminal not found at {terminal_path}")
    if not mt5.initialize(path=str(terminal_path)):
        _fail("initialize() failed — is the terminal running and signed in?")

    terminal = mt5.terminal_info()
    account = mt5.account_info()
    if terminal is None or account is None:
        mt5.shutdown()
        _fail("terminal_info()/account_info() returned nothing")
    if account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        mt5.shutdown()
        raise SystemExit(
            f"refusing to run: attached account is not a demo (trade_mode="
            f"{account.trade_mode}). Sign into the demo account and retry."
        )

    symbols = _resolve_symbols()
    report: dict[str, Any] = {
        "probe_version": PROBE_VERSION,
        "probed_at_utc": datetime.now(UTC).isoformat(),
        "package_version": mt5.__version__,
        "terminal": {
            "build": terminal.build,
            "name": terminal.name,
            "company": terminal.company,
            "connected": terminal.connected,
            "trade_allowed": terminal.trade_allowed,
            "maxbars": terminal.maxbars,
            "path_supplied": str(terminal_path),
        },
        "account": {
            # NOT the login number: NN-5 keeps account identifiers out of artifacts.
            "server": account.server,
            "currency": account.currency,
            "company": account.company,
            "margin_mode": str(account.margin_mode),
            "margin_mode_is_netting": account.margin_mode == mt5.ACCOUNT_MARGIN_MODE_RETAIL_NETTING,
            "margin_mode_is_hedging": account.margin_mode == mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
            "is_demo": True,
        },
        "symbols": symbols,
        "depth": {},
        "limitations": [
            "Bar depth is TERMINAL-VISIBLE reach, not broker depth: copy_rates_* reads "
            "backwards and is bounded by chart history. True M1 depth is unresolved.",
            "A 00:00 UTC D1 open histogram is ambiguous between a genuine UTC boundary "
            "and server-local time encoded as UTC; only the price anchor separates them.",
            "Tick and bar epochs are UTC, so the session offset can never be inferred "
            "by comparing a quote epoch against the local clock.",
            "session_open/session_close are PRICES, not times; session hours remain "
            "unmeasured and need the session-quote/session-trade calls.",
            "trade_tick_value is live-rate derived; never cache or equality-compare it.",
            "Two-sidedness is a recent sample only; historical density is not established.",
            "Only the exact aliased symbols were probed; suffixed variants are listed "
            "under near_matches and were NOT measured.",
        ],
    }

    for logical, symbol in symbols["logical_to_broker"].items():
        if not mt5.symbol_select(symbol, True):
            report["depth"][logical] = {
                "broker_symbol": symbol,
                "selected": False,
                "error": _error(),
            }
            continue
        report["depth"][logical] = {
            "broker_symbol": symbol,
            "selected": True,
            "tick_history": _earliest_tick(symbol),
            "m1_bar_reach": _bar_reach(symbol, mt5.TIMEFRAME_M1, "M1"),
            "d1_bar_reach": _bar_reach(symbol, mt5.TIMEFRAME_D1, "D1"),
            "trading_day_boundary": _d1_boundary(symbol),
            "tick_fields": _tick_fields(symbol),
            "specification": _specification(symbol),
        }

    report["completed_at_utc"] = datetime.now(UTC).isoformat()
    mt5.shutdown()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {args.output}\n")
    for logical, found in report["depth"].items():
        if not found.get("selected"):
            print(f"{logical:8} NOT SELECTED")
            continue
        tick = (found["tick_history"].get("earliest") or "NONE")[:10]
        m1 = (found["m1_bar_reach"].get("terminal_visible_earliest") or "NONE")[:10]
        boundary = found["trading_day_boundary"]
        if boundary.get("measured"):
            anchor = boundary["price_anchor"]
            if anchor.get("resolved"):
                offset = anchor["offset_hours_matching_bar_open"]
                shape = "true UTC boundary" if offset == 0 else f"server-local, UTC{offset:+d}"
            else:
                shape = "UNRESOLVED"
            audit = boundary["weekly_audit"]
            flags = (
                f"{len(audit['weeks_not_five_bars'])} off-count, "
                f"{len(audit['missing_weeks'])} missing"
            )
        else:
            shape, flags = "UNMEASURED", "-"
        print(f"{logical:8} tick {tick}  M1 reach {m1}  boundary: {shape}  [{flags}]")


if __name__ == "__main__":
    main()
