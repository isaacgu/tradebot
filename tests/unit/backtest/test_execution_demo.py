"""Known-answer integration and honest evidence/publication boundaries."""

import hashlib
import json
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from tradebot.backtest import execution_demo as demo
from tradebot.backtest.costs import (
    ConversionQuote,
    CostBreakdown,
    RoundTripInput,
    summarize_round_trip,
)
from tradebot.research.registry import Registry, RegistryError
from tradebot.research.report import canonical_bytes


def _counts(registry: Registry, experiment: str) -> dict[str, object]:
    counts = registry.audit(experiment)["counts"]
    assert isinstance(counts, dict)
    return counts


def test_fixed_report_contains_reconciled_long_short_profit_loss() -> None:
    report = demo.build_report("a" * 64)
    cases = report["cases"]
    assert isinstance(cases, list)
    expected = {
        "long-profit": ("6.90000", "124.20000"),
        "long-loss": ("-13.10000", "-236.06200"),
        "short-profit": ("5.90000", "106.20000"),
        "short-loss": ("-14.10000", "-254.08200"),
    }
    for case in cases:
        amounts = case["accounting"]
        quote, account = expected[case["name"]]
        assert Decimal(amounts["net_pnl_quote"]) == Decimal(quote)
        assert Decimal(amounts["net_pnl_account"]) == Decimal(account)
        assert Decimal(amounts["gross_pnl_account"]) - Decimal(
            amounts["total_cost_account"]
        ) == Decimal(account)
        assert case["entry"]["tick_index"] == 2
        assert case["exit"]["tick_index"] == 2
    assert report["simulated_orders"] == report["simulated_fills"] == 8
    assert report["broker_orders"] == 0
    assert report["source_kind"] == "synthetic"
    assert report["execution_enabled"] is False
    assert report["costs_modelled"] is False
    assert report["synthetic_pnl_reported"] is True
    assert report["economic_evaluation"] == "NOT_PERFORMED"
    assert report["gate_approvals_claimed"] == []


def test_report_bytes_ignore_ambient_decimal_context() -> None:
    expected = canonical_bytes(demo.build_report("a" * 64))
    with localcontext() as context:
        context.prec = 2
        context.capitals = 0
        actual = canonical_bytes(demo.build_report("a" * 64))
    assert actual == expected


def test_invalid_run_identity_rejected() -> None:
    with pytest.raises(ValueError):
        demo.build_report("unattributed")


def test_sequential_attempts_publish_identical_immutable_report(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    experiment = registry.register(demo.synthetic_declaration())
    first = demo.run_synthetic_attempt(registry, experiment, "first", output_root=tmp_path)
    second = demo.run_synthetic_attempt(registry, experiment, "second", output_root=tmp_path)
    assert first == second
    path = tmp_path / first["report.json"] / "report.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == first["report.json"]
    assert json.loads(path.read_bytes())["experiment_id"] == experiment
    assert not (tmp_path / "latest.json").exists()
    audit = registry.audit(experiment)
    assert audit["counts"] == {"started": 2, "completed": 2, "failed": 0, "incomplete": 0}
    with pytest.raises(RegistryError):
        demo.run_synthetic_attempt(registry, experiment, "first", output_root=tmp_path)


def test_mismatched_declaration_is_retained_as_failed(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    declaration = demo.synthetic_declaration()
    declaration["name"] = "different-experiment"
    experiment = registry.register(declaration)
    with pytest.raises(ValueError, match="declaration"):
        demo.run_synthetic_attempt(registry, experiment, "mismatch", output_root=tmp_path)
    assert registry.audit(experiment)["counts"] == {
        "started": 1,
        "completed": 0,
        "failed": 1,
        "incomplete": 0,
    }


def test_corrupted_existing_artifact_not_overwritten(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    experiment = registry.register(demo.synthetic_declaration())
    artifacts = demo.run_synthetic_attempt(registry, experiment, "first", output_root=tmp_path)
    path = tmp_path / artifacts["report.json"] / "report.json"
    path.write_bytes(b"corrupted")
    with pytest.raises(FileExistsError):
        demo.run_synthetic_attempt(registry, experiment, "retry", output_root=tmp_path)
    assert path.read_bytes() == b"corrupted"
    assert _counts(registry, experiment)["failed"] == 1


def test_runtime_failure_has_started_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    experiment = registry.register(demo.synthetic_declaration())

    def broken_report(*args: object, **kwargs: object) -> dict[str, object]:
        assert _counts(registry, experiment)["incomplete"] == 1
        raise RuntimeError("injected fixture failure")

    monkeypatch.setattr(demo, "build_report", broken_report)
    with pytest.raises(RuntimeError, match="injected fixture"):
        demo.run_synthetic_attempt(registry, experiment, "failure", output_root=tmp_path)
    assert _counts(registry, experiment)["failed"] == 1


def test_cli_rejects_market_data_option() -> None:
    with pytest.raises(SystemExit) as result:
        demo.main(["--market-data", "not-allowed"])
    assert result.value.code == 2


def test_cli_offline_run_and_attribution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert demo.main(["--output-root", str(tmp_path), "--attempt-id", "cli"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["evidence_class"] == "ENGINEERING_ONLY"
    assert output["audit"]["counts"]["completed"] == 1
    report_hash = output["artifacts"]["report.json"]
    report = json.loads((tmp_path / "artifacts" / report_hash / "report.json").read_bytes())
    assert report["git_sha"] == "UNCOMMITTED"
    assert report["implementation"] == output["audit"]["declaration"]["implementation"]


def test_wrong_git_identity_rejected() -> None:
    with pytest.raises(ValueError, match="git_sha"):
        demo.synthetic_declaration("HEAD")


def test_accounted_fill_mismatch_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    original = summarize_round_trip

    def wrong_amounts(
        trade: RoundTripInput, *, conversion: ConversionQuote | None
    ) -> CostBreakdown:
        result = original(trade, conversion=conversion)
        return replace(result, entry_fill_price=Decimal("9"))

    monkeypatch.setattr(demo, "summarize_round_trip", wrong_amounts)
    with pytest.raises(RuntimeError, match="accounted prices"):
        demo.build_report("a" * 64)


def test_consumed_fixture_mismatch_retained_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    experiment = registry.register(demo.synthetic_declaration())
    original = demo.build_report

    def wrong_fixture(experiment_id: str, *, git_sha: str) -> dict[str, object]:
        report = original(experiment_id, git_sha=git_sha)
        report["fixture_sha256"] = "0" * 64
        return report

    monkeypatch.setattr(demo, "build_report", wrong_fixture)
    with pytest.raises(RuntimeError, match="consumed fixture"):
        demo.run_synthetic_attempt(registry, experiment, "wrong-fixture", output_root=tmp_path)
    assert _counts(registry, experiment)["failed"] == 1


def test_no_fill_cannot_produce_completed_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo, "match_market_order", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="no fill"):
        demo.build_report("a" * 64)


def test_implementation_drift_retained_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = Registry(tmp_path / "registry.sqlite")
    experiment = registry.register(demo.synthetic_declaration())
    original = demo.synthetic_declaration
    calls = 0

    def drift(git_sha: str = "UNCOMMITTED") -> dict[str, object]:
        nonlocal calls
        calls += 1
        result = original(git_sha)
        if calls > 1:
            result["name"] = "changed-during-attempt"
        return result

    monkeypatch.setattr(demo, "synthetic_declaration", drift)
    with pytest.raises(RuntimeError, match="changed during"):
        demo.run_synthetic_attempt(registry, experiment, "drift", output_root=tmp_path)
    assert _counts(registry, experiment)["failed"] == 1
