"""Capture five fixed FBS-Demo carrier-label intervals without trading actions.

The source responses are evidence, not corrected timestamps or Gate approval.  Every
request is fetched twice and each native and canonical response is retained separately.
The terminal must already be running at the exact reviewed path.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tradebot.data.acquisition_probe import (  # noqa: E402
    CANONICAL_TICK_HEADER,
    SourceTick,
    encode_source_tick,
)
from tradebot.data.source_supplement import (  # noqa: E402
    BROKER_SYMBOL,
    FETCHES_PER_REQUEST,
    INSTRUMENT,
    MANIFEST_SCHEMA,
    SOURCE,
    SourceIntervalRequest,
    SourceSupplementError,
    canonical_json_sha256,
    parse_plan,
    request_set_sha256,
)
from tradebot.data.storage import sha256_path  # noqa: E402

TERMINAL = Path("C:/Program Files/MetaTrader 5/terminal64.exe")
PROBE_PATH = ROOT / "scripts" / "fbs_tick_continuity_probe.py"
CALL_SECONDS = 90
RUN_SECONDS = 30 * 60
SUCCESS = 1
TIMEOUT_EXIT_CODE = 75
REQUIRED_TICK_FIELDS = {
    "time",
    "bid",
    "ask",
    "last",
    "volume",
    "time_msc",
    "flags",
    "volume_real",
}
FALSE_GATES = {
    "execution_enabled": False,
    "training_enabled": False,
    "gate_approval": False,
}


class CaptureRefusal(RuntimeError):
    """A privacy-safe reason to stop the supplemental capture."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _epoch_milliseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return ((delta.days * 86_400 + delta.seconds) * 1_000) + delta.microseconds // 1_000


