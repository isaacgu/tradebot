"""Broker MT5 depth and trading-day boundary probe, for ADR-0007.

RUN ON WINDOWS. The `MetaTrader5` package ships Windows-only wheels with no source
distribution and talks by IPC to a running terminal, so it cannot run where the rest
of the suite runs.

    py -m pip install MetaTrader5 tzdata
    py scripts\\fbs_depth_probe.py ^
        --terminal "C:\\Program Files\\MetaTrader 5\\terminal64.exe" ^
        --output docs/reports/fbs-depth-probe.json

CREDENTIALS: never asked for, read or stored. `--terminal` names the executable and
`mt5.initialize(path=...)` attaches to the session you signed into by hand. The run
ABORTS unless the attached account reports itself as a demo.

**This file is deliberately thin.** Every judgement — window planning, response
validation, anchor scoring, boundary summarising, weekly auditing — lives in
`tradebot.data.boundary_probe` and `tradebot.data.session_weeks`, which mypy and CI
cover. An earlier version carried that logic here, the weekly-audit API was renamed
underneath it, and four stale references only surfaced on a live broker run because
nothing in `scripts/` is reachable by the test suite. Keep new logic out of this file.

What the numbers do and do not mean:

* *Ticks:* `copy_ticks_from(date, count=1)` reads FORWARD, so one call from 2000 gives
  the earliest retrievable tick. Reliable.
* *Bars:* `copy_rates_*` reads BACKWARDS and is bounded by chart history, so no
  one-record trick works. What is reported is `terminal_visible_earliest` — a floor,
  not broker depth — and out-of-range answers are discarded, because a live run asked
  for 2012 and was handed a single 2026 bar.
* *Boundary:* a uniform 00:00 UTC histogram cannot distinguish a genuine UTC-midnight
  boundary from server-local time encoded as UTC. Only the price anchor separates them,
  and a tie is reported unresolved. Do not re-add a tick-epoch offset estimator: tick
  epochs are UTC, so comparing them against the local clock measures nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5  # type: ignore[import-not-found]  # Windows-only, see docstring

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tradebot.data.boundary_probe import (
    CANDIDATE_OFFSETS_HOURS,
    AnchorObservation,
    BarRecord,
    TickRecord,
    plan_bar_windows,
    score_anchor,
    summarise_bar_reach,
    summarise_boundary,
    validate_window_response,
)
from tradebot.data.session_weeks import dst_mismatch_windows, fingerprint_dst_alignment

NEW_YORK = ZoneInfo("America/New_York")

# Explicit aliases. No substring fallback: a suffixed variant is a DIFFERENT contract
# with its own spread, depth and specification.
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
BOUNDARY_SPAN = timedelta(days=365 * 4)
BAR_WALK_SPAN = timedelta(days=365 * 20)
PRICE_ANCHOR_SAMPLES = 12
PROBE_VERSION = "boundary-pass-v5"
RUN_TIMEOUT = timedelta(minutes=30)
SYMBOL_TIMEOUT = timedelta(minutes=5)
TIMEOUT_EXIT_CODE = 75

SPEC_FIELDS = (
    "name",
    "description",
    "path",
    "chart_mode",
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


def _fail(message: str, error: tuple[int, str] | None = None) -> NoReturn:
    suffix = f": {error[0]}: {error[1]}" if error is not None else ""
    raise SystemExit(message + suffix)


def _utc(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


CALL_TIMEOUT = timedelta(seconds=90)
_run_deadline: float | None = None
_symbol_deadline: float | None = None


@dataclass(frozen=True, slots=True)
class Mt5Call:
    """A result and its MT5 error snapshot, captured on the same worker thread."""

    value: Any
    error: tuple[int, str]


class ProbeTimeout(RuntimeError):
    """A call or deadline poisoned this process's MT5 IPC session."""

    def __init__(self, label: str, waited_seconds: float) -> None:
        self.label = label
        self.waited_seconds = waited_seconds
        super().__init__(f"{label}: probe stopped waiting after {waited_seconds:.0f}s")


