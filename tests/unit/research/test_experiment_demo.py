"""Engineering integration: accounting precedes execution and no lockbox forecasts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradebot.research import experiment_demo
from tradebot.research.experiment_demo import run_synthetic_attempt, synthetic_declaration
from tradebot.research.registry import Registry


def test_identical_attempts_keep_identical_artifacts_and_distinct_audit_rows(
    tmp_path: Path,
) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    identity = registry.register(synthetic_declaration())
    root = tmp_path / "artifacts"
    one = run_synthetic_attempt(registry, identity, "one", partition="training", output_root=root)
    two = run_synthetic_attempt(registry, identity, "two", partition="training", output_root=root)
    assert one == two
    assert registry.audit(identity)["counts"] == {
        "started": 2,
        "completed": 2,
        "failed": 0,
        "incomplete": 0,
    }
    report = json.loads((root / one["report.json"] / "report.json").read_bytes())
    assert report["evidence_class"] == "ENGINEERING_ONLY"
    assert report["bars_processed"] == 146
    assert report["input_bars"] == 320
    assert report["routing_by_classification"] == {
        "training": 146,
        "validation": 136,
        "lockbox": 6,
        "purged": 12,
        "outside": 20,
    }
    assert report["decisions_by_status"] == {"warmup": 128, "forecast": 18}
    assert report["lockbox_decisions"] == report["orders_created"] == 0
    assert report["execution_enabled"] is report["pnl_reported"] is False
    assert report["gate_approvals_claimed"] == []
    assert not (root / "latest.json").exists()


def test_validation_has_fresh_state_and_excludes_lockbox(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    identity = registry.register(synthetic_declaration())
    root = tmp_path / "artifacts"
    artifacts = run_synthetic_attempt(
        registry, identity, "validate", partition="validation", output_root=root
    )
    trace = [
        json.loads(line)
        for line in (root / artifacts["report.json"] / "decisions.jsonl").read_text().splitlines()
    ]
    assert len(trace) == 136
    assert {row["status"] for row in trace[:2]} == {"suppressed"}
    assert all("2024-01-08T11:20:00" <= row["bar_open"] < "2024-01-08T12:30:00" for row in trace)
    assert max(row["decision_at"] for row in trace) < "2024-01-08T12:29:00"


def test_forbidden_lockbox_is_logged_before_any_fixture_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    identity = registry.register(synthetic_declaration())

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("fixture was accessed for a forbidden lockbox attempt")

    monkeypatch.setattr(experiment_demo, "synthetic_setup", forbidden)
    with pytest.raises(ValueError, match="lockbox"):
        run_synthetic_attempt(
            registry, identity, "denied", partition="lockbox", output_root=tmp_path / "artifacts"
        )
    audit = registry.audit(identity)
    assert audit["counts"] == {"started": 1, "completed": 0, "failed": 1, "incomplete": 0}
    assert not (tmp_path / "artifacts").exists()


def test_changed_declaration_is_failed_not_silently_replaced(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    declaration = synthetic_declaration()
    declaration["config_sha256"] = "f" * 64
    identity = registry.register(declaration)
    with pytest.raises(ValueError, match="preregistration"):
        run_synthetic_attempt(
            registry, identity, "changed", partition="training", output_root=tmp_path / "artifacts"
        )
    assert registry.audit(identity)["counts"] == {
        "started": 1,
        "completed": 0,
        "failed": 1,
        "incomplete": 0,
    }


def test_corrupt_existing_artifact_cannot_become_success(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    identity = registry.register(synthetic_declaration())
    root = tmp_path / "artifacts"
    first = run_synthetic_attempt(registry, identity, "one", partition="training", output_root=root)
    (root / first["report.json"] / "report.json").write_text("corrupt")
    with pytest.raises(FileExistsError, match="differs"):
        run_synthetic_attempt(registry, identity, "two", partition="training", output_root=root)
    assert registry.audit(identity)["counts"] == {
        "started": 2,
        "completed": 1,
        "failed": 1,
        "incomplete": 0,
    }


def test_input_failure_is_recorded_after_started_before_any_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    identity = registry.register(synthetic_declaration())

    def broken_fixture(git_sha: str) -> None:
        assert registry.audit(identity)["counts"] == {
            "started": 1,
            "completed": 0,
            "failed": 0,
            "incomplete": 1,
        }
        raise OSError("deliberate engineering fixture failure")

    monkeypatch.setattr(experiment_demo, "synthetic_setup", broken_fixture)
    with pytest.raises(OSError, match="deliberate"):
        run_synthetic_attempt(
            registry, identity, "broken", partition="training", output_root=tmp_path / "artifacts"
        )
    assert registry.audit(identity)["counts"] == {
        "started": 1,
        "completed": 0,
        "failed": 1,
        "incomplete": 0,
    }


def test_abrupt_interruption_remains_incomplete_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    identity = registry.register(synthetic_declaration())

    def interrupted_fixture(git_sha: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(experiment_demo, "synthetic_setup", interrupted_fixture)
    with pytest.raises(KeyboardInterrupt):
        run_synthetic_attempt(
            registry,
            identity,
            "interrupted",
            partition="validation",
            output_root=tmp_path / "artifacts",
        )
    audit = registry.audit(identity)
    assert audit["counts"] == {"started": 1, "completed": 0, "failed": 0, "incomplete": 1}
    attempts = audit["attempts"]
    assert isinstance(attempts, list)
    assert attempts[0]["metadata"]["partition"] == "validation"
