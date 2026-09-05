from __future__ import annotations

import json
import os
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tradebot.monitoring import broker_exporter as broker


class FakeMT5:
    def __init__(self) -> None:
        self.initializations = 0
        self.shutdowns = 0
        self.reads = 0
        self.account_reads = 0
        self.account_sequence: list[object | None] = []
        self.quote_symbols: list[str] = []
        self.connected = True
        self.initialize_result = True
        self.account: object | None = SimpleNamespace(
            server="FBS-Demo",
            currency="USD",
            trade_mode=0,
            balance=1000.0,
            equity=1012.0,
            margin=10.0,
            margin_free=1002.0,
            profit=12.0,
            margin_level=10120.0,
            login=99887766,
            name="PRIVATE PERSON",
            password="NEVER SERIALIZE",  # noqa: S106 -- synthetic redaction fixture
        )
        self.positions: tuple[object, ...] | None = (position(),)
        self.orders: tuple[object, ...] | None = (order(),)
        self.quote: object | None = SimpleNamespace(bid=1.09, ask=1.0901, time_msc=1788553000000)
        self.block: threading.Event | None = None

    def initialize(self, *, path: str, timeout: int) -> bool:
        assert path == "terminal64.exe"
        assert timeout == 10000
        self.initializations += 1
        return self.initialize_result

    def terminal_info(self) -> object:
        return SimpleNamespace(connected=self.connected, build=6140)

    def account_info(self) -> object | None:
        self.account_reads += 1
        if self.account_sequence:
            return self.account_sequence.pop(0)
        return self.account

    def positions_get(self) -> tuple[object, ...] | None:
        self.reads += 1
        if self.block is not None:
            self.block.wait(1)
        return self.positions

    def orders_get(self) -> tuple[object, ...] | None:
        return self.orders

    def symbol_info_tick(self, symbol: str) -> object | None:
        self.quote_symbols.append(symbol)
        return self.quote

    def last_error(self) -> tuple[int, str]:
        return -1, "native secret that must not be serialized"

    def shutdown(self) -> None:
        self.shutdowns += 1


def position(**updates: Any) -> SimpleNamespace:
    values = {
        "ticket": 12345,
        "symbol": "EURUSD",
        "type": 0,
        "volume": 0.2,
        "price_open": 1.08,
        "price_current": 1.09,
        "sl": 1.07,
        "tp": 1.1,
        "profit": 12.0,
        "swap": -0.2,
        "time_msc": 1788550000123,
        "comment": "PRIVATE COMMENT",
        "magic": 123456,
        "identifier": 98765,
    }
    return SimpleNamespace(**(values | updates))


def order(**updates: Any) -> SimpleNamespace:
    values = {
        "ticket": 54321,
        "symbol": "EURUSD",
        "type": 2,
        "volume_current": 0.1,
        "price_open": 1.05,
        "price_current": 1.09,
        "sl": 1.04,
        "tp": 1.06,
        "price_stoplimit": 0.0,
        "time_setup_msc": 1788550000456,
        "time_expiration": 0,
        "comment": "PRIVATE ORDER COMMENT",
        "external_id": "NEVER EXPORT",
    }
    return SimpleNamespace(**(values | updates))


def reader(api: FakeMT5, clock: list[float] | None = None) -> broker.BrokerReader:
    return broker.BrokerReader(
        Path("terminal64.exe"),
        loader=lambda: api,
        terminal_checker=lambda path: True,
        clock=(lambda: clock[0]) if clock else (lambda: 100.0),
    )


def test_whitelist_actual_positions_and_account_kind() -> None:
    api = FakeMT5()
    monitor = broker.BrokerMonitor(reader(api), clock=lambda: 100.0)
    assert monitor.poll_once()
    status = monitor.status()
    encoded = json.dumps(status)
    for private in ("99887766", "PRIVATE", "NEVER", "password", '"login"', '"magic"'):
        assert private not in encoded
    snapshot = status["snapshot"]
    assert snapshot["account"]["account_kind"] == "demo"
    assert snapshot["account"]["equity"] == 1012.0
    assert snapshot["positions"][0]["origin"] == "not_bot_certified"
    assert snapshot["positions"][0]["ticket"] == "12345"
    assert snapshot["positions"][0]["open_timestamp_seconds"] == 1788550000.123
    assert snapshot["orders"][0]["side"] == "buy_limit"
    assert snapshot["quotes"][0]["bid"] == 1.09
    assert not status["snapshot_stale"]
    assert monitor.poll_once()
    assert api.initializations == 1