def _timeout_text(timeout: ProbeTimeout) -> str:
    """A timeout is not an MT5 error, so never report ``last_error()`` for one."""
    return f"probe stopped waiting after {timeout.waited_seconds:.0f}s during {timeout.label}"


def _remaining_wait() -> float:
    limits = [CALL_TIMEOUT.total_seconds()]
    now = time.monotonic()
    if _run_deadline is not None:
        limits.append(max(0.0, _run_deadline - now))
    if _symbol_deadline is not None:
        limits.append(max(0.0, _symbol_deadline - now))
    return min(limits)


def _bounded(label: str, call: Callable[[], Any]) -> Mt5Call:
    """Run *call* on a daemon thread and stop waiting after :data:`CALL_TIMEOUT`.

    Be clear about what this does and does not do. An MT5 request is a blocking call
    into a C extension and cannot be cancelled from Python, so this bounds how long
    the PROBE waits — not how long the terminal works. The thread is a daemon, so a
    stuck request can never keep the process alive, and the run finishes with the
    timeout recorded instead of hanging indefinitely as the full v3 run did.

    A timeout raises :class:`ProbeTimeout`. The caller must fail-stop the entire process:
    the worker is still inside MT5, so this session may never be called or shut down again.
    """
    outcome: list[Mt5Call] = []
    failure: list[BaseException] = []
    completed = threading.Event()

    def _run() -> None:
        try:
            value = call()
            error = mt5.last_error()
            outcome.append(Mt5Call(value=value, error=(int(error[0]), str(error[1]))))
        except BaseException as exc:  # surfaced on the caller's thread below
            failure.append(exc)
        finally:
            completed.set()

    wait_seconds = _remaining_wait()
    if wait_seconds <= 0:
        raise ProbeTimeout(label, 0.0)
    thread = threading.Thread(target=_run, daemon=True, name=label)
    wait_started = time.monotonic()
    try:
        thread.start()
        thread.join(wait_seconds)
        if not completed.is_set():
            raise ProbeTimeout(label, wait_seconds)
    except ProbeTimeout:
        raise
    except BaseException as exc:
        # Conservatively poison even if the interrupt landed inside ``start()``: the
        # native worker may already exist although ``start()`` never returned.
        if not completed.is_set():
            raise ProbeTimeout(label, time.monotonic() - wait_started) from exc
        raise
    if failure:
        raise failure[0]
    if not outcome:
        raise RuntimeError(f"{label} ended without a result or exception")
    return outcome[0]


def _fetch_bar_opens(symbol: str, timeframe: int, start: datetime, end: datetime) -> Mt5Call:
    return _bounded(f"rates:{symbol}", lambda: mt5.copy_rates_range(symbol, timeframe, start, end))


def _fetch_ticks(symbol: str, when: datetime, count: int) -> Mt5Call:
    return _bounded(
        f"ticks:{symbol}", lambda: mt5.copy_ticks_from(symbol, when, count, mt5.COPY_TICKS_ALL)
    )


def _first_tick(symbol: str, when: datetime) -> tuple[TickRecord | None, str | None]:
    response = _fetch_ticks(symbol, when, 1)
    ticks = response.value
    if ticks is None or len(ticks) == 0:
        code, text = response.error
        return None, f"{code}: {text}"
    return TickRecord(time_msc=int(ticks[0]["time_msc"]), bid=float(ticks[0]["bid"])), None


def _earliest_tick(symbol: str) -> dict[str, Any]:
    response = _fetch_ticks(symbol, HISTORY_FLOOR, 1)
    tick = response.value
    if tick is None or len(tick) == 0:
        code, text = response.error
        return {
            "earliest": None,
            "error": f"{code}: {text}",
            "method": "copy_ticks_from(count=1)",
        }
    return {
        "earliest": _utc(int(tick[0]["time"])).isoformat(),
        "earliest_time_msc": int(tick[0]["time_msc"]),
        "method": "copy_ticks_from(count=1)",
    }


