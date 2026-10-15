from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from tradebot.monitoring.reference_status import add_reference_metrics, reference_status


@pytest.fixture
def evidence(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    identities = {}
    for name in (
        "docs/SPEC.md",
        "scripts/evaluate_reference_month.py",
        "src/tradebot/data/reference_acceptance.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture identity")
        identities[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report: dict[str, Any] = {
        "schema_version": 1,
        "evidence_class": "REFERENCE_MONTH_ACCEPTANCE_EVALUATION",
        "gate_approved": False,
        "training_enabled": False,
        "generated_at_utc": "2026-09-06T13:30:45.385123+00:00",
        "code_and_spec_sha256": identities,
        "status": "INDETERMINATE",
        "result": {
            "status": "INDETERMINATE",
            "gate_approved": False,
            "human_identities_authenticated": False,
            "approval_binding_verified": False,
            "counted_flagged_union": 16,
            "expected_liquid_minutes": 4410,
            "flagged_fraction": "0.003628117913832199546485260771",
            "strict_less_than_0_1_percent": False,
            "criterion": {
                "comparison": "STRICTLY_LESS_THAN",
                "denominator": "EXPECTED_LIQUID_MINUTE_BINS",
                "overlap_treatment": "UNION_EACH_MINUTE_COUNTED_ONCE",
                "threshold_fraction": "0.001",
                "threshold_percent": "0.1",
            },
            "scope": {"source": "FBS-Demo", "instrument": "EURUSD", "reference_month": "2024-10"},
        },
    }
    path = tmp_path / "build/reference/report.json"
    publish(path, report)
    return tmp_path, path, report


def publish(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(report).encode()
    path.write_bytes(content)
    path.with_name("report.sha256.json").write_text(
        json.dumps(
            {path.name: {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}}
        ),
        encoding="utf-8",
    )


def metrics(status: dict[str, Any]) -> str:
    registry = CollectorRegistry()
    add_reference_metrics(registry, status)
    return generate_latest(registry).decode()


def test_failed_calculation_and_incomplete_approval_are_distinct(
    evidence: tuple[Path, Path, dict[str, Any]],
) -> None:
    root, path, _ = evidence
    status = reference_status(path, root)
    assert status["report_state"] == 2
    assert status["report_path"] == "build/reference/report.json"
    assert status["acceptance_status"] == "INDETERMINATE"
    assert status["threshold_satisfied"] == 0
    assert status["counted_flagged_minutes"] == 16
    assert status["expected_liquid_minutes"] == 4410
    assert status["flagged_fraction"] == pytest.approx(16 / 4410)
    assert status["report_code_current"] == 1
    assert status["training_enabled"] == 0
    rendered = metrics(status)
    assert 'tradebot_reference_flagged_fraction{denominator="4410",numerator="16"}' in rendered
    assert "tradebot_reference_threshold_fraction 0.001" in rendered
    assert "tradebot_reference_acceptance_state 0.0" in rendered
    assert "tradebot_reference_training_enabled 0.0" in rendered


@pytest.mark.parametrize("fault", ["missing", "checksum_missing", "tampered", "bad_checksum"])
def test_unavailable_or_invalid_reports_never_export_measurements(
    evidence: tuple[Path, Path, dict[str, Any]], fault: str
) -> None:
    root, path, _ = evidence
    expected = 3
    if fault == "missing":
        path.unlink()
        expected = 0
    elif fault == "checksum_missing":
        path.with_name("report.sha256.json").unlink()
        expected = 1
    elif fault == "tampered":
        path.write_bytes(b"{}")
    else:
        path.with_name("report.sha256.json").write_bytes(b"{")
    status = reference_status(path, root)
    assert status["report_state"] == expected
    assert "counted_flagged_minutes" not in status
    assert "tradebot_reference_flagged_fraction" not in metrics(status)
    assert "tradebot_reference_training_enabled" not in metrics(status)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("flagged_fraction", "0"),
        ("flagged_fraction", "NaN"),
        ("strict_less_than_0_1_percent", True),
        ("counted_flagged_union", True),
        ("counted_flagged_union", -1),
        ("counted_flagged_union", 4411),
        ("expected_liquid_minutes", 0),
        ("status", "PASSED"),
        ("gate_approved", True),
        ("human_identities_authenticated", True),
        ("criterion", {}),
        ("scope", {}),
    ],
)
def test_checksum_alone_cannot_validate_contradictory_or_unsafe_claims(
    evidence: tuple[Path, Path, dict[str, Any]], field: str, value: object
) -> None:
    root, path, report = evidence
    report["result"][field] = value
    publish(path, report)
    assert reference_status(path, root)["report_state"] == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("evidence_class", "APPROVED_RELEASE"),
        ("training_enabled", True),
        ("gate_approved", True),
        ("generated_at_utc", "2026-09-06T13:30:45"),
        ("code_and_spec_sha256", {}),
    ],
)
def test_unsupported_report_contract_is_rejected(
    evidence: tuple[Path, Path, dict[str, Any]], field: str, value: object
) -> None:
    root, path, report = evidence
    report[field] = value
    publish(path, report)
    assert reference_status(path, root)["report_state"] == 3