@pytest.mark.parametrize(
    ("kind", "expected"), [(0, "demo"), (1, "contest"), (2, "live"), (9, "unknown")]
)
def test_account_kind_is_observed_not_assumed(kind: int, expected: str) -> None:
    api = FakeMT5()
    assert isinstance(api.account, SimpleNamespace)
    api.account.trade_mode = kind
    assert reader(api).read().account["account_kind"] == expected


def test_empty_only_after_successful_reads() -> None:
    api = FakeMT5()
    monitor = broker.BrokerMonitor(reader(api), clock=lambda: 100.0)
    initial = broker.render_metrics(monitor.status()).decode()
    assert "tradebot_broker_position_count" not in initial
    assert monitor.status()["snapshot_stale"]
    api.positions = ()
    api.orders = ()
    assert monitor.poll_once()
    metrics = broker.render_metrics(monitor.status()).decode()
    assert "tradebot_broker_position_count 0.0" in metrics
    assert "tradebot_broker_order_count 0.0" in metrics
    assert api.quote_symbols == []


@pytest.mark.parametrize("failing_field", ["positions", "orders", "account"])
def test_failed_query_preserves_stale_success(failing_field: str) -> None:
    api = FakeMT5()
    clock = [100.0]
    monitor = broker.BrokerMonitor(reader(api, clock), clock=lambda: clock[0])
    assert monitor.poll_once()
    setattr(api, failing_field, None)
    clock[0] = 105.0
    assert not monitor.poll_once()
    status = monitor.status()
    assert status["snapshot_stale"]
    assert status["last_success_timestamp_seconds"] == 100.0
    assert len(status["snapshot"]["positions"]) == 1
    assert status["poll_errors_total"] == 1
    assert "native secret" not in json.dumps(status)
    assert "tradebot_broker_position_count 1.0" in broker.render_metrics(status).decode()


def test_disconnected_and_expired_snapshot() -> None:
    api = FakeMT5()
    clock = [100.0]
    monitor = broker.BrokerMonitor(reader(api, clock), clock=lambda: clock[0])
    assert monitor.poll_once()
    clock[0] = 116
    assert monitor.status()["snapshot_stale"]
    api.connected = False
    assert not monitor.poll_once()
    assert monitor.status()["last_error"] == "terminal_disconnected"


def test_no_terminal_launch_without_verified_running_process() -> None:
    api = FakeMT5()
    probe = broker.BrokerReader(
        Path("terminal64.exe"), loader=lambda: api, terminal_checker=lambda path: False
    )
    with pytest.raises(broker.BrokerReadError, match="terminal_not_running"):
        probe.read()
    assert api.initializations == 0
    api.initialize_result = False
    with pytest.raises(broker.BrokerReadError, match="initialize_failed"):
        reader(api).read()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "1.0", None])
def test_invalid_prices_fail_closed(bad: object) -> None:
    api = FakeMT5()
    api.positions = (position(price_open=bad),)
    with pytest.raises(broker.BrokerReadError):
        reader(api).read()


@pytest.mark.parametrize(
    "updates",
    [
        {"ticket": True},
        {"ticket": -1},
        {"symbol": ""},
        {"symbol": "EUR\nUSD"},
        {"type": 7},
        {"volume": 0},
    ],
)
def test_invalid_position_identity(updates: dict[str, object]) -> None:
    api = FakeMT5()
    api.positions = (position(**updates),)
    with pytest.raises(broker.BrokerReadError):
        reader(api).read()


def test_bounds_and_duplicate_tickets_fail_without_truncation() -> None:
    api = FakeMT5()
    api.positions = (position(), position())
    with pytest.raises(broker.BrokerReadError, match="duplicate_ticket"):
        reader(api).read()
    api.positions = tuple(position(ticket=index) for index in range(broker.MAX_ROWS + 1))
    with pytest.raises(broker.BrokerReadError, match="row_limit_exceeded"):
        reader(api).read()


def test_quote_failures_are_optional_and_symbol_calls_bounded() -> None:
    api = FakeMT5()
    api.positions = tuple(position(ticket=index, symbol=f"SYMBOL{index}") for index in range(20))
    api.quote = None
    snapshot = reader(api).read()
    assert len(snapshot.positions) == 20
    assert snapshot.quote_failures == 16
    assert snapshot.quote_symbols_omitted == 4
    assert len(api.quote_symbols) == 16
    api.positions = (position(),)
    api.quote = SimpleNamespace(bid=float("nan"), ask=1.0, time_msc=0)
    assert reader(api).read().quote_failures == 1
    api.quote = SimpleNamespace(bid=0, ask=0, time_msc=0)
    assert reader(api).read().quote_failures == 1


