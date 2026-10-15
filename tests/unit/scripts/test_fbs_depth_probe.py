"""End-to-end contract test for scripts/fbs_depth_probe.py against a stub terminal.

The scripts directory is unreachable by normal imports and the real MetaTrader5 package
has no Linux wheel, so this file installs a stub in sys.modules and drives main(). That
matters: an earlier live run failed on four stale references left behind by a rename in
tradebot.data.boundary_probe, and nothing in the suite could have caught it. mypy now
covers the script, but mypy cannot see a stale dict key or a mislabelled verdict, so the
report itself is asserted here.

The stub deliberately models a HEALTHY 17:00-New-York server plus one symbol whose every
data call never returns, so the timeout boundary is exercised on the same run.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
import time
import types
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

NEW_YORK = ZoneInfo("America/New_York")
STUCK = "US100"
TICK_FIELDS = ("time", "bid", "ask", "last", "volume", "time_msc", "flags", "volume_real")
STUB_ERROR = (-10004, "stub: no data")


class _Dtype:
    names = TICK_FIELDS


class _Recs(list):  # type: ignore[type-arg]
    """Enough of a numpy structured array for the probe: indexing plus dtype.names."""

    dtype = _Dtype()


class _Info:
    def __init__(self, **fields: object) -> None:
        self.__dict__.update(fields)


def _open_instant(day: date) -> datetime:
    """17:00 New York, so the UTC hour moves 21:00/22:00 with US DST as a real server does."""
    return datetime(day.year, day.month, day.day, 17, tzinfo=NEW_YORK).astimezone(UTC)


def _open_price(day: date) -> float:
    """Unique per session, so an anchor match cannot be a coincidence of repeated prices."""
    return round(1.25 + day.toordinal() % 4000 * 1e-05, 8)


def _is_session(day: date) -> bool:
    """Sunday-open through Thursday-open: five CLOSE-labelled sessions a week."""
    return day.weekday() not in (4, 5)


def _session_for(instant: datetime) -> date | None:
    for delta in (-1, 0, 1):
        day = (instant + timedelta(days=delta)).date()
        if _is_session(day) and _open_instant(day) == instant:
            return day
    return None


def _stub_module(
    blocked: threading.Event,
    *,
    stuck: str | None = STUCK,
    connected: bool = True,
    broker_is_fbs: bool = True,
    trade_mode: int = 0,
    non_bid: frozenset[str] = frozenset({"UK100"}),
) -> Any:
    # Any, not ModuleType: the whole point is to attach attributes a real module declares.
    module: Any = types.ModuleType("MetaTrader5")
    calls: list[str] = []

    def _d1(start: datetime, end: datetime) -> _Recs:
        out = _Recs()
        day = start.date()
        while day <= end.date():
            instant = _open_instant(day)
            if _is_session(day) and start <= instant <= end:
                out.append({"time": int(instant.timestamp()), "open": _open_price(day)})
            day += timedelta(days=1)
        return out

    def copy_rates_range(symbol: str, timeframe: int, start: datetime, end: datetime) -> Any:
        calls.append(f"rates:{symbol}")
        if symbol == stuck:
            blocked.wait()
            return None
        if timeframe == module.TIMEFRAME_M1 and end < datetime.now(UTC) - timedelta(days=21):
            return _Recs()
        return _d1(start, end)

    def copy_ticks_from(symbol: str, when: datetime, count: int, flags: int) -> Any:
        calls.append(f"ticks:{symbol}")
        if symbol == stuck:
            blocked.wait()
            return None
        if when < datetime(2011, 1, 1, tzinfo=UTC):
            earliest = datetime(2011, 1, 2, 22, tzinfo=UTC)
            return _Recs(
                [
                    {
                        "time": int(earliest.timestamp()),
                        "bid": 1.25,
                        "ask": 1.2501,
                        "last": 0.0,
                        "volume": 0,
                        "time_msc": int(earliest.timestamp()) * 1000,
                        "flags": 6,
                        "volume_real": 0.0,
                    }
                ]
            )
        session = _session_for(when)
        first = _open_price(session) if session else 9.99
        base = int(when.timestamp())
        return _Recs(
            {
                "time": base + i,
                "bid": first + i * 1e-05,
                "ask": first + i * 1e-05 + 1e-04,
                "last": 0.0,
                "volume": 0,
                "time_msc": (base + i) * 1000,
                "flags": 6,
                "volume_real": 0.0,
            }
            for i in range(min(count, 64))
        )

    module.__version__ = "5.0.5328-stub"
    module.COPY_TICKS_ALL = 3
    module.TIMEFRAME_M1 = 1
    module.TIMEFRAME_D1 = 16408
    module.ACCOUNT_TRADE_MODE_DEMO = 0
    module.ACCOUNT_MARGIN_MODE_RETAIL_NETTING = 1
    module.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2
    module.SYMBOL_CHART_MODE_BID = 0
    module.last_error = lambda: STUB_ERROR
    module.initialize = lambda **_: True
    module.shutdown = lambda: calls.append("shutdown")
    module.symbol_select = lambda *_: True
    module.terminal_info = lambda: _Info(
        build=4885,
        name="MetaTrader 5 (stub)",
        company="FBS Stub Ltd" if broker_is_fbs else "Other Broker Ltd",
        connected=connected,
        trade_allowed=True,
        maxbars=100_000,
    )
    module.account_info = lambda: _Info(
        trade_mode=trade_mode,
        server="FBS-Stub-Demo" if broker_is_fbs else "Other-Demo",
        currency="USD",
        company="FBS Stub Ltd" if broker_is_fbs else "Other Broker Ltd",
        margin_mode=2,
    )
    module.symbols_get = lambda: [
        _Info(name=name) for name in ("GBPUSD", "EURUSD", "US500", "UK100", "DE30", "US30", STUCK)
    ]
    module.symbol_info = lambda name: _Info(
        name=name,
        description=f"{name} stub",
        chart_mode=1 if name in non_bid else 0,
        digits=5,
        currency_profit="USD",
    )
    module.copy_rates_range = copy_rates_range
    module.copy_ticks_from = copy_ticks_from
    module._calls = calls
    return module


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One probe run for the whole module: every assertion reads the same report."""
    tmp_path = tmp_path_factory.mktemp("probe")
    blocked = threading.Event()
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, "MetaTrader5", _stub_module(blocked, stuck=None))
        patch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
        patch.delitem(sys.modules, "fbs_depth_probe", raising=False)
        probe = importlib.import_module("fbs_depth_probe")
        patch.setattr(probe, "CALL_TIMEOUT", timedelta(seconds=1))

        terminal = tmp_path / "terminal64.exe"
        terminal.write_bytes(b"stub")
        output = tmp_path / "report.json"
        patch.setattr(sys, "argv", ["probe", "--terminal", str(terminal), "--output", str(output)])

        started = time.monotonic()
        try:
            probe.main()
        finally:
            blocked.set()  # release the parked daemon threads before the suite moves on
        elapsed = time.monotonic() - started
        patch.delitem(sys.modules, "fbs_depth_probe", raising=False)

    payload: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
    payload["_elapsed_seconds"] = elapsed
    return payload


