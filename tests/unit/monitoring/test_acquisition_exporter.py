from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tradebot.monitoring import acquisition_exporter
from tradebot.monitoring.acquisition_exporter import (
    DIAGNOSTIC_FIELDS,
    AcquisitionMonitor,
    JsonCache,
    gate1_status,
    make_handler,
    observe_process,
    platform_status,
    read_plan,
    render_metrics,
)


def digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.fixture
def monitor(tmp_path: Path) -> AcquisitionMonitor:
    plan = {
        "schema_version": 1,
        "probe_id": "test",
        "source": "FBS-Demo",
        "symbols": {"GBPUSD": "GBPUSD"},
        "repeat_fetches": 2,
        "chunk_sessions": 1,
        "purpose": "source_viability_not_gate_evidence",
        "windows": [
            {
                "id": "recent",
                "purpose": "Test sessions",
                "start_session_date": "2026-08-16",
                "end_session_date_exclusive": "2026-08-19",
            }
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return AcquisitionMonitor(
        path,
        tmp_path / "work",
        tmp_path / "report.json",
        tmp_path,
        process_observer=lambda: "stopped",
        cache_seconds=0,
    )


def checkpoint(
    monitor: AcquisitionMonitor,
    index: int,
    ticks: int,
    *,
    anomalies: int = 0,
    revised: bool = False,
) -> tuple[Path, dict[str, Any]]:
    expected = monitor.plan.chunks[index]
    metrics: dict[str, Any] = {
        **dict.fromkeys(DIAGNOSTIC_FIELDS, 0),
        "tick_count": ticks,
        "active_minutes": min(ticks, 2),
        "max_intertick_gap_milliseconds": 3500 if ticks else None,
        "positive_spread_counts": [["0.0001", ticks]] if ticks else [],
        "crossed_quotes": anomalies,
    }
    environment = {"runner_sha256": "old", "analysis_module_sha256": "old", "spec_sha256": "old"}
    chunk = {
        "chunk_id": expected.identity,
        "logical_symbol": expected.symbol,
        "broker_symbol": expected.broker_symbol,
        "window_id": expected.window,
        "session_date": expected.session_date,
        "metrics": metrics,
    }
    payload = {
        "schema_version": 1,
        "probe_version": "fbs-tick-continuity-v1",
        "plan_hash": monitor.plan.plan_hash,
        "source": monitor.plan.source,
        "environment": environment,
        "environment_sha256": digest(environment),
        "run_id": "test-run",
        "completed_at_utc": f"2026-09-04T10:0{index}:00+00:00",
        "chunk": chunk,
        "raw": {},
        "fetches": [
            {
                "metrics": metrics,
                "tick_count": ticks,
                "shape": {
                    "elapsed_seconds": 2,
                    "discarded_before_start": 0,
                    "discarded_after_end": 0,
                    "mt5_error_snapshot": {"code": 1, "text": "Success"},
                },
            }
            for _ in range(2)
        ],
        "repeat_comparisons": [{"identical": not revised}],
    }
    path = (
        monitor.work_dir
        / monitor.plan.plan_hash
        / expected.symbol
        / expected.window
        / f"{expected.session_date}.source-ticks.tsv.checkpoint.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    save_checkpoint(path, payload)
    return path, payload


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    payload["integrity"] = {
        "algorithm": "sha256",
        "payload_sha256": digest(
            {key: value for key, value in payload.items() if key != "integrity"}
        ),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_and_empty_evidence_never_means_quality_pass(monitor: AcquisitionMonitor) -> None:
    missing = monitor.snapshot()
    assert missing["chunks_expected"] == 3
    assert missing["chunks_completed"] == 0
    assert missing["structural_state"] == "unknown"
    checkpoint(monitor, 0, 0)
    empty = monitor.snapshot()
    assert empty["chunks_completed"] == 1
    assert empty["chunks_empty"] == 1
    assert empty["ticks"] == 0
    assert empty["structural_state"] == "unknown"
    assert empty["quality_indeterminate"]
    assert not empty["retrieval_complete"]


def test_counts_primary_ticks_only_and_includes_all_fetch_pacing(
    monitor: AcquisitionMonitor,
) -> None:
    checkpoint(monitor, 0, 0)
    checkpoint(monitor, 1, 100, revised=True)
    snapshot = monitor.snapshot()
    assert snapshot["ticks"] == 100
    assert snapshot["chunks_completed"] == 2
    assert snapshot["structural_state"] == "clean_observed"
    window = snapshot["windows"][0]
    assert window["fetch_rows"] == 200
    assert window["fetch_seconds"] == 8
    assert window["repeat_mismatches"] == 1
    assert window["max_gap_seconds"] == 3.5
    assert window["spread_p95"] == 0.0001
    metrics = render_metrics(snapshot).decode()
    assert "tradebot_acquisition_ticks 100.0" in metrics
    assert 'tradebot_acquisition_process_state{state="stopped"} 1.0' in metrics
    assert "old" not in metrics
    assert "test-run" not in metrics


def test_stale_report_does_not_override_checkpoint_progress(monitor: AcquisitionMonitor) -> None:
    checkpoint(monitor, 0, 0)
    checkpoint(monitor, 1, 100)
    partial = monitor.report.with_name("report.partial.json")
    partial.write_text(
        json.dumps(
            {
                "plan": {"plan_hash": monitor.plan.plan_hash},
                "completed_at_utc": "2026-09-04T10:00:00+00:00",
                "dataset": {"observed_chunks": 1},
                "status": "PARTIAL",
                "failure": {"kind": "PLANNED_STOP"},
            }
        ),
        encoding="utf-8",
    )
    snapshot = monitor.snapshot()
    assert snapshot["chunks_completed"] == 2
    assert snapshot["report_chunks"] == 1
    assert snapshot["report_stale"]
    assert snapshot["process_state"] == "stopped"
    assert snapshot["run_state"] == 0
    assert not snapshot["reported_failure"]


def test_corruption_is_not_counted_or_hidden_behind_cached_success(
    monitor: AcquisitionMonitor,
) -> None:
    path, payload = checkpoint(monitor, 0, 100)
    assert monitor.snapshot()["chunks_completed"] == 1
    # A distinct size makes this deterministic on filesystems with coarse mtime resolution.
    payload["chunk"]["metrics"]["tick_count"] = 2500
    path.write_text(json.dumps(payload), encoding="utf-8")
    broken = monitor.snapshot()
    assert broken["chunks_completed"] == 0
    assert broken["checkpoints_invalid"] == 1
    assert broken["structural_state"] == "unknown"
    assert monitor.snapshot()["checkpoints_invalid"] == 1
    path.write_text("{", encoding="utf-8")
    assert monitor.snapshot()["checkpoints_invalid"] == 1
    assert monitor.snapshot()["checkpoints_invalid"] == 1


@pytest.mark.parametrize("fault", ["identity", "repeat", "nan", "negative"])
def test_signed_invalid_schema_is_rejected(monitor: AcquisitionMonitor, fault: str) -> None:
    path, payload = checkpoint(monitor, 0, 100)
    if fault == "identity":
        payload["chunk"]["logical_symbol"] = "EURUSD"
    elif fault == "repeat":
        payload["fetches"].pop()
    elif fault == "nan":
        payload["chunk"]["metrics"]["active_minutes"] = "NaN"
    else:
        payload["chunk"]["metrics"]["tick_count"] = -1
    save_checkpoint(path, payload)
    snapshot = monitor.snapshot()
    assert snapshot["checkpoints_invalid"] == 1
    assert snapshot["chunks_completed"] == 0


def test_process_unknown_and_anomaly_are_separate_from_completion(
    monitor: AcquisitionMonitor,
) -> None:
    for index in range(3):
        checkpoint(monitor, index, 10, anomalies=int(index == 1))
    monitor.process_observer = lambda: "unknown"
    snapshot = monitor.snapshot()
    assert snapshot["retrieval_complete"]
    assert snapshot["run_state"] == -1
    assert snapshot["structural_state"] == "anomalies"
    assert snapshot["structural_anomalies"] == 2
    assert snapshot["quality_indeterminate"]
    assert not snapshot["evidence_identity_current"]


def test_plan_cannot_escape_work_directory(tmp_path: Path) -> None:
    plan = {
        "schema_version": 1,
        "probe_id": "test",
        "source": "FBS-Demo",
        "chunk_sessions": 1,
        "purpose": "source_viability_not_gate_evidence",
        "repeat_fetches": 2,
        "symbols": {"../GBPUSD": "GBPUSD"},
        "windows": [
            {
                "id": "recent",
                "purpose": "Test",
                "start_session_date": "2026-08-16",
                "end_session_date_exclusive": "2026-08-19",
            }
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="identifier"):
        read_plan(path)


def test_json_cache_rejects_oversize_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tradebot.monitoring.acquisition_exporter.MAX_JSON_BYTES", 8)
    path = tmp_path / "large.json"
    path.write_text('{"long":"value"}', encoding="utf-8")
    with pytest.raises(ValueError):
        JsonCache().read(path)


def test_monitor_cache_avoids_repeated_process_queries(monitor: AcquisitionMonitor) -> None:
    calls = 0

    def process() -> str:
        nonlocal calls
        calls += 1
        return "running"

    monitor.process_observer = process
    monitor.cache_seconds = 30
    assert monitor.snapshot()["process_state"] == "running"
    assert monitor.snapshot()["process_state"] == "running"
    assert calls == 1


def test_platform_readiness_uses_gate_spec_and_source_evidence(tmp_path: Path) -> None:
    source = tmp_path / "src/tradebot"
    (source / "core").mkdir(parents=True)
    (source / "core/bus.py").write_text("# Test source file", encoding="utf-8")
    (source / "data").mkdir()
    (source / "data/ingest.py").write_text("# Test source file", encoding="utf-8")
    docs = tmp_path / "docs"
    (docs / "reports").mkdir(parents=True)
    (docs / "SPEC.md").write_text("Frozen specification", encoding="utf-8")
    spec_hash = hashlib.sha256(b"Frozen specification").hexdigest()
    gate = docs / "reports/gate0_evidence.md"
    gate.write_text(f"Status: **APPROVED**.\nFrozen SHA-256 `{spec_hash}`", encoding="utf-8")
    status = platform_status(tmp_path, JsonCache())
    assert [item["state"] for item in status["phases"]] == [2, 1, 0, 0, 0, 0]
    assert status["execution_enabled"] == -1
    assert status["demo_flags"]["pnl_reported"] == -1
    gate.write_text("Status: **APPROVED**.\nFrozen SHA-256 `different`", encoding="utf-8")
    changed = platform_status(tmp_path, JsonCache())
    assert changed["phases"][0]["state"] == 1
    assert changed["phases"][0]["gate_approval_recorded"]
    assert not changed["phases"][0]["gate_spec_matches"]


def test_platform_does_not_invent_trading_or_performance(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs/env"
    config_dir.mkdir(parents=True)
    live = config_dir / "live.yaml"
    live.write_text("execution_enabled: false\n", encoding="utf-8")
    demo_dir = tmp_path / "build/gate0"
    demo_dir.mkdir(parents=True)
    (demo_dir / "demo-manifest.json").write_text(
        json.dumps(
            {
                "evidence_class": "smoke-demo-only-not-performance-evidence",
                "execution_enabled": False,
                "pnl_reported": False,
                "costs_modelled": False,
            }
        ),
        encoding="utf-8",
    )
    observed = platform_status(tmp_path, JsonCache())
    assert observed["execution_enabled"] == 0
    assert observed["demo_flags"]["pnl_reported"] == 0
    assert observed["demo_evidence_class"] == "smoke-demo-only-not-performance-evidence"
    live.write_text("execution_enabled: false\nexecution_enabled: true\n", encoding="utf-8")
    assert platform_status(tmp_path, JsonCache())["execution_enabled"] == -1


@pytest.mark.parametrize("kind", ["target", "other", "hidden", "failed"])
def test_process_inspection_matches_identity_and_preserves_unknown(
    monitor: AcquisitionMonitor,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    monkeypatch.setattr(acquisition_exporter, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        "tradebot.monitoring.acquisition_exporter.shutil.which", lambda name: "/powershell.exe"
    )
    work = monitor.work_dir if kind == "target" else monitor.work_dir / "other"
    command = f'python scripts/fbs_tick_continuity_probe.py --work-dir "{work}"'
    records = [{"CommandLine": None if kind == "hidden" else command}]

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if kind == "failed":
            raise subprocess.TimeoutExpired("powershell", 4)
        return subprocess.CompletedProcess([], 0, json.dumps(records), "")

    monkeypatch.setattr("tradebot.monitoring.acquisition_exporter.subprocess.run", run)
    expected = {"target": "running", "other": "stopped", "hidden": "unknown", "failed": "unknown"}
    assert observe_process(monitor.work_dir, monitor.repository) == expected[kind]


def test_http_serves_observations_and_has_no_control_endpoint(monitor: AcquisitionMonitor) -> None:
    checkpoint(monitor, 0, 20)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(monitor))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["scope"] == "exporter_only"
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["ticks"] == 20
        connection.request("GET", "/metrics")
        response = connection.getresponse()
        assert response.status == 200
        assert b"tradebot_platform_execution_enabled -1.0" in response.read()
        connection.request("GET", "/resume")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
        connection.request("POST", "/resume")
        response = connection.getresponse()
        assert response.status == 501
        response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def write_gate1_report(monitor: AcquisitionMonitor, *, days: int = 30) -> dict[str, Any]:
    spec = monitor.repository / "docs/SPEC.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("Test frozen specification", encoding="utf-8")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "finished_at": "2026-09-04T19:00:00+00:00",
        "selection": {
            "plan_hash": monitor.plan.plan_hash,
            "days": days,
            "code_hashes": {"docs/SPEC.md": hashlib.sha256(spec.read_bytes()).hexdigest()},
        },
        "scope": "Gate-1 30-day reproducibility" if days >= 30 else "engineering smoke only",
        "selected_primary_ticks": 10000,
        "independent_rebuilds_byte_identical": True,
        "raw_files_unchanged": True,
        "implementation_unchanged": True,
        "reproducibility_status": "PASSED" if days >= 30 else "NOT_SATISFIED",
        "liquid_hours_flagged_bar_criterion": "INDETERMINATE: no approved dated liquidity calendar",
        "quality": [
            {
                "quality_status": "INDETERMINATE",
                "calendar_status": "INDETERMINATE",
                "calendar_days_missing": ["2026-08-16", "2026-08-17"],
                "flag_counts": [["TS_RECV_IMPUTED", 10000]],
                "retrospective_flag_counts": [["PRICE_OUTLIER", 3]],
            }
        ],
    }
    publish_gate1_report(monitor, payload)
    return payload


def publish_gate1_report(monitor: AcquisitionMonitor, payload: dict[str, Any]) -> None:
    report = monitor.gate1_report
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload), encoding="utf-8")
    report.with_name("report.sha256.json").write_text(
        json.dumps(
            {
                "report.json": hashlib.sha256(report.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def test_gate1_missing_or_unpublished_report_is_not_success(monitor: AcquisitionMonitor) -> None:
    missing = gate1_status(monitor.gate1_report, monitor.repository, monitor.plan.plan_hash)
    assert missing["report_state"] == 0
    assert missing["independent_rebuilds_byte_identical"] == -1
    write_gate1_report(monitor)
    monitor.gate1_report.with_name("report.sha256.json").unlink()
    unpublished = gate1_status(monitor.gate1_report, monitor.repository, monitor.plan.plan_hash)
    assert unpublished["report_state"] == 1
    assert unpublished["reproducibility_recorded"] == -1


def test_gate1_measured_rebuild_does_not_promote_quality_acceptance(
    monitor: AcquisitionMonitor,
) -> None:
    write_gate1_report(monitor)
    snapshot = monitor.snapshot()
    evidence = snapshot["gate1"]
    assert evidence["report_state"] == 2
    assert evidence["selected_days"] == 30
    assert evidence["reproducibility_recorded"] == 1
    assert evidence["raw_files_unchanged"] == 1
    assert evidence["report_code_current"] == 1
    assert evidence["tick_quality_state"] == 0
    assert evidence["calendar_unknown_days"] == 2
    assert evidence["liquid_hours_criterion_state"] == 0
    metrics = render_metrics(snapshot).decode()
    assert 'tradebot_gate1_flag_rows{category="retrospective",flag="PRICE_OUTLIER"} 3.0' in metrics
    (monitor.repository / "docs/SPEC.md").write_text(
        "Changed implementation identity", encoding="utf-8"
    )
    assert monitor.snapshot()["gate1"]["report_code_current"] == 0


def test_gate1_small_smoke_stays_below_30day_criterion(monitor: AcquisitionMonitor) -> None:
    payload = write_gate1_report(monitor, days=2)
    measured = gate1_status(monitor.gate1_report, monitor.repository, monitor.plan.plan_hash)
    assert measured["report_state"] == 2
    assert measured["independent_rebuilds_byte_identical"] == 1
    assert measured["reproducibility_recorded"] == 0
    payload["reproducibility_status"] = "PASSED"
    publish_gate1_report(monitor, payload)
    assert (
        gate1_status(monitor.gate1_report, monitor.repository, monitor.plan.plan_hash)[
            "report_state"
        ]
        == 3
    )


def test_gate1_rejects_tampered_and_other_plan_reports(monitor: AcquisitionMonitor) -> None:
    payload = write_gate1_report(monitor)
    monitor.gate1_report.write_text("{}", encoding="utf-8")
    assert (
        gate1_status(monitor.gate1_report, monitor.repository, monitor.plan.plan_hash)[
            "report_state"
        ]
        == 3
    )
    payload["selection"]["plan_hash"] = "another-plan"
    publish_gate1_report(monitor, payload)
    invalid = gate1_status(monitor.gate1_report, monitor.repository, monitor.plan.plan_hash)
    assert invalid["report_state"] == 3
    assert invalid["reproducibility_recorded"] == -1


def test_data_quality_current_report_panels_use_only_instant_queries() -> None:
    repository = Path(__file__).resolve().parents[3]
    dashboard = json.loads(
        (repository / "deploy/grafana/dashboards/data-quality.json").read_text(encoding="utf-8")
    )
    current_panels = [
        panel for panel in dashboard["panels"] if panel["type"] in ("stat", "bargauge")
    ]
    targets = [target for panel in current_panels for target in panel.get("targets", [])]
    assert dashboard["uid"] == "tradebot-data-quality"
    assert targets and all(target.get("instant") is True for target in targets)
    assert all(target.get("range") is False for target in targets)
    # A missing report or removed flag must not retain an older range's last non-null value.
    expressions = {target["expr"] for target in targets}
    assert "tradebot_gate1_report_state" in expressions
    assert 'tradebot_gate1_flag_rows{category="causal"}' in expressions
    assert 'tradebot_gate1_flag_rows{category="retrospective"}' in expressions
    assert "time() - (tradebot_gate1_report_timestamp_seconds > 0)" in expressions
