"""Trust-boundary tests for the read-only engineering replay dashboard consumer."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from tradebot.monitoring.research_status import add_research_metrics, research_status


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def dataset_digest(rows: list[dict[str, str]]) -> str:
    result = hashlib.sha256(b"tradebot.clean-dataset.v1\n")
    for row in rows:
        result.update(f"{row['path']}\t{row['sha256']}\n".encode())
    return result.hexdigest()


def publish(tmp_path: Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any]]:
    repository = tmp_path / "repository"
    module = repository / "src/tradebot/__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text("# monitored implementation\n", encoding="utf-8")
    source_repository = Path(__file__).resolve().parents[3]
    spec = repository / "docs/SPEC.md"
    spec.parent.mkdir(parents=True)
    spec.write_bytes((source_repository / "docs/SPEC.md").read_bytes())
    implementation = [{"path": "tradebot", "sha256": digest(module.read_bytes())}]
    source_manifest = [
        {"path": "synthetic-fixture.json", "sha256": "a" * 64},
    ]
    config = {
        "instruments": ["Synthetic/EURUSD"],
        "timeframe_seconds": 60,
    }
    trace_sha256 = "c" * 64
    identity = {
        "spec_version": "1.0",
        "spec_sha256": digest(spec.read_bytes()),
        "config": config,
        "config_sha256": digest(canonical(config)),
        "implementation": implementation,
        "implementation_sha256": digest(canonical(implementation)),
        "runtime": {"python": "test"},
        "randomness": "NONE",
        "provenance": {
            "dataset_id": dataset_digest(source_manifest),
            "source_kind": "synthetic",
            "source_manifest": source_manifest,
        },
    }
    run_id = digest(canonical([identity, trace_sha256]))
    report: dict[str, Any] = {
        "schema_version": 1,
        "evidence_class": "engineering-decision-replay-only",
        "run_id": run_id,
        "status": "COMPLETED",
        "identity": identity,
        "execution_enabled": False,
        "orders_created": 0,
        "costs_modelled": False,
        "pnl_reported": False,
        "economic_evaluation": "NOT_PERFORMED",
        "data_acceptance": "NOT_ASSERTED",
        "gate_approvals_claimed": [],
        "forecast_scaling": "UNCALIBRATED",
        "bars_processed": 4,
        "decisions_by_status": {"forecast": 1, "suppressed": 1, "warmup": 2},
        "instruments": {"Synthetic/EURUSD": {"forecast": 1, "suppressed": 1, "warmup": 2}},
        "trace": {"path": "decisions.jsonl", "records": 4, "sha256": trace_sha256},
        # Deliberately large details are present in a producer report but ignored by monitoring.
        "latest_decisions": {"Synthetic/EURUSD": {"features": {"never": "displayed"}}},
    }
    root = repository / "build/research/decision-replay"
    run = root / run_id
    run.mkdir(parents=True)
    report_path = run / "report.json"
    report_path.write_bytes(canonical(report))
    pointer: dict[str, Any] = {
        "schema_version": 1,
        "evidence_class": "engineering-decision-replay-only",
        "run_id": run_id,
        "report": f"{run_id}/report.json",
        "sha256": digest(report_path.read_bytes()),
    }
    (root / "latest.json").write_bytes(canonical(pointer))
    return root, repository, report, pointer


def republish(root: Path, report: dict[str, Any], pointer: dict[str, Any]) -> None:
    report_path = root / pointer["report"]
    report_path.write_bytes(canonical(report))
    pointer["sha256"] = digest(report_path.read_bytes())
    (root / "latest.json").write_bytes(canonical(pointer))


def test_verified_summary_is_bounded_engineering_evidence(tmp_path: Path) -> None:
    root, repository, _, _ = publish(tmp_path)
    observed = research_status(root, repository)
    assert observed["artifact_state"] == "verified"
    assert observed["overview_state"] == "ENGINEERING_ONLY"
    assert observed["source_kind"] == "synthetic"
    assert observed["source_scope"] == "SYNTHETIC_ENGINEERING_ONLY"
    assert observed["bars_processed"] == 4
    assert observed["decisions_by_status"] == {
        "warmup": 2,
        "suppressed": 1,
        "abstain": 0,
        "forecast": 1,
    }
    assert observed["implementation_current"] is True
    assert "latest_decisions" not in observed

    registry = CollectorRegistry()
    add_research_metrics(registry, observed)
    metrics = generate_latest(registry).decode()
    assert 'tradebot_research_artifact_state{state="verified"} 1.0' in metrics
    assert 'tradebot_research_source_kind{kind="synthetic"} 1.0' in metrics
    assert "tradebot_research_bars_processed 4.0" in metrics
    assert "tradebot_research_implementation_current 1.0" in metrics
    assert 'tradebot_research_decisions{status="forecast"} 1.0' in metrics
    assert "never" not in metrics
    assert "run_id" not in metrics

    (repository / "src/tradebot/__init__.py").write_text("# changed\n", encoding="utf-8")
    historical = research_status(root, repository)
    assert historical["implementation_current"] is False
    historical_registry = CollectorRegistry()
    add_research_metrics(historical_registry, historical)
    assert "tradebot_research_implementation_current 0.0" in (
        generate_latest(historical_registry).decode()
    )

    unavailable_registry = CollectorRegistry()
    add_research_metrics(unavailable_registry, {**historical, "implementation_current": None})
    assert "tradebot_research_implementation_current" not in (
        generate_latest(unavailable_registry).decode()
    )


def test_missing_and_tampered_reports_stay_unknown_without_numeric_results(
    tmp_path: Path,
) -> None:
    missing = research_status(tmp_path / "missing", tmp_path)
    assert missing["artifact_state"] == "missing"
    assert missing["bars_processed"] is None
    missing_registry = CollectorRegistry()
    add_research_metrics(missing_registry, missing)
    assert (
        "tradebot_research_implementation_current" not in generate_latest(missing_registry).decode()
    )

    root, repository, _, pointer = publish(tmp_path)
    (root / pointer["report"]).write_text("{}", encoding="utf-8")
    rejected = research_status(root, repository)
    assert rejected["artifact_state"] == "invalid"
    assert rejected["overview_state"] == "UNKNOWN"
    registry = CollectorRegistry()
    add_research_metrics(registry, rejected)
    metrics = generate_latest(registry).decode()
    assert 'tradebot_research_artifact_state{state="invalid"} 1.0' in metrics
    assert 'tradebot_research_overview_state{state="unknown"} 1.0' in metrics
    assert "tradebot_research_bars_processed" not in metrics
    assert "tradebot_research_decisions" not in metrics
    assert "tradebot_research_implementation_current" not in metrics


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report.update(schema_version=2),
        lambda report: report.update(execution_enabled=True),
        lambda report: report.update(economic_evaluation="PROFITABLE"),
        lambda report: report.update(bars_processed=5),
        lambda report: report["identity"]["provenance"].update(source_kind="live"),
        lambda report: report["instruments"]["Synthetic/EURUSD"].update(forecast=2),
        lambda report: report["trace"].update(records=3),
    ],
)
def test_unsupported_unsafe_or_inconsistent_report_is_rejected(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> None:
    root, repository, report, pointer = publish(tmp_path)
    mutate(report)
    republish(root, report, pointer)
    assert research_status(root, repository)["artifact_state"] == "invalid"


def test_malformed_duplicate_nonfinite_and_oversize_json_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repository, report, pointer = publish(tmp_path)
    latest = root / "latest.json"
    latest.write_text("{", encoding="utf-8")
    assert research_status(root, repository)["artifact_state"] == "invalid"

    latest.write_text(
        json.dumps(pointer)[:-1] + ',"schema_version":1}',
        encoding="utf-8",
    )
    assert research_status(root, repository)["artifact_reason"] == "duplicate_json_key"

    republish(root, report, pointer)
    report_path = root / pointer["report"]
    payload = canonical(report).replace(b'"bars_processed":4', b'"bars_processed":NaN')
    report_path.write_bytes(payload)
    pointer["sha256"] = digest(payload)
    latest.write_bytes(canonical(pointer))
    assert research_status(root, repository)["artifact_reason"] == "nonfinite_json"

    republish(root, report, pointer)
    payload = canonical(report).replace(b'"bars_processed":4', b'"bars_processed":' + b"9" * 5000)
    report_path.write_bytes(payload)
    pointer["sha256"] = digest(payload)
    latest.write_bytes(canonical(pointer))
    assert research_status(root, repository)["artifact_reason"] == "malformed_json"

    republish(root, report, pointer)
    monkeypatch.setattr("tradebot.monitoring.research_status.LATEST_MAX_BYTES", 8)
    assert research_status(root, repository)["artifact_reason"] == "unsafe_or_oversize_file"


def test_report_path_must_be_exact_and_symlinks_are_rejected(tmp_path: Path) -> None:
    root, repository, _, pointer = publish(tmp_path)
    pointer["report"] = "../outside/report.json"
    (root / "latest.json").write_bytes(canonical(pointer))
    assert research_status(root, repository)["artifact_reason"] == "unsafe_report_path"

    root, repository, _, pointer = publish(tmp_path / "linked")
    report_path = root / pointer["report"]
    outside = tmp_path / "outside-report.json"
    outside.write_bytes(report_path.read_bytes())
    report_path.unlink()
    try:
        report_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this host")
    assert research_status(root, repository)["artifact_reason"] == "symlink_rejected"


def test_schema_booleans_config_drift_and_internal_run_id_are_rejected(tmp_path: Path) -> None:
    root, repository, report, pointer = publish(tmp_path / "schema")
    pointer["schema_version"] = True
    (root / "latest.json").write_bytes(canonical(pointer))
    assert research_status(root, repository)["artifact_state"] == "invalid"

    root, repository, report, pointer = publish(tmp_path / "report-schema")
    report["schema_version"] = True
    republish(root, report, pointer)
    assert research_status(root, repository)["artifact_state"] == "invalid"

    root, repository, report, pointer = publish(tmp_path / "config")
    report["identity"]["config"]["timeframe_seconds"] = 300
    republish(root, report, pointer)
    assert research_status(root, repository)["artifact_reason"] == "config_identity_mismatch"

    root, repository, report, pointer = publish(tmp_path / "run-id")
    report["identity"]["randomness"] = "FIXED_BUT_DIFFERENT"
    republish(root, report, pointer)
    assert research_status(root, repository)["artifact_reason"] == "run_identity_mismatch"


def test_research_dashboard_is_small_and_explicitly_not_trading() -> None:
    repository = Path(__file__).resolve().parents[3]
    dashboard = json.loads(
        (repository / "deploy/grafana/dashboards/research.json").read_text(encoding="utf-8")
    )
    assert dashboard["uid"] == "tradebot-research"
    assert len(dashboard["panels"]) == 6
    expressions = {
        target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])
    }
    targets = [target for panel in dashboard["panels"] for target in panel.get("targets", [])]
    assert targets and all(target.get("instant") is True for target in targets)
    assert all(target.get("range") is False for target in targets)
    assert "tradebot_research_bars_processed" in expressions
    assert "tradebot_research_decisions" in expressions
    assert "tradebot_research_implementation_current" in expressions
    content = dashboard["panels"][0]["options"]["content"]
    assert "not live calls or trades" in content
    assert "no execution, orders, fills, costs or P&L" in content
    assert "HISTORICAL" in content
    system = json.loads(
        (repository / "deploy/grafana/dashboards/system.json").read_text(encoding="utf-8")
    )
    research_links = [link for link in system["links"] if link["title"] == "Research & Backtesting"]
    assert research_links == [
        {
            "title": "Research & Backtesting",
            "type": "link",
            "url": "/d/tradebot-research",
            "targetBlank": False,
        }
    ]
