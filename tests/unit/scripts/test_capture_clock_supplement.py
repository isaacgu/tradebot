"""Offline control-flow tests for the fixed clock-supplement capture helper."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar

import pytest

from tradebot.data.source_supplement import (
    BROKER_SYMBOL,
    FETCHES_PER_REQUEST,
    INSTRUMENT,
    PLAN_SCHEMA,
    SOURCE,
    fixed_clock_supplement_requests,
    load_supplement_artifacts,
    request_set_sha256,
)
from tradebot.data.storage import sha256_path

FIELDS = (
    "time",
    "bid",
    "ask",
    "last",
    "volume",
    "time_msc",
    "flags",
    "volume_real",
)
ACCOUNT_LOGIN = 987_654_321


class _ScalarType:
    def __init__(self, kind: str) -> None:
        self.kind = kind


class _Dtype:
    names = FIELDS
    descr: ClassVar[list[tuple[str, str]]] = [
        ("time", "<i8"),
        ("bid", "<f8"),
        ("ask", "<f8"),
        ("last", "<f8"),
        ("volume", "<u8"),
        ("time_msc", "<i8"),
        ("flags", "<u4"),
        ("volume_real", "<f8"),
    ]
    fields: ClassVar[dict[str, tuple[_ScalarType, int]]] = {
        field: (_ScalarType("i" if field in {"time", "time_msc"} else "u"), 0)
        for field in ("time", "time_msc", "volume", "flags")
    }

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Dtype)


class _Array:
    dtype = _Dtype()
    ndim = 1

    def __init__(self, rows: list[dict[str, int | float]]) -> None:
        self.rows = rows
        self.shape = (len(rows),)
        self.size = len(rows)

    def __iter__(self) -> Any:
        return iter(self.rows)

    def tobytes(self, *, order: str) -> bytes:
        assert order == "C"
        return json.dumps(self.rows, sort_keys=True, separators=(",", ":")).encode()


class _FakeNumpy:
    __version__ = "synthetic-no-dependency"

    @staticmethod
    def save(stream: Any, raw: _Array, *, allow_pickle: bool) -> None:
        assert allow_pickle is False
        stream.write(raw.tobytes(order="C"))

    @staticmethod
    def load(path: Path, *, allow_pickle: bool) -> _Array:
        assert allow_pickle is False
        return _Array(json.loads(path.read_text(encoding="utf-8")))


class _HardExit(RuntimeError):
    pass


class _ProbeTimeout(RuntimeError):
    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.label = label
        self.waited_seconds = 90.0


class _FakeProbe:
    ProbeTimeout = _ProbeTimeout
    CALL_TIMEOUT: object = None
    _run_deadline: float | None = None

    def __init__(self, timeout_labels: set[str] | None = None) -> None:
        self.timeout_labels = timeout_labels or set()
        self.labels: list[str] = []
        self.hard_exits: list[int] = []

    def _bounded(self, label: str, function: Any) -> SimpleNamespace:
        self.labels.append(label)
        if label in self.timeout_labels or ("ticks-" in label and "ticks" in self.timeout_labels):
            raise _ProbeTimeout(label)
        return SimpleNamespace(value=function(), error=(1, "success"), elapsed_seconds=0.01)

    def _hard_exit(self, code: int) -> None:
        self.hard_exits.append(code)
        raise _HardExit(str(code))


class _FakeMt5:
    __version__ = "synthetic"
    ACCOUNT_TRADE_MODE_DEMO = 0
    COPY_TICKS_ALL = 3

    def __init__(self, terminal_dir: Path, *, balance: str = "1000") -> None:
        self.terminal_dir = terminal_dir
        self.balance = balance
        self.calls: list[str] = []

    def initialize(self, *, path: str, timeout: int) -> bool:
        self.calls.append(f"initialize:{path}:{timeout}")
        return True

    def terminal_info(self) -> SimpleNamespace:
        self.calls.append("terminal_info")
        return SimpleNamespace(connected=True, path=str(self.terminal_dir), build=6140)

    def account_info(self) -> SimpleNamespace:
        self.calls.append("account_info")
        return SimpleNamespace(
            login=ACCOUNT_LOGIN,
            server=SOURCE,
            trade_mode=self.ACCOUNT_TRADE_MODE_DEMO,
            currency="USD",
            balance=self.balance,
            equity="1000",
            margin="0",
        )

    def positions_get(self) -> tuple[()]:
        self.calls.append("positions_get")
        return ()

    def orders_get(self) -> tuple[()]:
        self.calls.append("orders_get")
        return ()

    def symbol_info(self, symbol: str) -> SimpleNamespace:
        self.calls.append(f"symbol_info:{symbol}")
        return SimpleNamespace(name=symbol, visible=True)

    def copy_ticks_range(self, symbol: str, start: datetime, end: datetime, mode: int) -> _Array:
        self.calls.append(f"copy_ticks_range:{symbol}:{start.isoformat()}:{end.isoformat()}:{mode}")
        first = int(start.timestamp() * 1_000) + 1_000
        rows = [
            {
                "time": first // 1_000,
                "bid": 1.10001,
                "ask": 1.10011,
                "last": 0.0,
                "volume": 0,
                "time_msc": first,
                "flags": 6,
                "volume_real": 0.0,
            },
            {
                "time": (first + 1_000) // 1_000,
                "bid": 1.10002,
                "ask": 1.10012,
                "last": 0.0,
                "volume": 0,
                "time_msc": first + 1_000,
                "flags": 6,
                "volume_real": 0.0,
            },
        ]
        return _Array(rows)

    def shutdown(self) -> None:
        self.calls.append("shutdown")


@pytest.fixture
def capture() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "capture_clock_supplement.py"
    spec = importlib.util.spec_from_file_location("capture_clock_supplement_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_plan(root: Path) -> tuple[Path, str]:
    evidence = root / "evidence.json"
    evidence.write_text("{}\n", encoding="utf-8")
    requests = fixed_clock_supplement_requests()
    plan = {
        "schema": PLAN_SCHEMA,
        "source": SOURCE,
        "instrument": INSTRUMENT,
        "broker_symbol": BROKER_SYMBOL,
        "fetches_per_request": FETCHES_PER_REQUEST,
        "execution_enabled": False,
        "training_enabled": False,
        "gate_approval": False,
        "input_sha256": {"evidence.json": sha256_path(evidence)},
        "request_set_sha256": request_set_sha256(requests),
        "requests": [request.to_dict() for request in requests],
    }
    path = root / "plan.json"
    encoded = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(encoded)
    return path, hashlib.sha256(encoded).hexdigest()


def _wire_runtime(
    monkeypatch: pytest.MonkeyPatch,
    capture: ModuleType,
    root: Path,
    *,
    balance: str = "1000",
    timeout_labels: set[str] | None = None,
) -> tuple[_FakeMt5, _FakeProbe, Path]:
    build = root / "build"
    build.mkdir()
    terminal = root / "terminal64.exe"
    terminal.write_bytes(b"synthetic terminal path marker")
    mt5 = _FakeMt5(terminal.parent, balance=balance)
    probe = _FakeProbe(timeout_labels)
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(capture, "TERMINAL", terminal)
    monkeypatch.setattr(capture, "require_running_terminal", lambda path: None)
    monkeypatch.setattr(capture, "load_runtime", lambda: (mt5, probe, _FakeNumpy))
    return mt5, probe, terminal


def test_full_synthetic_capture_retains_two_responses_and_no_account_id(
    capture: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mt5, _, terminal = _wire_runtime(monkeypatch, capture, tmp_path)
    plan, plan_sha = _write_plan(tmp_path)
    output = tmp_path / "build" / "capture"

    assert capture.run(plan, plan_sha, output, terminal) == 0

    manifest = output / "supplement-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["status"] == "COMPLETE"
    assert payload["account_identifiers_persisted"] is False
    assert payload["planned_fetches"] == 10
    assert all(len(item["responses"]) == 2 for item in payload["requests"])
    assert len(list(output.glob("*.native.npy"))) == 10
    assert len(list(output.glob("*.source-ticks.tsv.gz"))) == 10
    assert str(ACCOUNT_LOGIN) not in "".join(
        path.read_text(encoding="utf-8") for path in output.glob("*.json")
    )
    assert len([call for call in mt5.calls if call.startswith("copy_ticks_range:")]) == 10
    assert mt5.calls[-1] == "shutdown"
    artifacts = load_supplement_artifacts(
        manifest,
        artifact_root=output,
        expected_manifest_sha256=sha256_path(manifest),
        ordinal_start=50,
    )
    assert len(artifacts) == 5
    assert sum(artifact.expected_rows for artifact in artifacts) == 10


def test_copy_timeout_persists_poison_and_never_calls_shutdown(
    capture: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mt5, probe, terminal = _wire_runtime(monkeypatch, capture, tmp_path, timeout_labels={"ticks"})
    plan, plan_sha = _write_plan(tmp_path)
    output = tmp_path / "build" / "timeout"

    with pytest.raises(_HardExit):
        capture.run(plan, plan_sha, output, terminal)

    partial = json.loads((output / "partial.json").read_text(encoding="utf-8"))
    assert partial["mt5_session_poisoned"] is True
    assert partial["failure"]["kind"] == "TIMEOUT"
    assert probe.hard_exits == [capture.TIMEOUT_EXIT_CODE]
    assert "shutdown" not in mt5.calls


def test_cleanup_timeout_is_not_swallowed_or_followed_by_an_mt5_call(
    capture: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mt5, probe, terminal = _wire_runtime(
        monkeypatch,
        capture,
        tmp_path,
        balance="999",
        timeout_labels={"shutdown-own-python-session-after-error"},
    )
    plan, plan_sha = _write_plan(tmp_path)
    output = tmp_path / "build" / "cleanup-timeout"

    with pytest.raises(_HardExit):
        capture.run(plan, plan_sha, output, terminal)

    cleanup = json.loads((output / "cleanup-failure.json").read_text(encoding="utf-8"))
    assert cleanup["mt5_session_poisoned"] is True
    assert cleanup["failure"]["kind"] == "TIMEOUT"
    assert probe.hard_exits == [capture.TIMEOUT_EXIT_CODE]
    assert probe.labels[-1] == "shutdown-own-python-session-after-error"
    assert "shutdown" not in mt5.calls


def test_helper_has_no_trade_mutation_api_calls(capture: ModuleType) -> None:
    assert capture.__file__ is not None
    source = Path(capture.__file__).read_text(encoding="utf-8")
    assert "order_send" not in source
    assert "positions_close" not in source