def _bar_reach(symbol: str, timeframe: int, label: str, bar_minutes: int, maxbars: int) -> Any:
    """Walk cap-sized windows back from now; all judgement is in the tested module."""
    requests = plan_bar_windows(
        now=datetime.now(UTC), maxbars=maxbars, bar_minutes=bar_minutes, span=BAR_WALK_SPAN
    )
    outcomes = []
    for request in requests:
        response = _fetch_bar_opens(symbol, timeframe, request.start, request.end)
        rates = response.value
        if rates is None:
            code, text = response.error
            outcomes.append(validate_window_response(request, [], error=f"{code}: {text}"))
            break
        outcome = validate_window_response(request, [_utc(int(rate["time"])) for rate in rates])
        outcomes.append(outcome)
        if not outcome.has_data:
            break
    reach = summarise_bar_reach(label, outcomes)
    return {
        "timeframe": reach.timeframe,
        "terminal_visible_earliest": (
            reach.terminal_visible_earliest.isoformat() if reach.terminal_visible_earliest else None
        ),
        "is_broker_depth": reach.is_broker_depth,
        "windows_walked": reach.windows_walked,
        "total_out_of_range": reach.total_out_of_range,
        "stopped_because": reach.stopped_because,
        "window_days": requests[0].span_days if requests else None,
        "expected_bars_per_window": requests[0].expected_bars if requests else None,
        "requested_span_days": reach.requested_span_days,
        "planned_span_days": reach.planned_span_days,
        "walked_span_days": reach.walked_span_days,
        "plan_truncated": reach.plan_truncated,
    }


def _anchor(symbol: str, opens: list[datetime], prices: list[float]) -> dict[str, Any]:
    observations = []
    for open_instant, price in list(zip(opens, prices, strict=True))[-PRICE_ANCHOR_SAMPLES:]:
        found: dict[int, TickRecord] = {}
        attempted: set[int] = set()
        failures: dict[int, str] = {}
        for offset in CANDIDATE_OFFSETS_HOURS:
            attempted.add(offset)
            tick, error = _first_tick(symbol, open_instant + timedelta(hours=offset))
            if tick is not None:
                found[offset] = tick
            else:
                failures[offset] = error or "no tick"
        observations.append(
            AnchorObservation(
                bar=BarRecord(open_instant=open_instant, open_price=price),
                first_ticks=found,
                attempted_offsets=frozenset(attempted),
                failures=failures,
            )
        )
    verdict = score_anchor(observations)
    return {
        "resolved": verdict.resolved,
        "offset_hours_matching_bar_open": verdict.offset_hours,
        "epochs_are_true_utc": verdict.epochs_are_true_utc,
        "matches": {str(k): v for k, v in verdict.matches.items()},
        # Per-offset denominators: how many bars actually TESTED each candidate.
        # Without these a reader cannot tell 7-of-7 from 7-of-12.
        "eligible": {str(k): v for k, v in verdict.eligible.items()},
        "tied_offsets": list(verdict.tied_offsets),
        "candidates_sharing_a_tick": verdict.shared_tick_discards,
        "observations_checked": verdict.observations,
        "samples_checked": verdict.samples,
        "incomplete_observations": verdict.incomplete_observations,
        "incomplete_reasons": list(verdict.incomplete_reasons),
        "reason": verdict.reason,
    }