def test_every_aliased_symbol_is_probed_and_ger40_keeps_its_de30_mapping(
    report: dict[str, Any],
) -> None:
    assert set(report["depth"]) == {"GBPUSD", "EURUSD", "US500", "UK100", "GER40", "US30", STUCK}
    assert report["depth"]["GER40"]["broker_symbol"] == "DE30"
    assert report["symbols"]["unavailable"] == []
    assert report["status"] == "COMPLETE"


def test_the_report_never_carries_an_account_identifier(report: dict[str, Any]) -> None:
    """NN-5: the login number stays out of artifacts."""
    assert set(report["account"]) == {
        "server",
        "currency",
        "company",
        "margin_mode",
        "margin_mode_is_netting",
        "margin_mode_is_hedging",
        "is_demo",
    }


def test_a_seventeen_hundred_new_york_server_is_measured_as_one(report: dict[str, Any]) -> None:
    boundary = report["depth"]["GBPUSD"]["trading_day_boundary"]
    assert boundary["measured"] is True
    assert boundary["boundary_is_1700_new_york"] is True


def test_the_offset_zero_price_anchor_resolves_against_matching_ticks(
    report: dict[str, Any],
) -> None:
    anchor = report["depth"]["GBPUSD"]["trading_day_boundary"]["price_anchor"]
    assert anchor["resolved"] is True
    assert anchor["offset_hours_matching_bar_open"] == 0
    assert anchor["tied_offsets"] == []


def test_no_calendar_means_no_pass_from_either_verdict(report: dict[str, Any]) -> None:
    """A clean-looking sample is not gate-grade without SPEC 2.4's calendar."""
    boundary = report["depth"]["GBPUSD"]["trading_day_boundary"]
    assert boundary["weekly_audit"]["status"] == "INDETERMINATE"
    assert boundary["weekly_audit"]["calendar_supplied"] is False
    assert boundary["dst_fingerprint_10c"]["status"] == "PROVISIONALLY_ALIGNED"


