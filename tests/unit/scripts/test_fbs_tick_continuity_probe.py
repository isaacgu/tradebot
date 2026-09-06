"""Offline end-to-end contracts for the Windows FBS tick-continuity probe.

The real ``MetaTrader5`` package has Windows-only wheels and requires a logged-in
terminal.  These tests install a deliberately small module stub, import the script as
its own program, and assert durable artifacts and native-call behaviour.  The pure
planning and analysis layer has separate tests; this module protects the integration
seams that type checking cannot see: CLI wiring, checkpoints, resume, repeat-fetch
preservation, account refusal and a poisoned native session.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib
import json
import sys
import threading
import types
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

TICK_FIELDS = (
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
STUB_ERROR = (1, "stub: success")


class _Dtype:
    names = TICK_FIELDS


class _Records(list[dict[str, int | float]]):
    """Enough of a NumPy structured array for the script's source conversion."""

    dtype = _Dtype()


class _Info:
    build: int

    def __init__(self, **fields: object) -> None:
        self.__dict__.update(fields)


def _epoch_milliseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return ((delta.days * 86_400 + delta.seconds) * 1000) + delta.microseconds // 1000


def _stub_module(
    *,
    blocked: threading.Event | None = None,
    connected: bool = True,
    broker_is_fbs: bool = True,
    trade_mode: int = 0,
    revise_second_fetch: bool = False,
) -> Any:
    """Return a deterministic MT5 module with inspectable native-call history."""

    module: Any = types.ModuleType("MetaTrader5")
    range_calls: list[tuple[str, datetime, datetime]] = []
    calls_per_range: Counter[tuple[str, datetime, datetime]] = Counter()
    lifecycle_calls: list[str] = []

    def initialize(*, path: str) -> bool:
        lifecycle_calls.append(f"initialize:{path}")
        return True

    def terminal_info() -> _Info:
        lifecycle_calls.append("terminal_info")
        return _Info(
            build=6_140,
            name="MetaTrader 5 (stub)",
            company="FBS Stub Terminal",
            connected=connected,
            trade_allowed=True,
        )

    def account_info() -> _Info:
        lifecycle_calls.append("account_info")
        return _Info(
            login=ACCOUNT_LOGIN,
            trade_mode=trade_mode,
            server="FBS-Demo" if broker_is_fbs else "Other-Demo",
            currency="USD",
            company="FBS Markets Inc." if broker_is_fbs else "Other Broker Ltd",
        )

    def symbol_select(symbol: str, selected: bool) -> bool:
        lifecycle_calls.append(f"symbol_select:{symbol}:{selected}")
        return True

    def copy_ticks_range(
        symbol: str,
        start: datetime,
        end: datetime,
        flags: int,
    ) -> _Records:
        assert flags == module.COPY_TICKS_ALL
        range_key = (symbol, start, end)
        range_calls.append(range_key)
        calls_per_range[range_key] += 1
        if blocked is not None:
            blocked.wait()

        start_msc = _epoch_milliseconds(start)
        revised = revise_second_fetch and calls_per_range[range_key] == 2
        records = _Records()
        for index in range(2):
            stamp = start_msc + (index + 1) * 1_000
            bid = 1.10000 + index * 0.00001
            if revised and index == 1:
                bid += 0.00007
            records.append(
                {
                    "time": stamp // 1_000,
                    "bid": bid,
                    "ask": bid + 0.00010,
                    "last": 0.0,
                    "volume": index + 1,
                    "time_msc": stamp,
                    "flags": module.TICK_FLAG_BID | module.TICK_FLAG_ASK,
                    "volume_real": float(index + 1),
                }
            )
        return records

    def shutdown() -> None:
        lifecycle_calls.append("shutdown")

    module.__version__ = "5.0.5328-stub"
    module.COPY_TICKS_ALL = 3
    module.TICK_FLAG_BID = 2
    module.TICK_FLAG_ASK = 4
    module.ACCOUNT_TRADE_MODE_DEMO = 0
    module.last_error = lambda: STUB_ERROR
    module.initialize = initialize
    module.terminal_info = terminal_info
    module.account_info = account_info
    module.symbol_select = symbol_select
    module.copy_ticks_range = copy_ticks_range
    module.shutdown = shutdown
    module._range_calls = range_calls
    module._calls_per_range = calls_per_range
    module._lifecycle_calls = lifecycle_calls
    return module