def _boundary(symbol: str, *, anchor_supported: bool) -> dict[str, Any]:
    now = datetime.now(UTC)
    response = _fetch_bar_opens(symbol, mt5.TIMEFRAME_D1, now - BOUNDARY_SPAN, now)
    rates = response.value
    if rates is None or len(rates) == 0:
        code, text = response.error
        return {"measured": False, "error": f"{code}: {text}"}
    opens = [_utc(int(rate["time"])) for rate in rates]
    prices = [float(rate["open"]) for rate in rates]

    summary = summarise_boundary(opens, zone=NEW_YORK)
    audit = summary.audit
    # The check-10(c) verdict is distinct from the generic weekly audit: the audit can
    # be satisfied by any four quiet weeks, which says nothing about DST alignment.
    fingerprint = fingerprint_dst_alignment(opens, zone=NEW_YORK)
    windows = [
        {
            "label": window.label,
            "transition": window.transition.isoformat(),
            "first_full_session": window.first_full_session.isoformat(),
            "transition_weeks": list(window.transition_weeks),
            "fully_affected_weeks": list(window.fully_affected_weeks),
            "observed": dict(fingerprint.window_counts.get(window.label, {})),
            "fully_covered": window.label in fingerprint.covered_windows,
        }
        for year in range(opens[0].year, now.year + 1)
        for window in dst_mismatch_windows(year)
        if window.label in fingerprint.window_counts
    ]

    return {
        "measured": True,
        "bars": summary.bars,
        "span": {
            "first": summary.first_open.isoformat() if summary.first_open else None,
            "last": summary.last_open.isoformat() if summary.last_open else None,
        },
        "utc_open_histogram": summary.utc_open_histogram,
        "new_york_open_histogram": summary.local_open_histogram,
        "boundary_is_1700_new_york": summary.boundary_is_1700_local,
        "histogram_alone_is_ambiguous": summary.histogram_alone_is_ambiguous,
        "price_anchor": (
            _anchor(symbol, opens, prices)
            if anchor_supported
            else {
                "resolved": False,
                "offset_hours_matching_bar_open": None,
                "epochs_are_true_utc": None,
                "matches": {},
                "eligible": {},
                "tied_offsets": [],
                "candidates_sharing_a_tick": 0,
                "observations_checked": 0,
                "samples_checked": 0,
                "incomplete_observations": 0,
                "incomplete_reasons": [],
                "reason": "unsupported: symbol bars are not declared Bid-built",
            }
        ),
        "weekly_audit": {
            "status": str(audit.status),
            "reason": audit.reason,
            "calendar_supplied": audit.calendar_supplied,
            "interior_weeks": len(audit.interior_weeks),
            "missing_weeks": list(audit.missing_weeks),
            "weeks_excess": dict(audit.weeks_excess),
            "weeks_shortfall": dict(audit.weeks_shortfall),
            "uncovered_weeks": list(audit.uncovered_weeks),
            "duplicate_opens": list(audit.duplicate_opens),
            "ambiguous_closes": list(audit.ambiguous_closes),
            "ambiguous_weeks": list(audit.ambiguous_weeks),
        },
        "dst_fingerprint_10c": {
            "status": str(fingerprint.status),
            "reason": fingerprint.reason,
            "covered_windows": list(fingerprint.covered_windows),
            "partially_covered_windows": list(fingerprint.partially_covered_windows),
            "anomalous_weeks": dict(fingerprint.anomalous_weeks),
        },
        "dst_mismatch_windows": windows,
    }