def test_timeout_poisons_without_overlapping_retry_or_shutdown() -> None:
    api = FakeMT5()
    monitor = broker.BrokerMonitor(reader(api), timeout_seconds=0.02, clock=lambda: 100.0)
    assert monitor.poll_once()
    release = threading.Event()
    api.block = release
    try:
        assert not monitor.poll_once()
        assert monitor.status()["ipc_poisoned"]
        assert monitor.status()["snapshot_stale"]
        assert len(monitor.status()["snapshot"]["positions"]) == 1
        assert not monitor.poll_once()
        monitor.stop()
        assert api.reads == 2
        assert api.shutdowns == 0
    finally:
        release.set()


def test_concurrent_polls_do_not_overlap() -> None:
    api = FakeMT5()
    api.block = threading.Event()
    monitor = broker.BrokerMonitor(reader(api), timeout_seconds=0.2)
    running = threading.Thread(target=monitor.poll_once)
    running.start()
    try:
        assert monitor.poll_lock.locked() or running.is_alive()
        if monitor.poll_lock.locked():
            assert not monitor.poll_once()
    finally:
        api.block.set()
        running.join(1)
    assert api.reads == 1


def test_reader_exception_text_never_leaks() -> None:
    api = FakeMT5()

    def fail() -> object:
        raise RuntimeError("PRIVATE ACCOUNT CREDENTIAL")

    api.account_info = fail  # type: ignore[method-assign]
    monitor = broker.BrokerMonitor(reader(api))
    assert not monitor.poll_once()
    assert monitor.status()["last_error"] == "read_failed_RuntimeError"
    assert "PRIVATE" not in json.dumps(monitor.status())


def test_http_is_read_only_and_never_polls_on_scrape() -> None:
    api = FakeMT5()
    monitor = broker.BrokerMonitor(reader(api), clock=lambda: 100.0)
    assert monitor.poll_once()
    server = ThreadingHTTPServer(("127.0.0.1", 0), broker.make_handler(monitor))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
    try:
        for path in ("/health", "/metrics", "/api/status"):
            connection.request("GET", path)
            response = connection.getresponse()
            assert response.status == 200
            body = response.read()
            assert b"PRIVATE" not in body
        connection.request("POST", "/api/status", body=b"{}")
        response = connection.getresponse()
        assert response.status == 501
        response.read()
        connection.request("GET", "/close-position")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
        assert api.reads == 1
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_start_stop_and_poll_interval_guardrails() -> None:
    api = FakeMT5()
    monitor = broker.BrokerMonitor(reader(api))
    assert monitor.poll_once()
    monitor.start()
    with pytest.raises(RuntimeError, match="already started"):
        monitor.start()
    monitor.stop()
    assert monitor.thread is not None and not monitor.thread.is_alive()
    assert api.shutdowns == 1
    for interval in (0, 1, 4.99, float("nan")):
        with pytest.raises(ValueError):
            broker.BrokerMonitor(reader(api), poll_seconds=interval)
    for timeout in (0, 31, float("inf")):
        with pytest.raises(ValueError):
            broker.BrokerMonitor(reader(api), timeout_seconds=timeout)


def test_linux_optional_loader_and_terminal_observer_are_unknown() -> None:
    if os.name != "nt":
        with pytest.raises(broker.BrokerReadError, match="windows_mt5"):
            broker.load_mt5()
        assert not broker.terminal_is_running(Path("terminal64.exe"))


@pytest.mark.parametrize("changed_field", ["login", "server"])
def test_account_change_latches_and_retains_only_prior_snapshot(changed_field: str) -> None:
    api = FakeMT5()
    monitor = broker.BrokerMonitor(reader(api), clock=lambda: 100.0)
    assert monitor.poll_once()
    previous = monitor.latest
    assert isinstance(api.account, SimpleNamespace)
    setattr(api.account, changed_field, 11223344 if changed_field == "login" else "OTHER SERVER")
    api.account.equity = 987654321.0
    assert not monitor.poll_once()
    status = monitor.status()
    assert monitor.latest is previous
    assert status["snapshot_stale"] and status["account_changed"]
    assert status["last_error"] == "account_changed_restart_observer_required"
    calls = (api.reads, api.account_reads)
    assert not monitor.poll_once()
    with pytest.raises(broker.BrokerReadError, match="account_changed"):
        monitor.reader.read()
    assert (api.reads, api.account_reads) == calls
    encoded = json.dumps(status) + broker.render_metrics(status).decode()
    for private in ("99887766", "11223344", "OTHER SERVER", "987654321"):
        assert private not in encoded
    assert "tradebot_broker_account_changed 1.0" in encoded