def _write_plan(path: Path, *, sessions: int = 1) -> None:
    """Write one exact-symbol plan with one or two Sunday/Monday sessions."""

    if sessions not in (1, 2):
        raise ValueError("test plan supports one or two sessions")
    end = "2024-09-30" if sessions == 1 else "2024-10-01"
    payload = {
        "schema_version": 1,
        "probe_id": "offline-continuity-test",
        "source": "FBS-Demo",
        "symbols": {"EURUSD": "EURUSD"},
        "repeat_fetches": 2,
        "chunk_sessions": 1,
        "purpose": "source_viability_not_gate_evidence",
        "windows": [
            {
                "id": "test_window",
                "purpose": "Offline integration-test window.",
                "start_session_date": "2024-09-29",
                "end_session_date_exclusive": end,
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _import_probe(patch: pytest.MonkeyPatch, module: Any) -> Any:
    patch.setitem(sys.modules, "MetaTrader5", module)
    patch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)
    return importlib.import_module("fbs_tick_continuity_probe")


def _arguments(
    *,
    terminal: Path,
    plan: Path,
    work_dir: Path,
    output: Path,
    max_new_chunks: int | None = None,
) -> list[str]:
    result = [
        "probe",
        "--terminal",
        str(terminal),
        "--plan",
        str(plan),
        "--work-dir",
        str(work_dir),
        "--allow-external-work-dir",
        "--output",
        str(output),
    ]
    if max_new_chunks is not None:
        result.extend(("--max-new-chunks", str(max_new_chunks)))
    return result


def _paths(tmp_path: Path, *, sessions: int = 1) -> tuple[Path, Path, Path, Path]:
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"stub")
    plan = tmp_path / "plan.json"
    _write_plan(plan, sessions=sessions)
    return terminal, plan, tmp_path / "work", tmp_path / "report.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_work_directory_lock_refuses_a_concurrent_writer(tmp_path: Path) -> None:
    module = _stub_module()
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        with probe._work_dir_lock(tmp_path):
            with pytest.raises(SystemExit, match="another continuity probe holds"):
                with probe._work_dir_lock(tmp_path):
                    pytest.fail("the second writer must never acquire the lock")
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)


def test_external_work_directory_requires_an_explicit_opt_in(tmp_path: Path) -> None:
    module = _stub_module()
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        with pytest.raises(ValueError, match=r"must resolve under.*ignored build"):
            probe._validate_work_dir(tmp_path, allow_external=False)
        probe._validate_work_dir(tmp_path, allow_external=True)
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)


def test_source_conversion_rejects_schema_drift_and_out_of_range_rows(
    tmp_path: Path,
) -> None:
    module = _stub_module()
    terminal, plan_path, _work_dir, _output = _paths(tmp_path)
    del terminal
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        request = probe._load_plan(plan_path).chunks[0]
        records = module.copy_ticks_range(
            request.broker_symbol,
            request.start,
            request.end,
            module.COPY_TICKS_ALL,
        )
        records.dtype = types.SimpleNamespace(names=(*TICK_FIELDS, "future_field"))
        with pytest.raises(ValueError, match="unexpected=\\['future_field'\\]"):
            probe._source_ticks(records, request)

        records.dtype = _Dtype()
        records[0]["time_msc"] = _epoch_milliseconds(request.start) - 1
        with pytest.raises(ValueError, match="before_start=1"):
            probe._source_ticks(records, request)

        records[0]["time_msc"] = _epoch_milliseconds(request.end)
        records[1]["time_msc"] = _epoch_milliseconds(request.end)
        retained, shape = probe._source_ticks(records, request)
        assert retained == []
        assert shape["discarded_exactly_at_end"] == 2
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)


