"""Published calibration artifacts describe invented engineering, not market training."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradebot.data.storage import sha256_path
from tradebot.research import calibration_demo
from tradebot.research.calibration_demo import main, run_synthetic_calibration
from tradebot.research.fitting import load_calibration
from tradebot.research.registry import Registry


def test_synthetic_attempt_persists_frozen_models_and_repeatable_report(tmp_path: Path) -> None:
    first = run_synthetic_calibration(output_root=tmp_path, attempt_id="first")
    second = run_synthetic_calibration(output_root=tmp_path, attempt_id="second")
    assert first.report_path == second.report_path
    assert sha256_path(first.report_path) == first.report_sha256
    report = json.loads(first.report_path.read_bytes())
    assert report["status"] == "COMPLETED"
    assert report["evidence_class"] == "ENGINEERING_ONLY"
    assert report["models_fitted"] == 2
    assert report["market_data_models_fitted"] == 0
    assert report["execution_enabled"] is False
    assert report["pnl_reported"] is False
    assert report["gate_approvals_claimed"] == []
    assert report["declaration"]["financial_variants_tried"] == 0
    for result in report["results"]:
        artifact = tmp_path / result["calibration_path"]
        calibration = load_calibration(artifact, expected_sha256=result["calibration_sha256"])
        assert calibration.sample_count == 9
        assert len(result["validation"]["forecasts"]) == 3
        assert result["fit"]["release_sha256"] is None
    audit = Registry(tmp_path / "registry.sqlite").audit(first.experiment_id)
    assert audit["counts"] == {"started": 2, "completed": 2, "failed": 0, "incomplete": 0}


def test_failure_retained_and_completed_report_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise ValueError("injected fitting failure")

    monkeypatch.setattr(calibration_demo, "fit_replay", fail)
    with pytest.raises(ValueError, match="injected"):
        run_synthetic_calibration(output_root=tmp_path, attempt_id="broken")
    assert not (tmp_path / "reports").exists()
    # Read-only SQL inspection of this test's engineering ledger preserves the
    # otherwise unreachable registration id after the runner raises.
    import sqlite3

    with sqlite3.connect(tmp_path / "registry.sqlite") as database:
        events = [
            json.loads(row[0])
            for row in database.execute("SELECT body FROM registry_events ORDER BY seq")
        ]
    assert [event["operation"] for event in events] == ["REGISTER", "START", "FINISH"]
    assert events[-1]["payload"]["status"] == "FAILED"


def test_cli_exposes_only_synthetic_run_and_explicit_nontraining_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--output-root", str(tmp_path), "--attempt-id", "cli"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["market_data_models_fitted"] == 0
    assert Path(result["report"]).is_file()
    with pytest.raises(SystemExit):
        main(["--output-root", str(tmp_path), "--attempt-id", "denied", "--snapshot", "FBS.json"])


def test_existing_report_cannot_be_overwritten(tmp_path: Path) -> None:
    first = run_synthetic_calibration(output_root=tmp_path, attempt_id="first")
    first.report_path.write_text("damaged", encoding="utf-8")
    with pytest.raises(FileExistsError, match="differs"):
        run_synthetic_calibration(output_root=tmp_path, attempt_id="retry")
    assert first.report_path.read_text() == "damaged"
    audit = Registry(tmp_path / "registry.sqlite").audit(first.experiment_id)
    assert audit["counts"] == {"started": 2, "completed": 1, "failed": 1, "incomplete": 0}