def test_both_dst_mismatch_windows_are_reported_for_the_covered_years(
    report: dict[str, Any],
) -> None:
    windows = report["depth"]["GBPUSD"]["trading_day_boundary"]["dst_mismatch_windows"]
    assert {window["label"].rsplit("-", 1)[-1] for window in windows} == {"spring", "autumn"}
    assert all(window["transition_weeks"] for window in windows)


def test_bar_reach_is_never_reported_as_broker_depth(report: dict[str, Any]) -> None:
    for label in ("m1_bar_reach", "d1_bar_reach"):
        assert report["depth"]["GBPUSD"][label]["is_broker_depth"] is False


def test_non_bid_bars_cannot_receive_a_bid_anchor_verdict(report: dict[str, Any]) -> None:
    anchor = report["depth"]["UK100"]["trading_day_boundary"]["price_anchor"]

    assert anchor["resolved"] is False
    assert "not declared Bid-built" in anchor["reason"]


def test_the_limitations_disclose_that_a_timeout_is_absent_evidence(
    report: dict[str, Any],
) -> None:
    assert any("absent evidence" in line for line in report["limitations"])


class _HardExitCalled(RuntimeError):
    def __init__(self, code: int) -> None:
        self.code = code


def _import_probe(patch: pytest.MonkeyPatch, module: Any) -> Any:
    patch.setitem(sys.modules, "MetaTrader5", module)
    patch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    patch.delitem(sys.modules, "fbs_depth_probe", raising=False)
    return importlib.import_module("fbs_depth_probe")


def test_first_timeout_fail_stops_without_shutdown_and_preserves_canonical(
    tmp_path: Path,
) -> None:
    blocked = threading.Event()
    module = _stub_module(blocked)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(probe, "CALL_TIMEOUT", timedelta(milliseconds=50))
        patch.setattr(
            probe,
            "_hard_exit",
            lambda code: (_ for _ in ()).throw(_HardExitCalled(code)),
        )
        terminal = tmp_path / "terminal64.exe"
        terminal.write_bytes(b"stub")
        output = tmp_path / "report.json"
        output.write_text('{"canonical": true}\n', encoding="utf-8")
        patch.setattr(sys, "argv", ["probe", "--terminal", str(terminal), "--output", str(output)])

        try:
            with pytest.raises(_HardExitCalled) as stopped:
                probe.main()
        finally:
            blocked.set()
            patch.delitem(sys.modules, "fbs_depth_probe", raising=False)

    assert stopped.value.code == probe.TIMEOUT_EXIT_CODE
    assert json.loads(output.read_text(encoding="utf-8")) == {"canonical": True}
    partial = output.with_name("report.partial.json")
    payload = json.loads(partial.read_text(encoding="utf-8"))
    assert payload["status"] == "PARTIAL"
    assert payload["failure"]["kind"] == "TIMEOUT"
    assert "stopped waiting" in payload["failure"]["error"]
    assert str(STUB_ERROR[0]) not in payload["failure"]["error"]
    assert "shutdown" not in module._calls
    assert sum(call.startswith("ticks:US100") for call in module._calls) == 1


def test_non_timeout_incomplete_run_also_preserves_canonical(tmp_path: Path) -> None:
    blocked = threading.Event()
    module = _stub_module(blocked, stuck=None)
    module.symbols_get = lambda: []
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        terminal = tmp_path / "terminal64.exe"
        terminal.write_bytes(b"stub")
        output = tmp_path / "report.json"
        output.write_text('{"canonical": true}\n', encoding="utf-8")
        patch.setattr(sys, "argv", ["probe", "--terminal", str(terminal), "--output", str(output)])

        with pytest.raises(SystemExit, match="incomplete evidence"):
            probe.main()

        patch.delitem(sys.modules, "fbs_depth_probe", raising=False)

    assert json.loads(output.read_text(encoding="utf-8")) == {"canonical": True}
    payload = json.loads(output.with_name("report.partial.json").read_text(encoding="utf-8"))
    assert payload["status"] == "PARTIAL"
    assert payload["failure"]["kind"] == "INCOMPLETE_EVIDENCE"
    assert payload["failure"]["mt5_session_poisoned"] is False
    assert module._calls == ["shutdown"]


def test_mt5_error_is_snapshotted_on_the_worker_thread() -> None:
    blocked = threading.Event()
    module = _stub_module(blocked, stuck=None)
    local = threading.local()
    module.last_error = lambda: getattr(local, "error", (0, "unset"))
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)

        def failing_call() -> None:
            local.error = (-42, "worker-only")

        result = probe._bounded("thread-local-error", failing_call)
        patch.delitem(sys.modules, "fbs_depth_probe", raising=False)

    assert result.error == (-42, "worker-only")