def test_threshold_is_strict_and_calculation_pass_never_enables_training(
    evidence: tuple[Path, Path, dict[str, Any]],
) -> None:
    root, path, report = evidence
    result = report["result"]
    result.update(counted_flagged_union=1, expected_liquid_minutes=1000, flagged_fraction="0.001")
    report["status"] = result["status"] = "FAILED"
    publish(path, report)
    assert reference_status(path, root)["threshold_satisfied"] == 0
    result.update(
        counted_flagged_union=0,
        flagged_fraction="0",
        strict_less_than_0_1_percent=True,
        approval_binding_verified=True,
    )
    report["status"] = result["status"] = "PASSED"
    publish(path, report)
    status = reference_status(path, root)
    assert status["acceptance_state"] == 2
    assert status["training_enabled"] == 0
    assert status["gate_approval"] == "not asserted by monitoring"


def test_zero_denominator_keeps_rate_unknown(evidence: tuple[Path, Path, dict[str, Any]]) -> None:
    root, path, report = evidence
    report["result"].update(
        counted_flagged_union=0,
        expected_liquid_minutes=0,
        flagged_fraction=None,
        strict_less_than_0_1_percent=None,
    )
    publish(path, report)
    status = reference_status(path, root)
    assert status["report_state"] == 2
    assert "flagged_fraction" not in status
    assert "tradebot_reference_flagged_fraction" not in metrics(status)


def test_historical_code_is_not_current_and_foreign_spec_is_invalid(
    evidence: tuple[Path, Path, dict[str, Any]],
) -> None:
    root, path, _ = evidence
    (root / "scripts/evaluate_reference_month.py").write_bytes(b"changed code")
    assert reference_status(path, root)["report_code_current"] == 0
    (root / "docs/SPEC.md").write_bytes(b"different specification")
    assert reference_status(path, root)["report_state"] == 3


def test_reader_is_bounded_and_does_not_follow_paths_outside_repository(
    evidence: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path, _ = evidence
    assert reference_status(path, root / "other")["report_state"] == 3
    monkeypatch.setattr("tradebot.monitoring.reference_status.MAX_REPORT_BYTES", 8)
    assert reference_status(path, root)["report_state"] == 3


@pytest.mark.parametrize("nested_file", ["report", "checksum"])
def test_deep_json_cannot_disable_other_dashboard_evidence(
    evidence: tuple[Path, Path, dict[str, Any]], nested_file: str
) -> None:
    root, path, _ = evidence
    content = b"[" * 10_000 + b"]" * 10_000
    sidecar = path.with_name("report.sha256.json")
    if nested_file == "checksum":
        sidecar.write_bytes(content)
    else:
        path.write_bytes(content)
        sidecar.write_text(
            json.dumps(
                {path.name: {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}}
            ),
            encoding="utf-8",
        )
    status = reference_status(path, root)
    assert status["report_state"] == 3
    assert "tradebot_reference_flagged_fraction" not in metrics(status)
