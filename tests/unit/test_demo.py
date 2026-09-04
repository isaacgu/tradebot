from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import tradebot.demo as demo
from tradebot.demo import Gate0Manifest, Gate0Run, HelloStrategy

ROOT = Path(__file__).parents[2]
BACKTEST_CONFIG = ROOT / "configs" / "env" / "backtest.yaml"
PAPER_CONFIG = ROOT / "configs" / "env" / "paper.yaml"


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["git"], returncode=0, stdout=stdout, stderr="")


def _set_cli_args(monkeypatch: pytest.MonkeyPatch, output: Path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tradebot-demo",
            "--backtest-config",
            str(BACKTEST_CONFIG),
            "--paper-config",
            str(PAPER_CONFIG),
            "--output",
            str(output),
        ],
    )


@pytest.mark.parametrize("variable", ["GIT_SHA", "GITHUB_SHA"])
def test_resolve_git_sha_prefers_supplied_environment(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setenv(variable, "ci-provenance")
    run = Mock(side_effect=AssertionError("git must not run when CI supplies a SHA"))
    monkeypatch.setattr("tradebot.demo.subprocess.run", run)

    assert demo.resolve_git_sha() == "ci-provenance"
    run.assert_not_called()


def test_resolve_git_sha_marks_a_dirty_tree_uncommitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    run = Mock(return_value=_completed("?? local-file\n"))
    monkeypatch.setattr("tradebot.demo.subprocess.run", run)

    assert demo.resolve_git_sha() == "UNCOMMITTED"
    run.assert_called_once()


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("A" * 40, "a" * 40),
        ("0" * 64, "0" * 64),
        ("0" * 39, "UNCOMMITTED"),
        ("g" * 40, "UNCOMMITTED"),
    ],
)
def test_resolve_git_sha_validates_clean_head(
    monkeypatch: pytest.MonkeyPatch, candidate: str, expected: str
) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    run = Mock(side_effect=[_completed(""), _completed(f"{candidate}\n")])
    monkeypatch.setattr("tradebot.demo.subprocess.run", run)

    assert demo.resolve_git_sha() == expected
    assert run.call_count == 2


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("git"), subprocess.TimeoutExpired(cmd="git", timeout=2)],
)
def test_resolve_git_sha_handles_git_failures(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setattr("tradebot.demo.subprocess.run", Mock(side_effect=error))

    assert demo.resolve_git_sha() == "UNCOMMITTED"


def test_parser_defaults_match_repository_layout() -> None:
    args = demo._parser().parse_args([])

    assert args.backtest_config == Path("configs/env/backtest.yaml")
    assert args.paper_config == Path("configs/env/paper.yaml")
    assert args.output == Path("build/gate0/demo-manifest.json")


def test_main_writes_manifest_and_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "gate0" / "manifest.json"
    _set_cli_args(monkeypatch, output)
    monkeypatch.setenv("GIT_SHA", "f" * 40)

    demo.main()

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["code_parity"] is True
    assert {result["git_sha"] for result in manifest["results"]} == {"f" * 40}
    assert output.with_suffix(".json.sha256").is_file()
    for mode in ("backtest", "paper"):
        raw_path, digest_path = demo.metrics_paths(output, mode)
        assert raw_path.is_file(), "the raw exposition is the CI-uploaded artifact"
        assert digest_path.is_file(), "the canonical digest is the hashed evidence"
    logs = capsys.readouterr().out
    assert "gate0_demo_started" in logs
    assert "gate0_demo_finished" in logs


def test_main_exits_when_traces_differ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "manifest.json"
    _set_cli_args(monkeypatch, output)
    monkeypatch.setenv("GIT_SHA", "e" * 40)

    def nonparity_run(backtest_path: Path, paper_path: Path, *, git_sha: str) -> Gate0Run:
        del backtest_path, paper_path, git_sha
        return Gate0Run(
            manifest=Gate0Manifest(
                schema_version=demo.MANIFEST_SCHEMA_VERSION,
                evidence_class="smoke-demo-only-not-performance-evidence",
                dataset_id=demo.SYNTHETIC_DATASET_ID,
                random_seed=demo.SYNTHETIC_SEED,
                fixture_base_ts=demo.SYNTHETIC_BASE_TS.isoformat(),
                trace_fields=list(demo.TRACE_FIELDS),
                costs_modelled=False,
                pnl_reported=False,
                execution_enabled=False,
                availability_parity_demonstrated=False,
                code_parity=False,
                results=[],
            ),
            metrics=(),
        )

    monkeypatch.setattr(demo, "build_gate0_manifest", nonparity_run)

    with pytest.raises(SystemExit, match="Gate-0 logical traces differ"):
        demo.main()
    assert json.loads(output.read_text(encoding="utf-8"))["code_parity"] is False


def test_manifest_rejects_configs_in_the_wrong_order() -> None:
    with pytest.raises(ValueError, match="backtest and paper configs"):
        demo.build_gate0_manifest(PAPER_CONFIG, BACKTEST_CONFIG, git_sha="test-sha")


def test_hello_strategy_checkpoint_contract() -> None:
    strategy = HelloStrategy("SYNTH_GBP_USD")

    assert strategy.state() == {}
    strategy.restore({})
    with pytest.raises(ValueError, match="no restorable state"):
        strategy.restore({"unexpected": True})
