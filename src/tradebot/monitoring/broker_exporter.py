"""Local read-only MT5 account/position observation, never a trading gateway.

One serialized poll lane observes all positions, including manual/external trades.
Only whitelisted fields leave IPC; account IDs, names, comments and credentials do
not. A timeout poisons this observer's IPC session: HTTP keeps the stale last known
snapshot, but no further broker call or shutdown is attempted until process restart.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib
import json
import math
import os
import threading
import time
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol, cast

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

MAX_ROWS = 1000
MAX_QUOTE_SYMBOLS = 16
MIN_POLL_SECONDS = 5.0
PREFIX = "tradebot_broker_"
type PublicRecord = dict[str, str | int | float]


class MT5ReaderAPI(Protocol):
    """Only the read/own-IPC subset exists at this boundary; no mutation methods."""

    def initialize(self, *, path: str, timeout: int) -> bool: ...
    def terminal_info(self) -> object | None: ...
    def account_info(self) -> object | None: ...
    def positions_get(self) -> Sequence[object] | None: ...
    def orders_get(self) -> Sequence[object] | None: ...
    def symbol_info_tick(self, symbol: str) -> object | None: ...
    def last_error(self) -> tuple[int, str]: ...
    def shutdown(self) -> None: ...


class BrokerReadError(RuntimeError):
    """Safe, internally named failure; native error text might contain private data."""


def load_mt5() -> MT5ReaderAPI:
    if os.name != "nt":
        raise BrokerReadError("windows_mt5_runtime_required")
    try:
        return cast(MT5ReaderAPI, importlib.import_module("MetaTrader5"))
    except ImportError as exc:
        raise BrokerReadError("optional_metatrader5_package_missing") from exc


def terminal_is_running(path: Path) -> bool:
    """Require exactly one already-running exact executable, without shell startup.

    Windows Toolhelp and QueryFullProcessImageName use read/query access only.
    Handles are closed; no target process is started, modified or terminated.
    An inaccessible same-named process is ambiguous and fails closed. The check
    is best-effort: process exit between it and MT5 initialize remains a race.
    """
    if os.name != "nt":
        return False

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel_loader = getattr(ctypes, "WinDLL", None)
    if kernel_loader is None:
        return False
    kernel = kernel_loader("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for function in (kernel.Process32FirstW, kernel.Process32NextW):
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        return False
    try:
        matches = 0
        unknown = False
        expected_path = path.resolve()
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            if str(entry.szExeFile).casefold() == path.name.casefold():
                process = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if process:
                    try:
                        buffer = ctypes.create_unicode_buffer(32768)
                        length = wintypes.DWORD(len(buffer))
                        if kernel.QueryFullProcessImageNameW(
                            process, 0, buffer, ctypes.byref(length)
                        ):
                            if Path(buffer.value).resolve() == expected_path:
                                matches += 1
                        else:
                            unknown = True
                    finally:
                        kernel.CloseHandle(process)
                else:
                    unknown = True
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return matches == 1 and not unknown
    finally:
        kernel.CloseHandle(snapshot)


def _number(record: object, field: str) -> float:
    value = getattr(record, field, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrokerReadError("invalid_numeric_" + field)
    number = float(value)
    if not math.isfinite(number):
        raise BrokerReadError("nonfinite_" + field)
    return number


def _integer(record: object, field: str) -> int:
    value = getattr(record, field, None)
    if type(value) is not int or value < 0:
        raise BrokerReadError("invalid_integer_" + field)
    return value


def _text(record: object, field: str) -> str:
    value = getattr(record, field, None)
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= 100
        or any(ord(character) < 32 for character in value)
    ):
        raise BrokerReadError("invalid_text_" + field)
    return value


def _position(record: object) -> PublicRecord:
    result: PublicRecord = {
        "ticket": str(_integer(record, "ticket")),
        "symbol": _text(record, "symbol"),
        "side": {0: "buy", 1: "sell"}.get(_integer(record, "type"), "unknown"),
        "origin": "not_bot_certified",
        "volume_lots": _number(record, "volume"),
        "open_price": _number(record, "price_open"),
        "current_price": _number(record, "price_current"),
        "stop_loss": _number(record, "sl"),
        "take_profit": _number(record, "tp"),
        "profit": _number(record, "profit"),
        "swap": _number(record, "swap"),
        "open_timestamp_seconds": _integer(record, "time_msc") / 1000,
    }
    if result["side"] == "unknown" or float(result["volume_lots"]) <= 0:
        raise BrokerReadError("invalid_position_side_or_volume")
    return result


def _order(record: object) -> PublicRecord:
    order_types = {
        0: "buy",
        1: "sell",
        2: "buy_limit",
        3: "sell_limit",
        4: "buy_stop",
        5: "sell_stop",
        6: "buy_stop_limit",
        7: "sell_stop_limit",
    }
    return {
        "ticket": str(_integer(record, "ticket")),
        "symbol": _text(record, "symbol"),
        "side": order_types.get(_integer(record, "type"), "unknown"),
        "origin": "not_bot_certified",
        "volume_lots": _number(record, "volume_current"),
        "open_price": _number(record, "price_open"),
        "current_price": _number(record, "price_current"),
        "stop_loss": _number(record, "sl"),
        "take_profit": _number(record, "tp"),
        "stop_limit_price": _number(record, "price_stoplimit"),
        "setup_timestamp_seconds": _integer(record, "time_setup_msc") / 1000,
        "expiration_timestamp_seconds": _integer(record, "time_expiration"),
    }


@dataclass(frozen=True)
class BrokerSnapshot:
    started_at_seconds: float
    completed_at_seconds: float
    account: PublicRecord
    positions: tuple[PublicRecord, ...]
    orders: tuple[PublicRecord, ...]
    quotes: tuple[PublicRecord, ...]
    quote_failures: int
    quote_symbols_omitted: int


class BrokerReader:
    def __init__(
        self,
        terminal: Path,
        *,
        loader: Callable[[], MT5ReaderAPI] = load_mt5,
        terminal_checker: Callable[[Path], bool] = terminal_is_running,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.terminal = terminal
        self.loader = loader
        self.terminal_checker = terminal_checker
        self.clock = clock
        self.api: MT5ReaderAPI | None = None
        self.initialized = False
        self._account_identity: tuple[str, int] | None = None
        self.account_changed = False

    def _check_account_identity(self, account: object) -> None:
        # Private in-memory pin only: never serialize the login or exception values.
        identity = (_text(account, "server"), _integer(account, "login"))
        if identity[1] == 0:
            raise BrokerReadError("account_identity_unavailable")
        if self._account_identity is None:
            self._account_identity = identity
        elif identity != self._account_identity:
            self.account_changed = True
            raise BrokerReadError("account_changed_restart_observer_required")

    def read(self) -> BrokerSnapshot:
        if self.account_changed:
            raise BrokerReadError("account_changed_restart_observer_required")
        started = self.clock()
        if not self.terminal_checker(self.terminal):
            raise BrokerReadError("terminal_not_running_ambiguous_or_identity_unknown")
        if self.api is None:
            self.api = self.loader()
        api = self.api
        if not self.initialized:
            if not api.initialize(path=str(self.terminal), timeout=10000):
                raise BrokerReadError("initialize_failed")
            self.initialized = True
        terminal = api.terminal_info()
        if terminal is None or getattr(terminal, "connected", None) is not True:
            raise BrokerReadError("terminal_disconnected")
        account = api.account_info()
        if account is None:
            raise BrokerReadError("account_unavailable")
        self._check_account_identity(account)
        public_account: PublicRecord = {
            "server": _text(account, "server"),
            "currency": _text(account, "currency"),
            "account_kind": {0: "demo", 1: "contest", 2: "live"}.get(
                _integer(account, "trade_mode"), "unknown"
            ),
            "terminal_build": _integer(terminal, "build"),
        }
        for field in ("balance", "equity", "margin", "margin_free", "profit", "margin_level"):
            public_account[field] = _number(account, field)
        positions = api.positions_get()
        if positions is None:
            raise BrokerReadError("positions_unavailable")
        orders = api.orders_get()
        if orders is None:
            raise BrokerReadError("orders_unavailable")
        if len(positions) > MAX_ROWS or len(orders) > MAX_ROWS:
            raise BrokerReadError("row_limit_exceeded_snapshot_not_truncated")
        public_positions = tuple(
            sorted((_position(item) for item in positions), key=lambda x: x["ticket"])
        )
        public_orders = tuple(sorted((_order(item) for item in orders), key=lambda x: x["ticket"]))
        for collection in (public_positions, public_orders):
            if len({row["ticket"] for row in collection}) != len(collection):
                raise BrokerReadError("duplicate_ticket")
        symbols = sorted({str(row["symbol"]) for row in public_positions})
        quotes: list[PublicRecord] = []
        failures = 0
        for symbol in symbols[:MAX_QUOTE_SYMBOLS]:
            try:
                tick = api.symbol_info_tick(symbol)
                if tick is None:
                    raise BrokerReadError("quote_unavailable")
                bid, ask = _number(tick, "bid"), _number(tick, "ask")
                timestamp = _integer(tick, "time_msc")
                if bid <= 0 or ask < bid or timestamp <= 0:
                    raise BrokerReadError("invalid_quote")
                quotes.append(
                    {
                        "symbol": symbol,
                        "bid": bid,
                        "ask": ask,
                        "timestamp_seconds": timestamp / 1000,
                    }
                )
            except BrokerReadError:
                failures += 1
        confirmed_account = api.account_info()
        if confirmed_account is None:
            raise BrokerReadError("account_recheck_unavailable")
        self._check_account_identity(confirmed_account)
        if not self.terminal_checker(self.terminal):
            raise BrokerReadError("terminal_not_running_ambiguous_or_identity_unknown")
        return BrokerSnapshot(
            started,
            self.clock(),
            public_account,
            public_positions,
            public_orders,
            tuple(quotes),
            failures,
            max(0, len(symbols) - MAX_QUOTE_SYMBOLS),
        )

    def close(self) -> None:
        if self.api is not None and self.initialized:
            self.api.shutdown()  # This process's IPC only, never the terminal application.
            self.initialized = False


class BrokerMonitor:
    def __init__(
        self,
        reader: BrokerReader,
        *,
        poll_seconds: float = 5,
        timeout_seconds: float = 10,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not math.isfinite(poll_seconds) or poll_seconds < MIN_POLL_SECONDS:
            raise ValueError("poll_seconds must be at least five")
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be within (0,30]")
        self.reader, self.clock = reader, clock
        self.poll_seconds, self.timeout_seconds = poll_seconds, timeout_seconds
        self.lock = threading.Lock()
        self.poll_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.latest: BrokerSnapshot | None = None
        self.last_attempt = 0.0
        self.last_error: str | None = None
        self.errors = 0
        self.successes = 0
        self.poisoned = False
        self.in_flight = False

    def poll_once(self) -> bool:
        if not self.poll_lock.acquire(blocking=False):
            return False
        try:
            with self.lock:
                if self.poisoned or self.reader.account_changed:
                    return False
                self.last_attempt, self.in_flight = self.clock(), True
            results: list[BrokerSnapshot] = []
            failures: list[str] = []

            def capture() -> None:
                try:
                    results.append(self.reader.read())
                except BrokerReadError as exc:
                    failures.append(str(exc))
                except Exception as exc:
                    # Never serialize native exception text, account objects or credentials.
                    failures.append("read_failed_" + type(exc).__name__)

            worker = threading.Thread(target=capture, daemon=True, name="broker-read-only-ipc")
            worker.start()
            worker.join(self.timeout_seconds)
            with self.lock:
                self.in_flight = worker.is_alive()
                if self.in_flight:
                    self.poisoned = True
                    self.last_error = "ipc_timeout_restart_observer_required"
                elif failures:
                    self.last_error = failures[0]
                elif results:
                    self.latest = results[0]
                    self.last_error = None
                    self.successes += 1
                    return True
                else:
                    self.last_error = "read_ended_without_snapshot"
                self.errors += 1
                return False
        finally:
            self.poll_lock.release()

    def status(self) -> dict[str, Any]:
        with self.lock:
            now = self.clock()
            stale = (
                self.latest is None
                or self.last_error is not None
                or self.reader.account_changed
                or now - self.latest.completed_at_seconds > max(15, self.poll_seconds * 3)
            )
            return {
                "schema_version": 1,
                "observed_at_utc": datetime.fromtimestamp(now, UTC).isoformat(),
                "read_only": True,
                "snapshot_available": self.latest is not None,
                "snapshot_stale": stale,
                "ipc_poisoned": self.poisoned,
                "account_changed": self.reader.account_changed,
                "poll_in_flight": self.in_flight,
                "last_attempt_timestamp_seconds": self.last_attempt,
                "last_success_timestamp_seconds": self.latest.completed_at_seconds
                if self.latest
                else 0,
                "poll_errors_total": self.errors,
                "poll_successes_total": self.successes,
                "last_error": self.last_error,
                "snapshot": asdict(self.latest) if self.latest else None,
                "scope": "All terminal positions/orders, including external; not bot-certified.",
                "limitations": [
                    "A snapshot is a short series of read calls, not an atomic broker transaction.",
                    "Failed reads retain the last successful snapshot; check stale/age indicators.",
                    "Account P&L is broker-reported, not proof of bot strategy performance.",
                    "This observer neither enables execution nor provides trading controls.",
                    "Quotes cover at most 16 open-position symbols; omissions are explicit.",
                    "Account identity is pinned privately; a detected change requires restart.",
                ],
            }

    def start(self) -> None:
        if self.thread is not None:
            raise RuntimeError("poller already started")

        def run() -> None:
            while not self.stop_event.is_set():
                self.poll_once()
                if (
                    self.poisoned
                    or self.reader.account_changed
                    or self.stop_event.wait(self.poll_seconds)
                ):
                    break

        self.thread = threading.Thread(target=run, daemon=True, name="broker-observer-poller")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(self.timeout_seconds + 1)
        # Never call shutdown while a timed-out native worker can still be inside IPC.
        if not self.poisoned and self.poll_lock.acquire(blocking=False):
            try:
                worker = threading.Thread(target=self.reader.close, daemon=True)
                worker.start()
                worker.join(self.timeout_seconds)
            finally:
                self.poll_lock.release()


def render_metrics(status: dict[str, Any]) -> bytes:
    registry = CollectorRegistry()
    for field in (
        "snapshot_available",
        "snapshot_stale",
        "ipc_poisoned",
        "account_changed",
        "poll_in_flight",
        "last_attempt_timestamp_seconds",
        "last_success_timestamp_seconds",
        "poll_errors_total",
        "poll_successes_total",
    ):
        Gauge(PREFIX + field, "Broker observer " + field, registry=registry).set(
            float(status[field])
        )
    snapshot = status["snapshot"]
    if snapshot is None:
        return generate_latest(registry)
    account = snapshot["account"]
    Gauge(
        PREFIX + "account_info",
        "Observed account kind; no account identifiers.",
        ["server", "currency", "account_kind", "terminal_build"],
        registry=registry,
    ).labels(
        *(str(account[field]) for field in ("server", "currency", "account_kind", "terminal_build"))
    ).set(1)
    amount = Gauge(
        PREFIX + "account_amount",
        "Last successful broker account values; fund currency.",
        ["field", "currency"],
        registry=registry,
    )
    for field in ("balance", "equity", "margin", "margin_free", "profit"):
        amount.labels(field, account["currency"]).set(account[field])
    Gauge(
        PREFIX + "margin_level_percent",
        "Broker margin level percent, not money.",
        registry=registry,
    ).set(account["margin_level"])
    for collection in ("positions", "orders"):
        singular = "position" if collection == "positions" else "order"
        Gauge(
            PREFIX + singular + "_count",
            "Last successful count; only observed empty is zero.",
            registry=registry,
        ).set(len(snapshot[collection]))
        fields: tuple[str, ...] = (
            "volume_lots",
            "open_price",
            "current_price",
            "stop_loss",
            "take_profit",
        )
        fields += (
            ("profit", "swap", "open_timestamp_seconds")
            if collection == "positions"
            else ("stop_limit_price", "setup_timestamp_seconds", "expiration_timestamp_seconds")
        )
        for field in fields:
            metric = Gauge(
                PREFIX + singular + "_" + field,
                "Last successful broker " + field,
                ["ticket", "symbol", "side", "origin"],
                registry=registry,
            )
            for row in snapshot[collection]:
                metric.labels(
                    *(str(row[key]) for key in ("ticket", "symbol", "side", "origin"))
                ).set(row[field])
    for field in ("bid", "ask", "timestamp_seconds"):
        quote_metric = Gauge(
            PREFIX + "quote_" + field,
            "Observed open-position symbol quote " + field,
            ["symbol"],
            registry=registry,
        )
        for row in snapshot["quotes"]:
            quote_metric.labels(row["symbol"]).set(row[field])
    for field in ("quote_failures", "quote_symbols_omitted"):
        Gauge(PREFIX + field, "Optional quote observation " + field, registry=registry).set(
            snapshot[field]
        )
    return generate_latest(registry)


def _valid_host_headers(hosts: Sequence[str], port: int) -> bool:
    """Accept a single explicit loopback authority, not DNS aliases or proxy hosts."""
    if len(hosts) != 1:
        return False
    hostname, separator, supplied_port = hosts[0].strip(" \t").partition(":")
    return hostname.lower() in ("localhost", "127.0.0.1") and (
        not separator or supplied_port == str(port)
    )


def make_handler(monitor: BrokerMonitor) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            if not _valid_host_headers(
                self.headers.get_all("Host", []), cast(ThreadingHTTPServer, self.server).server_port
            ):
                self.send_error(403, "Loopback Host required")
                return False
            return True

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/health":
                body, content_type = b'{"status":"ok","scope":"exporter_only"}', "application/json"
            elif path in ("/metrics", "/api/status"):
                state = monitor.status()
                body = (
                    render_metrics(state)
                    if path == "/metrics"
                    else json.dumps(state, allow_nan=False).encode()
                )
                content_type = CONTENT_TYPE_LATEST if path == "/metrics" else "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal", type=Path, required=True)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    args = parser.parse_args(argv)
    monitor = BrokerMonitor(
        BrokerReader(args.terminal),
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(monitor))
    monitor.start()
    print(f"Read-only broker observer at http://{args.host}:{args.port}/metrics", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        monitor.stop()


if __name__ == "__main__":
    main()