def test_account_change_during_first_snapshot_rejects_all_rows() -> None:
    api = FakeMT5()
    assert isinstance(api.account, SimpleNamespace)
    changed = SimpleNamespace(**(vars(api.account) | {"login": 11223344}))
    api.account_sequence = [api.account, changed]
    monitor = broker.BrokerMonitor(reader(api), clock=lambda: 100.0)
    assert not monitor.poll_once()
    status = monitor.status()
    assert status["account_changed"] and status["snapshot_stale"]
    assert status["snapshot"] is None
    assert "tradebot_broker_position_count" not in broker.render_metrics(status).decode()
    assert "11223344" not in json.dumps(status)


def test_missing_account_recheck_is_unknown_not_empty_or_changed() -> None:
    api = FakeMT5()
    monitor = broker.BrokerMonitor(reader(api), clock=lambda: 100.0)
    assert monitor.poll_once()
    previous = monitor.latest
    api.account_sequence = [api.account, None]
    assert not monitor.poll_once()
    assert monitor.latest is previous
    assert monitor.status()["snapshot_stale"]
    assert not monitor.status()["account_changed"]
    assert monitor.status()["last_error"] == "account_recheck_unavailable"
    assert monitor.poll_once()


def test_terminal_identity_checked_before_and_after_every_snapshot() -> None:
    api = FakeMT5()
    outcomes = iter((True, True, True, False, False))
    probe = broker.BrokerReader(
        Path("terminal64.exe"), loader=lambda: api, terminal_checker=lambda path: next(outcomes)
    )
    monitor = broker.BrokerMonitor(probe)
    assert monitor.poll_once()
    previous = monitor.latest
    assert not monitor.poll_once()
    assert monitor.latest is previous and monitor.status()["snapshot_stale"]
    reads = api.reads
    assert not monitor.poll_once()
    assert api.reads == reads
    assert api.initializations == 1


@pytest.mark.parametrize("bad_login", [0, -1, True, "99887766", None])
def test_invalid_private_account_identity_fails_closed(bad_login: object) -> None:
    api = FakeMT5()
    assert isinstance(api.account, SimpleNamespace)
    api.account.login = bad_login
    with pytest.raises(broker.BrokerReadError):
        reader(api).read()
    assert api.reads == 0


@pytest.mark.parametrize(
    "headers",
    [
        [],
        ["attacker.invalid:8766"],
        ["localhost", "localhost"],
        ["localhost", "attacker.invalid"],
        ["localhost, attacker.invalid"],
        ["localhost.attacker.invalid"],
        ["localhost."],
        ["localhost:8765"],
        ["localhost:"],
        ["localhost:+8766"],
        ["localhost:08766"],
        ["localhost:8766:8766"],
        ["localhost\nattacker.invalid"],
        ["127.1"],
        ["2130706433"],
        ["[::1]:8766"],
        ["http://localhost:8766"],
        ["attacker.invalid@localhost:8766"],
    ],
)
def test_host_allowlist_rejects_ambiguous_and_external_authorities(headers: list[str]) -> None:
    assert not broker._valid_host_headers(headers, 8766)


@pytest.mark.parametrize(
    "authority", ["localhost", "127.0.0.1", "LOCALHOST:8766", "127.0.0.1:8766", " localhost\t"]
)
def test_host_allowlist_preserves_explicit_loopback(authority: str) -> None:
    assert broker._valid_host_headers([authority], 8766)


def test_http_host_guard_covers_all_routes_and_methods_before_state_access() -> None:
    api = FakeMT5()
    monitor = broker.BrokerMonitor(reader(api), clock=lambda: 100.0)
    assert monitor.poll_once()
    server = ThreadingHTTPServer(("127.0.0.1", 0), broker.make_handler(monitor))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        for path in ("/health", "/metrics", "/api/status", "/unknown"):
            for method in ("GET", "HEAD", "POST"):
                for authorities in ([], ["attacker.invalid"], ["localhost", "localhost"]):
                    connection.putrequest(method, path, skip_host=True)
                    for authority in authorities:
                        connection.putheader("Host", authority)
                    connection.endheaders()
                    response = connection.getresponse()
                    body = response.read()
                    assert response.status == 403
                    assert b"equity" not in body and b"12345" not in body
        for authority in ("localhost", "127.0.0.1", f"localhost:{port}", f"127.0.0.1:{port}"):
            connection.request("GET", "/api/status", headers={"Host": authority})
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["snapshot_available"]
        assert api.reads == 1
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