def test_complete_run_writes_report_raw_checkpoint_and_hash_sidecar(tmp_path: Path) -> None:
    module = _stub_module()
    terminal, plan, work_dir, output = _paths(tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        probe.main()
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "COMPLETE"
    assert report["retrieval_status"] == "COMPLETE"
    assert report["structural_status"] == "PASSED"
    assert report["repeat_fetch_status"] == "IDENTICAL"
    assert report["quality_status"] == "INDETERMINATE"
    assert report["dataset"]["complete"] is True
    assert report["dataset"]["dataset_sha256"] is not None
    assert report["dataset"]["expected_chunks"] == 1
    assert report["dataset"]["observed_chunks"] == 1
    assert report["dataset"]["total_ticks"] == 2

    chunk = next(iter(report["chunks"].values()))
    raw_path = work_dir / chunk["raw"]["path"]
    checkpoint_path = raw_path.with_suffix(".checkpoint.json")
    assert raw_path.is_file()
    assert checkpoint_path.is_file()
    assert chunk["raw"]["compressed_sha256"] == _sha256(raw_path)
    assert chunk["raw"]["semantic_sha256"] == chunk["evidence"]["semantic_sha256"]
    with gzip.open(raw_path, "rt", encoding="ascii") as stream:
        lines = stream.readlines()
    assert lines[0] == "tradebot.source-ticks.semantic.v1\n"
    assert len(lines) == 3

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["chunk"]["chunk_id"] == chunk["evidence"]["chunk_id"]
    assert len(checkpoint["fetches"]) == 2
    assert checkpoint["repeat_comparisons"] == chunk["repeat_comparisons"]
    assert all(item["identical"] for item in checkpoint["repeat_comparisons"])
    unsigned = {key: value for key, value in checkpoint.items() if key != "integrity"}
    expected_checkpoint_hash = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert checkpoint["integrity"] == {
        "algorithm": "sha256",
        "payload_sha256": expected_checkpoint_hash,
    }
    assert report["dataset"]["active_minutes"] == 1
    assert report["dataset"]["positive_spread_quotes"] == 2
    assert report["windows"]["EURUSD"]["test_window"]["active_minutes"] == 1

    sidecar = output.with_suffix(".json.sha256")
    assert sidecar.read_text(encoding="utf-8") == f"{_sha256(output)}  {output.name}\n"
    assert not output.with_name("report.partial.json").exists()
    assert len(module._range_calls) == 2
    assert module._lifecycle_calls[-1] == "shutdown"


def test_report_and_checkpoints_never_store_the_account_identifier(tmp_path: Path) -> None:
    module = _stub_module()
    terminal, plan, work_dir, output = _paths(tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        probe.main()
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert set(report["account"]) == {"server", "currency", "company", "is_demo"}
    identifier = str(ACCOUNT_LOGIN)
    json_artifacts = [output, *work_dir.rglob("*.json")]
    assert json_artifacts
    assert all(identifier not in path.read_text(encoding="utf-8") for path in json_artifacts)


def test_planned_stop_resumes_without_refetching_a_checkpointed_chunk(tmp_path: Path) -> None:
    module = _stub_module()
    terminal, plan, work_dir, output = _paths(tmp_path, sessions=2)
    output.write_text('{"canonical": true}\n', encoding="utf-8")
    partial = output.with_name("report.partial.json")

    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(
            sys,
            "argv",
            _arguments(
                terminal=terminal,
                plan=plan,
                work_dir=work_dir,
                output=output,
                max_new_chunks=1,
            ),
        )
        probe.main()

        first_range = module._range_calls[0]
        assert module._calls_per_range[first_range] == 2
        assert json.loads(output.read_text(encoding="utf-8")) == {"canonical": True}
        stopped = json.loads(partial.read_text(encoding="utf-8"))
        assert stopped["status"] == "PARTIAL"
        assert stopped["retrieval_status"] == "PARTIAL"
        assert stopped["failure"]["kind"] == "PLANNED_STOP"
        assert stopped["failure"]["remaining_chunks"] == 1
        assert stopped["dataset"]["complete"] is False
        assert stopped["resume"]["new_chunks"] == 1

        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        probe.main()
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "COMPLETE"
    assert report["dataset"]["complete"] is True
    assert report["resume"] == {
        "checkpoint_chunks_reused": 1,
        "new_chunks": 1,
        "resume_exercised": True,
    }
    assert len(report["chunks"]) == 2
    assert not partial.exists()
    assert module._calls_per_range[first_range] == 2
    assert sorted(module._calls_per_range.values()) == [2, 2]
    assert len(module._range_calls) == 4


def test_resume_recomputes_metrics_instead_of_trusting_a_rehashed_checkpoint(
    tmp_path: Path,
) -> None:
    module = _stub_module()
    terminal, plan, work_dir, output = _paths(tmp_path, sessions=2)
    partial = output.with_name("report.partial.json")

    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(
            sys,
            "argv",
            _arguments(
                terminal=terminal,
                plan=plan,
                work_dir=work_dir,
                output=output,
                max_new_chunks=1,
            ),
        )
        probe.main()
        checkpoint_path = next(work_dir.rglob("*.checkpoint.json"))
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint["chunk"]["metrics"]["active_minutes"] = 999
        unsigned = {key: value for key, value in checkpoint.items() if key != "integrity"}
        checkpoint["integrity"]["payload_sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        with pytest.raises(ValueError, match="checkpoint primary metrics mismatch"):
            probe.main()
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    assert len(module._range_calls) == 2
    failure = json.loads(partial.read_text(encoding="utf-8"))["failure"]
    assert failure["kind"] == "ERROR"
    assert "primary metrics mismatch" in failure["error"]


def test_resume_refuses_to_mix_a_different_terminal_build(tmp_path: Path) -> None:
    module = _stub_module()
    terminal, plan, work_dir, output = _paths(tmp_path, sessions=2)
    arguments = _arguments(
        terminal=terminal,
        plan=plan,
        work_dir=work_dir,
        output=output,
        max_new_chunks=1,
    )
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(sys, "argv", arguments)
        probe.main()

        original_terminal_info = module.terminal_info

        def changed_terminal_info() -> _Info:
            info = cast(_Info, original_terminal_info())
            info.build += 1
            return info

        module.terminal_info = changed_terminal_info
        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        with pytest.raises(ValueError, match="checkpoint environment mismatch"):
            probe.main()
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    assert len(module._range_calls) == 2


def test_repeat_fetch_revision_is_reported_and_both_responses_are_preserved(
    tmp_path: Path,
) -> None:
    module = _stub_module(revise_second_fetch=True)
    terminal, plan, work_dir, output = _paths(tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        probe.main()
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "COMPLETE"
    assert report["repeat_fetch_status"] == "REVISION_OBSERVED"
    assert report["repeat_fetch_mismatches"] == 1
    chunk = next(iter(report["chunks"].values()))
    comparison = chunk["repeat_comparisons"][0]
    assert comparison["identical"] is False
    assert comparison["first_difference_index"] == 1
    assert comparison["first_sha256"] != comparison["second_sha256"]

    first_raw = work_dir / chunk["raw"]["path"]
    repeated = chunk["fetches"][1]["preserved_raw"]
    repeated_raw = work_dir / repeated["path"]
    assert first_raw.is_file() and repeated_raw.is_file()
    assert repeated["semantic_sha256"] == comparison["second_sha256"]
    assert gzip.decompress(first_raw.read_bytes()) != gzip.decompress(repeated_raw.read_bytes())
    assert report["windows"]["EURUSD"]["test_window"]["repeat_fetch_mismatches"] == 1


def test_resume_refuses_a_missing_preserved_repeat_response(tmp_path: Path) -> None:
    module = _stub_module(revise_second_fetch=True)
    terminal, plan, work_dir, output = _paths(tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        arguments = _arguments(
            terminal=terminal,
            plan=plan,
            work_dir=work_dir,
            output=output,
        )
        patch.setattr(sys, "argv", arguments)
        probe.main()
        report = json.loads(output.read_text(encoding="utf-8"))
        chunk = next(iter(report["chunks"].values()))
        repeat_path = work_dir / chunk["fetches"][1]["preserved_raw"]["path"]
        repeat_path.unlink()

        patch.setattr(sys, "argv", arguments)
        with pytest.raises(ValueError, match=r"repeat 2 raw.*missing"):
            probe.main()
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    assert len(module._range_calls) == 2


def test_structural_status_checks_a_changed_repeat_response(tmp_path: Path) -> None:
    module = _stub_module()
    ordinary_fetch = module.copy_ticks_range

    def cross_on_second_fetch(*args: object, **kwargs: object) -> _Records:
        records = cast(_Records, ordinary_fetch(*args, **kwargs))
        range_key = module._range_calls[-1]
        if module._calls_per_range[range_key] == 2:
            records[-1]["ask"] = float(records[-1]["bid"]) - 0.00001
        return records

    module.copy_ticks_range = cross_on_second_fetch
    terminal, plan, work_dir, output = _paths(tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        probe.main()
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["repeat_fetch_status"] == "REVISION_OBSERVED"
    assert report["structural_status"] == "FAILED"
    assert report["structural_failures"]["crossed_quotes"] == 1


def test_non_none_tick_array_with_mt5_error_is_not_checkpointed(tmp_path: Path) -> None:
    module = _stub_module()
    module.last_error = lambda: (-10005, "internal timeout")
    terminal, plan, work_dir, output = _paths(tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        with pytest.raises(SystemExit, match=r"copy_ticks_range failed.*internal timeout"):
            probe.main()
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    assert len(module._range_calls) == 1
    assert not list(work_dir.rglob("*.checkpoint.json"))
    partial = json.loads(output.with_name("report.partial.json").read_text(encoding="utf-8"))
    assert partial["failure"]["kind"] == "ERROR"


class _HardExitCalled(RuntimeError):
    def __init__(self, code: int) -> None:
        self.code = code


def test_timeout_preserves_canonical_and_never_calls_shutdown(tmp_path: Path) -> None:
    blocked = threading.Event()
    module = _stub_module(blocked=blocked)
    terminal, plan, work_dir, output = _paths(tmp_path)
    output.write_text('{"canonical": true}\n', encoding="utf-8")
    partial = output.with_name("report.partial.json")

    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(probe, "CALL_TIMEOUT", timedelta(milliseconds=25))
        patch.setattr(
            probe,
            "_hard_exit",
            lambda code: (_ for _ in ()).throw(_HardExitCalled(code)),
        )
        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        try:
            with pytest.raises(_HardExitCalled) as stopped:
                probe.main()
        finally:
            blocked.set()
            patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    assert stopped.value.code == 75
    assert json.loads(output.read_text(encoding="utf-8")) == {"canonical": True}
    payload = json.loads(partial.read_text(encoding="utf-8"))
    assert payload["status"] == "PARTIAL"
    assert payload["failure"]["kind"] == "TIMEOUT"
    assert payload["failure"]["mt5_session_poisoned"] is True
    assert "probe stopped waiting" in payload["failure"]["error"]
    assert str(STUB_ERROR[0]) not in payload["failure"]["error"]
    assert "shutdown" not in module._lifecycle_calls
    assert len(module._range_calls) == 1


def test_timeout_after_a_completed_chunk_records_resumable_progress(tmp_path: Path) -> None:
    module = _stub_module()
    terminal, plan, work_dir, output = _paths(tmp_path, sessions=2)
    blocked = threading.Event()
    ordinary_fetch = module.copy_ticks_range

    def block_after_first_chunk(*args: object, **kwargs: object) -> _Records:
        if len(module._range_calls) >= 2:
            blocked.wait()
        return cast(_Records, ordinary_fetch(*args, **kwargs))

    module.copy_ticks_range = block_after_first_chunk
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(probe, "CALL_TIMEOUT", timedelta(milliseconds=25))
        patch.setattr(
            probe,
            "_hard_exit",
            lambda code: (_ for _ in ()).throw(_HardExitCalled(code)),
        )
        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        try:
            with pytest.raises(_HardExitCalled):
                probe.main()
        finally:
            blocked.set()
            patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    payload = json.loads(output.with_name("report.partial.json").read_text(encoding="utf-8"))
    assert payload["failure"]["kind"] == "TIMEOUT"
    assert payload["dataset"]["observed_chunks"] == 1
    assert payload["dataset"]["missing_chunk_ids"]
    assert payload["resume"]["new_chunks"] == 1
    assert len(payload["chunks"]) == 1
    assert "shutdown" not in module._lifecycle_calls


@pytest.mark.parametrize(
    ("stub_options", "message"),
    [
        ({"connected": False}, "terminal is disconnected"),
        ({"trade_mode": 1}, "attached account is not a demo"),
        ({"broker_is_fbs": False}, "does not identify as FBS"),
    ],
)
def test_wrong_account_or_connection_is_refused_before_fetching(
    tmp_path: Path,
    stub_options: dict[str, Any],
    message: str,
) -> None:
    module = _stub_module(**stub_options)
    terminal, plan, work_dir, output = _paths(tmp_path)
    partial = output.with_name("report.partial.json")
    with pytest.MonkeyPatch.context() as patch:
        probe = _import_probe(patch, module)
        patch.setattr(
            sys,
            "argv",
            _arguments(terminal=terminal, plan=plan, work_dir=work_dir, output=output),
        )
        with pytest.raises(SystemExit, match=message):
            probe.main()
        patch.delitem(sys.modules, "fbs_tick_continuity_probe", raising=False)

    assert not output.exists()
    assert not list(work_dir.rglob("*.json"))
    assert not list(work_dir.rglob("*.gz"))
    assert not module._range_calls
    assert module._lifecycle_calls[-1] == "shutdown"
    # Identity failures happen before the account is accepted, so even a partial
    # artifact would risk authenticating untrusted account metadata as probe evidence.
    assert not partial.exists()
