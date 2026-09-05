"""The offline reference-month command publishes evidence without granting approval."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from tradebot.data.reference_acceptance import LoadedBars, ReferenceScope


@pytest.fixture
def runner() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "evaluate_reference_month.py"
    spec = importlib.util.spec_from_file_location("evaluate_reference_month_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_report_without_calendar_or_inventory_is_honestly_indeterminate(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_parquet_files", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        runner,
        "read_clean_bar_files",
        lambda _paths, *, scope: LoadedBars(bars=(), files=(), corpus_ids=()),
    )
    root = Path(__file__).resolve().parents[3]
    report = runner.build_report(
        scope=ReferenceScope(
            venue="FBS",
            source="FBS-Demo",
            instrument="EURUSD",
            calendar_instrument="FBS-Demo/EURUSD",
            reference_month="2024-10",
        ),
        bar_root=root,
        tick_root=None,
        policy_path=root / "configs/calendars/reference_month_policy_draft.json",
        calendar_path=None,
        approval_binding_path=None,
        producer_report_path=None,
        producer_sidecar_path=None,
        expected_producer_report_sha256=None,
        known_at=datetime(2026, 9, 5, tzinfo=UTC),
        generated_at=datetime(2026, 9, 5, tzinfo=UTC),
    )

    assert report["status"] == "INDETERMINATE"
    assert report["gate_approved"] is False
    assert report["training_enabled"] is False
    reasons = report["result"]["reasons"]
    assert any("calendar" in reason for reason in reasons)
    assert any("producer clean-file inventory" in reason for reason in reasons)


def test_write_report_is_checksum_published_and_never_overwritten(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "_ROOT", tmp_path)
    output = tmp_path / "build" / "reference-readiness"

    report_path, sidecar_path = runner.write_report(
        {"status": "INDETERMINATE", "gate_approved": False}, output
    )

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["report.json"]["sha256"]
    assert json.loads(report_path.read_text(encoding="utf-8"))["gate_approved"] is False
    with pytest.raises(ValueError, match="must not already exist"):
        runner.write_report({}, output)


def test_cli_returns_two_for_indeterminate_and_never_claims_gate_approval(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        runner,
        "build_report",
        lambda **_kwargs: {"status": "INDETERMINATE", "gate_approved": False},
    )
    output = runner._ROOT / "build" / "unused-test-output"
    monkeypatch.setattr(
        runner,
        "write_report",
        lambda _report, _output: (output / "report.json", output / "report.sha256.json"),
    )

    code = runner.main(
        [
            "--known-at",
            "2026-09-05T00:00:00Z",
            "--output-dir",
            str(output),
        ]
    )

    assert code == 2
    assert json.loads(capsys.readouterr().out)["gate_approved"] is False


def test_cli_requires_utc_cutoffs(runner: ModuleType) -> None:
    with pytest.raises(ValueError, match="UTC"):
        runner._utc("2026-09-05T12:00:00", "known_at")