def _write_bytes(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json(path: Path, payload: object) -> None:
    _write_bytes(
        path,
        (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"),
    )


def _manifest_payload(payload: dict[str, object]) -> dict[str, object]:
    unsigned = dict(payload)
    unsigned.pop("integrity", None)
    return {
        **unsigned,
        "integrity": {
            "algorithm": "sha256",
            "payload_sha256": canonical_json_sha256(unsigned),
        },
    }


def _inside_repository(relative: object, field: str) -> Path:
    if not isinstance(relative, str):
        raise CaptureRefusal(f"{field} must be a repository-relative path")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise CaptureRefusal(f"{field} escapes the repository")
    root = ROOT.resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise CaptureRefusal(f"{field} is missing or escapes the repository")
    return resolved


def load_reviewed_plan(
    path: Path, expected_sha256: str
) -> tuple[bytes, dict[str, Any], tuple[SourceIntervalRequest, ...]]:
    if len(expected_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256
    ):
        raise CaptureRefusal("expected plan SHA-256 must be lowercase hexadecimal")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise CaptureRefusal("plan bytes do not match the reviewed SHA-256")
    try:
        payload = json.loads(content)
        requests = parse_plan(payload)
    except (json.JSONDecodeError, SourceSupplementError) as exc:
        raise CaptureRefusal(str(exc)) from exc
    if not isinstance(payload, dict):
        raise CaptureRefusal("supplement plan must be an object")
    return content, cast(dict[str, Any], payload), requests


def check_input_pins(plan: dict[str, Any]) -> bool:
    pins = plan.get("input_sha256")
    if not isinstance(pins, dict) or not pins:
        return False
    try:
        return all(
            isinstance(expected, str)
            and sha256_path(_inside_repository(name, "input pin")) == expected
            for name, expected in pins.items()
        )
    except (CaptureRefusal, OSError):
        return False


def inputs_unchanged(plan: dict[str, Any], plan_path: Path, plan_bytes: bytes) -> bool:
    try:
        return check_input_pins(plan) and plan_path.read_bytes() == plan_bytes
    except OSError:
        return False


def require_running_terminal(path: Path) -> None:
    if os.name != "nt" or path.resolve() != TERMINAL.resolve() or not path.is_file():
        raise CaptureRefusal("the exact installed Windows terminal is required")
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'terminal64.exe'\" | "
        "Select-Object -ExpandProperty ExecutablePath | ConvertTo-Json -Compress"
    )
    powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(  # noqa: S603 - fixed read-only process inventory
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    values = json.loads(result.stdout or "null")
    candidates = [values] if isinstance(values, str) else (values or [])
    if not any(Path(item).resolve() == path.resolve() for item in candidates if item):
        raise CaptureRefusal("the exact terminal must already be running")


def load_runtime() -> tuple[Any, Any, Any]:
    numpy = importlib.import_module("numpy")
    mt5 = importlib.import_module("MetaTrader5")
    spec = importlib.util.spec_from_file_location("clock_supplement_bounded_probe", PROBE_PATH)
    if spec is None or spec.loader is None:
        raise CaptureRefusal("cannot load the bounded MT5 probe")
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    return mt5, probe, numpy


def _response_stem(request: SourceIntervalRequest, fetch: int) -> str:
    return f"{request.index_in_window + 1:02d}-{request.session_date.isoformat()}-fetch-{fetch}"


def preserve_response(
    raw: Any,
    *,
    output: Path,
    request: SourceIntervalRequest,
    fetch: int,
    numpy: Any,
) -> dict[str, object]:
    """Preserve the full native response before validating/canonicalizing it."""

    stem = _response_stem(request, fetch)
    native_path = output / f"{stem}.native.npy"
    with native_path.open("xb") as stream:
        numpy.save(stream, raw, allow_pickle=False)
        stream.flush()
        os.fsync(stream.fileno())
    record: dict[str, object] = {
        "fetch": fetch,
        "chunk_id": request.chunk_id,
        "status": "NATIVE_PRESERVED",
        "native_path": native_path.name,
        "native_bytes": native_path.stat().st_size,
        "native_sha256": sha256_path(native_path),
        "returned_rows": int(raw.size),
        "dtype": raw.dtype.descr,
        "shape": list(raw.shape),
        "preserved_at_utc": utc_now(),
    }
    receipt = output / f"{stem}.received.json"
    _write_json(receipt, record)

    loaded = numpy.load(native_path, allow_pickle=False)
    native_equal = (
        loaded.dtype == raw.dtype
        and loaded.shape == raw.shape
        and loaded.tobytes(order="C") == raw.tobytes(order="C")
    )
    record["native_roundtrip_equal"] = native_equal
    if not native_equal:
        raise CaptureRefusal("native response did not survive an exact NumPy roundtrip")
    if raw.ndim != 1 or set(raw.dtype.names or ()) != REQUIRED_TICK_FIELDS:
        raise CaptureRefusal("native response differs from the exact eight-field tick schema")
    if any(
        raw.dtype.fields[field][0].kind not in "iu"
        for field in ("time", "time_msc", "volume", "flags")
    ):
        raise CaptureRefusal("native integer tick fields have unexpected types")

    start_msc = _epoch_milliseconds(request.start)
    end_msc = _epoch_milliseconds(request.end)
    stamps = [int(row["time_msc"]) for row in raw]
    record.update(
        {
            "rows_before_start": sum(stamp < start_msc for stamp in stamps),
            "rows_exactly_at_end": sum(stamp == end_msc for stamp in stamps),
            "rows_after_end": sum(stamp > end_msc for stamp in stamps),
            "half_open_rows": sum(start_msc <= stamp < end_msc for stamp in stamps),
            "timestamp_regressions": sum(right < left for left, right in pairwise(stamps)),
            "time_field_mismatches": sum(
                int(row["time"]) != int(row["time_msc"]) // 1_000 for row in raw
            ),
            "minimum_time_msc": min(stamps, default=None),
            "maximum_time_msc": max(stamps, default=None),
            "returned_order_preserved": True,
            "timezone_adjustment_applied": False,
        }
    )

    canonical_path = output / f"{stem}.source-ticks.tsv.gz"
    semantic = hashlib.sha256(CANONICAL_TICK_HEADER)
    with canonical_path.open("xb") as stream:
        with gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed:
            compressed.write(CANONICAL_TICK_HEADER)
            for row in raw:
                tick = SourceTick(
                    time=int(row["time"]),
                    time_msc=int(row["time_msc"]),
                    bid=Decimal(str(row["bid"])),
                    ask=Decimal(str(row["ask"])),
                    last=Decimal(str(row["last"])),
                    volume=int(row["volume"]),
                    flags=int(row["flags"]),
                    volume_real=Decimal(str(row["volume_real"])),
                )
                line = encode_source_tick(tick)
                semantic.update(line)
                compressed.write(line)
        stream.flush()
        os.fsync(stream.fileno())
    record.update(
        {
            "canonical_path": canonical_path.name,
            "canonical_bytes": canonical_path.stat().st_size,
            "canonical_sha256": sha256_path(canonical_path),
            "semantic_sha256": semantic.hexdigest(),
        }
    )
    structural = (
        record["rows_before_start"] == 0
        and record["rows_exactly_at_end"] == 0
        and record["rows_after_end"] == 0
        and record["half_open_rows"] == record["returned_rows"]
        and record["timestamp_regressions"] == 0
        and record["time_field_mismatches"] == 0
    )
    if not structural:
        raise CaptureRefusal("preserved response failed half-open or timestamp structure checks")
    record["status"] = "VERIFIED"
    _write_json(output / f"{stem}.verified.json", record)
    return record


def _safe_failure_reason(exc: BaseException) -> str:
    if isinstance(exc, (CaptureRefusal, SourceSupplementError)):
        return str(exc)
    return "unexpected exception text withheld for account privacy"


def _write_failure(
    output: Path,
    *,
    report: dict[str, object],
    exc: BaseException,
    poisoned: bool,
) -> None:
    payload = {
        **report,
        "status": "PARTIAL",
        "completed_at_utc": utc_now(),
        "mt5_session_poisoned": poisoned,
        "failure": {
            "kind": "TIMEOUT" if poisoned else type(exc).__name__,
            "reason": _safe_failure_reason(exc),
            "call": getattr(exc, "label", None) if poisoned else None,
            "waited_seconds": getattr(exc, "waited_seconds", None) if poisoned else None,
        },
    }
    path = output / "partial.json"
    if not path.exists():
        _write_json(path, payload)


def run(plan_path: Path, expected_plan_sha256: str, output: Path, terminal: Path) -> int:
    plan_bytes, plan, requests = load_reviewed_plan(plan_path, expected_plan_sha256)
    build_root = (ROOT / "build").resolve()
    output = output.resolve()
    if not output.is_relative_to(build_root) or output == build_root:
        raise CaptureRefusal("output must be a new directory below repository build")
    output.mkdir(parents=True, exist_ok=False)
    _write_bytes(output / "plan.json", plan_bytes)
    started = time.monotonic()
    run_id = f"clock-supplement-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    report: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "status": "RUNNING",
        **FALSE_GATES,
        "purpose": "Additive immutable raw coverage; no timestamp rewrite or approval.",
        "run_id": run_id,
        "started_at_utc": utc_now(),
        "source": SOURCE,
        "instrument": INSTRUMENT,
        "broker_symbol": BROKER_SYMBOL,
        "fetches_per_request": FETCHES_PER_REQUEST,
        "planned_requests": len(requests),
        "planned_fetches": len(requests) * FETCHES_PER_REQUEST,
        "request_set_sha256": request_set_sha256(requests),
        "reviewed_plan_sha256": expected_plan_sha256,
        "input_sha256": plan["input_sha256"],
        "input_pins_before": False,
        "input_pins_after": False,
        "account_identifiers_persisted": False,
        "mt5_session_poisoned": False,
        "call_timeout_seconds": CALL_SECONDS,
        "run_timeout_seconds": RUN_SECONDS,
        "requests": [
            {"request": request.to_dict(), "responses": [], "comparison": None}
            for request in requests
        ],
        "calls": [],
    }
    mt5: Any = None
    probe: Any = None
    numpy: Any = None
    attached = False
    expected_identity: tuple[object, ...] | None = None
    terminal_build: int | None = None
    before_counts: tuple[int, int] | None = None
    after_counts: tuple[int, int] | None = None

    def call(label: str, function: Any) -> Any:
        metadata: dict[str, object] = {"call": label, "started_at_utc": utc_now()}
        cast(list[dict[str, object]], report["calls"]).append(metadata)
        result = probe._bounded(label, function)
        metadata.update(
            {
                "returned_at_utc": utc_now(),
                "elapsed_seconds": result.elapsed_seconds,
                "mt5_error_code": result.error[0],
                "native_error_text": "WITHHELD_ACCOUNT_PRIVACY",
            }
        )
        return result

    def checked(label: str, function: Any) -> Any:
        result = call(label, function)
        if result.value is None or result.error[0] != SUCCESS:
            raise CaptureRefusal(f"required read-only MT5 call failed: {label}")
        return result.value

    def account_state() -> tuple[tuple[object, ...], tuple[int, int]]:
        nonlocal terminal_build
        info = checked("terminal-info", mt5.terminal_info)
        account = checked("account-info", mt5.account_info)
        positions = checked("positions-get", mt5.positions_get)
        orders = checked("orders-get", mt5.orders_get)
        if not info.connected or Path(info.path).resolve() != terminal.parent.resolve():
            raise CaptureRefusal("terminal connection or path changed")
        if (
            account.server != SOURCE
            or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO
            or account.currency != "USD"
            or Decimal(str(account.balance)) != Decimal("1000")
            or Decimal(str(account.equity)) != Decimal("1000")
            or Decimal(str(account.margin)) != Decimal("0")
        ):
            raise CaptureRefusal(
                "account must remain exact FBS-Demo, demo, USD, USD 1000 balance/equity "
                "and zero margin"
            )
        counts = (len(positions), len(orders))
        if counts != (0, 0):
            raise CaptureRefusal("demo account must have no open positions or pending orders")
        current_build = int(info.build)
        if terminal_build is None:
            terminal_build = current_build
        elif current_build != terminal_build:
            raise CaptureRefusal("terminal build changed during capture")
        identity = (
            int(account.login),
            str(account.server),
            int(account.trade_mode),
            str(account.currency),
            Decimal(str(account.balance)),
            Decimal(str(account.equity)),
            Decimal(str(account.margin)),
        )
        return identity, counts

    def validate_state() -> tuple[int, int]:
        nonlocal expected_identity
        identity, counts = account_state()
        if expected_identity is None:
            expected_identity = identity
        elif identity != expected_identity:
            raise CaptureRefusal("account identity changed during capture")
        return counts

    try:
        report["input_pins_before"] = check_input_pins(plan)
        if report["input_pins_before"] is not True:
            raise CaptureRefusal("reviewed input hashes do not match")
        require_running_terminal(terminal)
        mt5, probe, numpy = load_runtime()
        probe.CALL_TIMEOUT = timedelta(seconds=CALL_SECONDS)
        probe._run_deadline = started + RUN_SECONDS
        initialized = call(
            "initialize-explicit-terminal",
            lambda: mt5.initialize(path=str(terminal), timeout=CALL_SECONDS * 1_000),
        )
        attached = initialized.value is True
        if not attached or initialized.error[0] != SUCCESS:
            raise CaptureRefusal("explicit terminal attachment failed")
        before_counts = validate_state()
        symbol = checked("symbol-info-EURUSD", lambda: mt5.symbol_info(BROKER_SYMBOL))
        if symbol.name != BROKER_SYMBOL or not symbol.visible:
            raise CaptureRefusal("exact EURUSD must already be present and visible")
        report["runtime"] = {
            "python_version": sys.version.split()[0],
            "numpy_version": str(numpy.__version__),
            "mt5_package_version": str(mt5.__version__),
            "terminal_build": terminal_build,
        }
        request_records = cast(list[dict[str, Any]], report["requests"])
        for fetch in range(1, FETCHES_PER_REQUEST + 1):
            for request, request_record in zip(requests, request_records, strict=True):
                validate_state()
                response = call(
                    f"ticks-{request.session_date.isoformat()}-fetch-{fetch}",
                    partial(
                        mt5.copy_ticks_range,
                        BROKER_SYMBOL,
                        request.start,
                        request.end,
                        mt5.COPY_TICKS_ALL,
                    ),
                )
                try:
                    if response.value is None:
                        raise CaptureRefusal("copy_ticks_range returned no response")
                    record = preserve_response(
                        response.value,
                        output=output,
                        request=request,
                        fetch=fetch,
                        numpy=numpy,
                    )
                    record["elapsed_seconds"] = response.elapsed_seconds
                    record["mt5_error_code"] = response.error[0]
                    if response.error[0] != SUCCESS:
                        raise CaptureRefusal("copy_ticks_range returned a non-success error code")
                    request_record["responses"].append(record)
                finally:
                    validate_state()
        for request_record in request_records:
            responses = cast(list[dict[str, object]], request_record["responses"])
            if len(responses) != FETCHES_PER_REQUEST:
                raise CaptureRefusal("a request does not have both retained responses")
            first, second = responses
            identical = all(
                first[field] == second[field]
                for field in (
                    "returned_rows",
                    "semantic_sha256",
                    "canonical_sha256",
                    "native_sha256",
                )
            )
            request_record["comparison"] = {
                "identical": identical,
                "semantic_sha256": first["semantic_sha256"] if identical else None,
            }
            request_record["completed_at_utc"] = utc_now()
            if not identical:
                raise CaptureRefusal(
                    "repeat responses differ; both are retained but not admissible"
                )
        after_counts = validate_state()
        report["input_pins_after"] = inputs_unchanged(plan, plan_path, plan_bytes)
        if report["input_pins_after"] is not True:
            raise CaptureRefusal("reviewed inputs changed during capture")
        shutdown = call("shutdown-own-python-session", mt5.shutdown)
        attached = False
        if shutdown.value is False or shutdown.error[0] != SUCCESS:
            raise CaptureRefusal("MT5 shutdown reported failure")
        report.update(
            {
                "status": "COMPLETE",
                "completed_at_utc": utc_now(),
                "account_policy": {
                    "server": SOURCE,
                    "is_demo": True,
                    "currency": "USD",
                    "balance_equals_usd_1000": True,
                    "equity_equals_usd_1000": True,
                    "margin_equals_zero": True,
                    "positions_before": before_counts[0],
                    "orders_before": before_counts[1],
                    "positions_after": after_counts[0],
                    "orders_after": after_counts[1],
                },
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        manifest = output / "supplement-manifest.json"
        _write_json(manifest, _manifest_payload(report))
        _write_bytes(
            output / "supplement-manifest.sha256",
            f"{sha256_path(manifest)}  {manifest.name}\n".encode("ascii"),
        )
        return 0
    except BaseException as exc:
        poisoned = probe is not None and isinstance(exc, probe.ProbeTimeout)
        if poisoned:
            # The timed-out daemon may still be inside MT5; any shutdown call could race it.
            try:
                report["input_pins_after"] = inputs_unchanged(plan, plan_path, plan_bytes)
                report["elapsed_seconds"] = time.monotonic() - started
                _write_failure(output, report=report, exc=exc, poisoned=True)
            finally:
                probe._hard_exit(TIMEOUT_EXIT_CODE)
            raise AssertionError("hard exit returned") from exc
        report["input_pins_after"] = inputs_unchanged(plan, plan_path, plan_bytes)
        report["elapsed_seconds"] = time.monotonic() - started
        _write_failure(output, report=report, exc=exc, poisoned=False)
        if attached:
            try:
                call("shutdown-own-python-session-after-error", mt5.shutdown)
            except BaseException as shutdown_exc:
                cleanup_poisoned = isinstance(shutdown_exc, probe.ProbeTimeout)
                report["mt5_session_poisoned"] = cleanup_poisoned
                report["cleanup_failure"] = {
                    "kind": "TIMEOUT" if cleanup_poisoned else type(shutdown_exc).__name__,
                    "reason": _safe_failure_reason(shutdown_exc),
                }
                if cleanup_poisoned:
                    # The cleanup call itself may still be in MT5.  Make no later MT5 call.
                    try:
                        _write_json(
                            output / "cleanup-failure.json",
                            {
                                "status": "PARTIAL",
                                "mt5_session_poisoned": True,
                                "failure": report["cleanup_failure"],
                                **FALSE_GATES,
                            },
                        )
                    finally:
                        probe._hard_exit(TIMEOUT_EXIT_CODE)
                    raise AssertionError("hard exit returned") from shutdown_exc
                # ``partial.json`` is immutable; retain the cleanup outcome separately.
                _write_json(
                    output / "cleanup-failure.json",
                    {
                        "status": "PARTIAL",
                        "mt5_session_poisoned": False,
                        "failure": report["cleanup_failure"],
                        **FALSE_GATES,
                    },
                )
        return 1
    finally:
        if probe is not None:
            probe._run_deadline = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--terminal", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run(
        args.plan.resolve(),
        args.expected_plan_sha256,
        args.output,
        args.terminal.resolve(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