def _tick_fields(symbol: str, count: int = 10_000) -> dict[str, Any]:
    response = _fetch_ticks(symbol, datetime.now(UTC) - timedelta(days=5), count)
    ticks = response.value
    if ticks is None or len(ticks) == 0:
        code, text = response.error
        return {"available": False, "error": f"{code}: {text}"}
    two_sided = sum(1 for t in ticks if t["bid"] > 0 and t["ask"] > 0)
    crossed = sum(1 for t in ticks if t["bid"] > 0 and t["ask"] > 0 and t["ask"] <= t["bid"])
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

    `chart_mode` is included because the price anchor compares a bar open against a
    tick BID, which is valid only while bars are bid-built — so the assumption is
    recorded as evidence. `session_open`/`session_close` are session open/close
    PRICES, not times. `trade_tick_value` is live-rate derived and must never be
    cached or equality-compared (SPEC 2.4).
    """
    response = _bounded(f"symbol-info:{symbol}", lambda: mt5.symbol_info(symbol))
    info = response.value
    if info is None:
        code, text = response.error
        return {"available": False, "error": f"{code}: {text}"}
    return {"available": True} | {
        field: str(getattr(info, field)) for field in SPEC_FIELDS if hasattr(info, field)
    }


def _resolve_symbols() -> dict[str, Any]:
    response = _bounded("symbols-get", mt5.symbols_get)
    catalogue = response.value
    if catalogue is None:
        _fail("symbols_get() returned nothing", response.error)
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


def _select_symbol(symbol: str) -> Mt5Call:
    """Select one exact contract through the bounded MT5 call boundary."""
    return _bounded(f"symbol-select:{symbol}", lambda: mt5.symbol_select(symbol, True))


def _hard_exit(code: int) -> NoReturn:
    """Terminate a process whose MT5 session has an in-flight timed-out call."""
    os._exit(code)


def _partial_path(output: Path) -> Path:
    """Return a sidecar path that can never replace canonical complete evidence."""
    return output.with_name(f"{output.stem}.partial{output.suffix}")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Durably write JSON beside *path*, then atomically replace the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _is_fbs_demo(_terminal: Any, account: Any) -> bool:
    """Bind an artifact named FBS to a connected FBS demo session."""
    account_identities = (
        str(getattr(account, "company", "")).strip().casefold(),
        str(getattr(account, "server", "")).strip().casefold(),
    )
    return any(value == "fbs" or value.startswith(("fbs ", "fbs-")) for value in account_identities)


def _incomplete_reasons(report: dict[str, Any]) -> list[str]:
    """Return missing measurement evidence that must not replace a canonical report."""
    reasons = [
        f"symbol unavailable: {logical}"
        for logical in report.get("symbols", {}).get("unavailable", [])
    ]
    for logical, evidence in report.get("depth", {}).items():
        if not evidence.get("selected"):
            reasons.append(f"{logical}: symbol selection failed")
            continue
        if evidence.get("specification", {}).get("available") is not True:
            reasons.append(f"{logical}: specification unavailable")
        if evidence.get("tick_history", {}).get("earliest") is None:
            reasons.append(f"{logical}: earliest tick unavailable")
        for key in ("m1_bar_reach", "d1_bar_reach"):
            reach = evidence.get(key, {})
            if reach.get("terminal_visible_earliest") is None:
                reasons.append(f"{logical}: {key} unavailable")
            if str(reach.get("stopped_because", "")).startswith("request error"):
                reasons.append(f"{logical}: {key} request error")
            if reach.get("plan_truncated") and "safety step cap" in str(
                reach.get("stopped_because", "")
            ):
                reasons.append(f"{logical}: {key} exhausted a truncated plan")
        if evidence.get("trading_day_boundary", {}).get("measured") is not True:
            reasons.append(f"{logical}: trading-day boundary unavailable")
        else:
            anchor = evidence["trading_day_boundary"].get("price_anchor", {})
            if int(anchor.get("incomplete_observations", 0)) > 0:
                reasons.append(f"{logical}: price anchor has incomplete candidate observations")
        if evidence.get("tick_fields", {}).get("available") is not True:
            reasons.append(f"{logical}: tick fields unavailable")
    return reasons


def _poisoned_exit(report: dict[str, Any], output: Path, timeout: ProbeTimeout) -> NoReturn:
    """Persist partial evidence without touching *output*, then kill every worker thread."""
    report["status"] = "PARTIAL"
    report["completed_at_utc"] = datetime.now(UTC).isoformat()
    report["failure"] = {
        "kind": "TIMEOUT",
        "call": timeout.label,
        "error": _timeout_text(timeout),
        "mt5_session_poisoned": True,
    }
    partial = _partial_path(output)
    report["partial_output"] = str(partial)
    report["canonical_output_preserved"] = str(output)
    try:
        _write_json_atomic(partial, report)
    finally:
        # Even a full disk must not return control to a process with live MT5 IPC.
        _hard_exit(TIMEOUT_EXIT_CODE)


def _incomplete_exit(report: dict[str, Any], output: Path, reasons: list[str]) -> NoReturn:
    """Persist a safely completed but incomplete run beside canonical evidence."""
    report["status"] = "PARTIAL"
    report["completed_at_utc"] = datetime.now(UTC).isoformat()
    report["failure"] = {
        "kind": "INCOMPLETE_EVIDENCE",
        "reasons": reasons,
        "mt5_session_poisoned": False,
    }
    partial = _partial_path(output)
    report["partial_output"] = str(partial)
    report["canonical_output_preserved"] = str(output)
    _write_json_atomic(partial, report)
    raise SystemExit("probe completed with incomplete evidence; canonical output was preserved")


def main() -> None:
    global _run_deadline, _symbol_deadline

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal", required=True, help="path to terminal64.exe")
    parser.add_argument("--output", type=Path, default=Path("fbs-depth-probe.json"))
    args = parser.parse_args()

    terminal_path = Path(args.terminal)
    if not terminal_path.is_file():
        raise SystemExit(f"terminal not found at {terminal_path}")
    report: dict[str, Any] = {
        "status": "RUNNING",
        "probe_version": PROBE_VERSION,
        "probed_at_utc": datetime.now(UTC).isoformat(),
        "package_version": mt5.__version__,
        "terminal": {"path_supplied": str(terminal_path)},
        "depth": {},
        "limitations": [
            "Bar depth is TERMINAL-VISIBLE reach, not broker depth: copy_rates_* reads "
            "backwards and is bounded by chart history. True M1 depth is unresolved.",
            "Out-of-range bars are discarded: a live run requested 2012 and received a "
            "single 2026 bar, which must not be read as depth.",
            "A 00:00 UTC D1 histogram is ambiguous between a genuine UTC boundary and "
            "server-local time encoded as UTC; only the price anchor separates them.",
            "A tied anchor is reported unresolved, never broken by candidate order.",
            "Tick epochs are UTC, so the session offset can never be inferred by "
            "comparing a quote epoch against the local clock.",
            "session_open/session_close are PRICES, not times; session hours remain "
            "unmeasured and need the session-quote/session-trade calls.",
            "trade_tick_value is live-rate derived; never cache or equality-compare it.",
            "Neither the weekly audit nor the 10(c) fingerprint can PASS without SPEC 2.4's "
            "expected-liquidity calendar, and this probe supplies none.",
            "Two-sidedness is a recent sample only; historical density is not established.",
            "Only the exact aliased symbols were probed; near_matches were NOT measured.",
            f"Every MT5 request is bounded to {CALL_TIMEOUT.total_seconds():.0f}s of "
            "PROBE waiting on a daemon thread. A stuck request is recorded as a timeout "
            "instead of hanging the run, but it is not cancelled inside the terminal, so "
            "a timed-out measurement is absent evidence, not a negative result.",
            "The first timeout poisons the MT5 IPC session. No later MT5 call or shutdown "
            "is attempted; a PARTIAL sidecar is fsynced and the process exits nonzero.",
            "A PARTIAL run never replaces the requested complete-evidence output.",
        ],
    }
    initialized = False
    _run_deadline = time.monotonic() + RUN_TIMEOUT.total_seconds()
    try:
        initialized_call = _bounded("initialize", lambda: mt5.initialize(path=str(terminal_path)))
        if not initialized_call.value:
            _fail(
                "initialize() failed — is the terminal running and signed in?",
                initialized_call.error,
            )
        initialized = True

        terminal_call = _bounded("terminal-info", mt5.terminal_info)
        account_call = _bounded("account-info", mt5.account_info)
        terminal, account = terminal_call.value, account_call.value
        if terminal is None:
            _fail("terminal_info() returned nothing", terminal_call.error)
        if account is None:
            _fail("account_info() returned nothing", account_call.error)
        if not terminal.connected:
            _fail("refusing to run: terminal is disconnected")
        if account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            _fail(
                f"refusing to run: attached account is not a demo (trade_mode={account.trade_mode})"
            )
        if not _is_fbs_demo(terminal, account):
            _fail(
                "refusing to run: attached demo does not identify as FBS "
                f"(server={account.server!r}, company={account.company!r})"
            )

        report["terminal"] = {
            "build": terminal.build,
            "name": terminal.name,
            "company": terminal.company,
            "connected": terminal.connected,
            "trade_allowed": terminal.trade_allowed,
            "maxbars": terminal.maxbars,
            "path_supplied": str(terminal_path),
        }
        report["account"] = {
            # NOT the login number: NN-5 keeps account identifiers out of artifacts.
            "server": account.server,
            "currency": account.currency,
            "company": account.company,
            "margin_mode": str(account.margin_mode),
            "margin_mode_is_netting": (
                account.margin_mode == mt5.ACCOUNT_MARGIN_MODE_RETAIL_NETTING
            ),
            "margin_mode_is_hedging": (
                account.margin_mode == mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
            ),
            "is_demo": True,
        }

        symbols = _resolve_symbols()
        report["symbols"] = symbols
        for logical, symbol in symbols["logical_to_broker"].items():
            _symbol_deadline = time.monotonic() + SYMBOL_TIMEOUT.total_seconds()
            selected = _select_symbol(symbol)
            if not selected.value:
                code, text = selected.error
                report["depth"][logical] = {
                    "broker_symbol": symbol,
                    "selected": False,
                    "error": f"{code}: {text}",
                }
                continue
            specification = _specification(symbol)
            bid_mode = str(getattr(mt5, "SYMBOL_CHART_MODE_BID", 0))
            anchor_supported = (
                specification.get("available") is True
                and specification.get("chart_mode") == bid_mode
            )
            report["depth"][logical] = {
                "broker_symbol": symbol,
                "selected": True,
                "specification": specification,
                "tick_history": _earliest_tick(symbol),
                "m1_bar_reach": _bar_reach(symbol, mt5.TIMEFRAME_M1, "M1", 1, terminal.maxbars),
                "d1_bar_reach": _bar_reach(symbol, mt5.TIMEFRAME_D1, "D1", 1440, terminal.maxbars),
                "trading_day_boundary": _boundary(symbol, anchor_supported=anchor_supported),
                "tick_fields": _tick_fields(symbol),
            }
        _symbol_deadline = None
        _bounded("shutdown", mt5.shutdown)
        initialized = False
    except ProbeTimeout as timeout:
        _poisoned_exit(report, args.output, timeout)
    except BaseException:
        _symbol_deadline = None
        if initialized:
            try:
                _bounded("shutdown-after-error", mt5.shutdown)
            except ProbeTimeout as timeout:
                _poisoned_exit(report, args.output, timeout)
        raise
    finally:
        _run_deadline = None
        _symbol_deadline = None

    incomplete = _incomplete_reasons(report)
    if incomplete:
        _incomplete_exit(report, args.output, incomplete)
    report["status"] = "COMPLETE"
    report["completed_at_utc"] = datetime.now(UTC).isoformat()
    _write_json_atomic(args.output, report)
    print(f"wrote {args.output}\n")
    modes: Counter[str] = Counter()
    for logical, found in report["depth"].items():
        if not found.get("selected"):
            print(f"{logical:8} NOT SELECTED")
            continue
        modes[found["specification"].get("chart_mode", "?")] += 1
        tick = (found["tick_history"].get("earliest") or "NONE")[:10]
        m1 = (found["m1_bar_reach"].get("terminal_visible_earliest") or "NONE")[:10]
        boundary = found["trading_day_boundary"]
        if boundary.get("measured"):
            anchor = boundary["price_anchor"]
            shape = (
                f"offset {anchor['offset_hours_matching_bar_open']:+d}"
                if anchor["resolved"]
                else f"UNRESOLVED ({anchor['reason'][:40]})"
            )
            status = boundary["dst_fingerprint_10c"]["status"]
        else:
            shape, status = "UNMEASURED", "-"
        print(f"{logical:8} tick {tick}  M1 reach {m1}  anchor {shape}  10c {status}")
    print(f"\nchart_mode across probed symbols: {dict(modes)}")


if __name__ == "__main__":
    main()