def test_interrupting_a_join_with_a_live_worker_poison_stops_the_session() -> None:
    blocked = threading.Event()
    module = _stub_module(blocked, stuck=None)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)

        def interrupt_join(_thread: threading.Thread, _timeout: float | None = None) -> None:
            raise KeyboardInterrupt

        patch.setattr(threading.Thread, "join", interrupt_join)
        try:
            with pytest.raises(probe.ProbeTimeout):
                probe._bounded("interrupted", blocked.wait)
        finally:
            blocked.set()
            patch.delitem(sys.modules, "fbs_depth_probe", raising=False)


def test_terminal_branding_cannot_authenticate_another_brokers_account() -> None:
    blocked = threading.Event()
    module = _stub_module(blocked, stuck=None)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)

        assert not probe._is_fbs_demo(
            _Info(company="FBS Markets Inc."),
            _Info(company="Other Broker", server="Other-Demo"),
        )
        patch.delitem(sys.modules, "fbs_depth_probe", raising=False)


def test_safety_cap_exhaustion_marks_evidence_incomplete() -> None:
    blocked = threading.Event()
    module = _stub_module(blocked, stuck=None)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        evidence = {
            "symbols": {"unavailable": []},
            "depth": {
                "GBPUSD": {
                    "selected": True,
                    "specification": {"available": True},
                    "tick_history": {"earliest": "2011-01-01T00:00:00+00:00"},
                    "m1_bar_reach": {
                        "terminal_visible_earliest": "2013-01-01T00:00:00+00:00",
                        "stopped_because": (
                            "walk reached the safety step cap before the requested span"
                        ),
                        "plan_truncated": True,
                    },
                    "d1_bar_reach": {
                        "terminal_visible_earliest": "2006-01-01T00:00:00+00:00",
                        "stopped_because": "walk exhausted the planned windows",
                        "plan_truncated": False,
                    },
                    "trading_day_boundary": {"measured": True},
                    "tick_fields": {"available": True},
                }
            },
        }

        reasons = probe._incomplete_reasons(evidence)
        patch.delitem(sys.modules, "fbs_depth_probe", raising=False)

    assert reasons == ["GBPUSD: m1_bar_reach exhausted a truncated plan"]


def test_incomplete_anchor_candidates_mark_evidence_incomplete() -> None:
    blocked = threading.Event()
    module = _stub_module(blocked, stuck=None)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        evidence = {
            "symbols": {"unavailable": []},
            "depth": {
                "GBPUSD": {
                    "selected": True,
                    "specification": {"available": True},
                    "tick_history": {"earliest": "2011-01-01T00:00:00+00:00"},
                    "m1_bar_reach": {
                        "terminal_visible_earliest": "2013-01-01T00:00:00+00:00",
                        "stopped_because": "window returned no in-range bars",
                        "plan_truncated": True,
                    },
                    "d1_bar_reach": {
                        "terminal_visible_earliest": "2006-01-01T00:00:00+00:00",
                        "stopped_because": "walk exhausted the planned windows",
                        "plan_truncated": False,
                    },
                    "trading_day_boundary": {
                        "measured": True,
                        "price_anchor": {"incomplete_observations": 3},
                    },
                    "tick_fields": {"available": True},
                }
            },
        }

        reasons = probe._incomplete_reasons(evidence)
        patch.delitem(sys.modules, "fbs_depth_probe", raising=False)

    assert reasons == ["GBPUSD: price anchor has incomplete candidate observations"]


@pytest.mark.parametrize(
    ("stub_options", "message"),
    [
        ({"connected": False}, "disconnected"),
        ({"broker_is_fbs": False}, "does not identify as FBS"),
        ({"trade_mode": 1}, "not a demo"),
    ],
)
def test_wrong_terminal_identity_is_refused_before_evidence_is_written(
    tmp_path: Path, stub_options: dict[str, Any], message: str
) -> None:
    blocked = threading.Event()
    module = _stub_module(blocked, stuck=None, **stub_options)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        terminal = tmp_path / "terminal64.exe"
        terminal.write_bytes(b"stub")
        output = tmp_path / "report.json"
        patch.setattr(sys, "argv", ["probe", "--terminal", str(terminal), "--output", str(output)])

        with pytest.raises(SystemExit, match=message):
            probe.main()

        patch.delitem(sys.modules, "fbs_depth_probe", raising=False)

    assert not output.exists()
    assert module._calls == ["shutdown"]
