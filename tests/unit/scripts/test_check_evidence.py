"""Gate evidence validation fails closed without turning a machine check into approval."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def checker() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "check_evidence.py"
    spec = importlib.util.spec_from_file_location("check_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def evidence(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    original = Path(__file__).resolve().parents[3] / "docs" / "SPEC.md"
    (tmp_path / "docs" / "SPEC.md").write_bytes(original.read_bytes())
    (tmp_path / "report.json").write_text("{}\n")
    (tmp_path / "metrics.prom").write_text("tradebot_test 1\n")
    digest = hashlib.sha256((tmp_path / "report.json").read_bytes()).hexdigest()
    metrics = hashlib.sha256((tmp_path / "metrics.prom").read_bytes()).hexdigest()
    ci = f"https://github.com/example/bot/actions/runs/123 commit `{'a' * 40}`"
    text = f"""# Gate 0 evidence

Frozen SPEC judged against: `docs/SPEC.md` v1.0, SHA-256
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`

## Evidence categories
| # | Category | Status | Content |
|---|---|---|---|
| 1 | CI run on a committed git SHA | PROVIDED | {ci} |
| 2 | Report / manifest artifact hashes | PROVIDED | `report.json` SHA-256 `{digest}` |
| 3 | Observability evidence | PROVIDED | `metrics.prom` SHA-256 `{metrics}` |
| 4 | Independent reviewer sign-off | PROVIDED | Human Reviewer, 2026-09-04 |
| 5 | Principal sign-off | PROVIDED | Human Principal, 2026-09-04 |

## Inherited obligations from Gate N-1
None — first gate.

## Sign-off
Independent human reviewer: Human Reviewer  date: 2026-09-04
Principal: Human Principal  date: 2026-09-04
"""
    path = tmp_path / "gate0_evidence.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_pack_has_machine_result_but_never_authenticates_approval(
    checker: ModuleType,
    evidence: Path,
) -> None:
    result = checker.check_evidence(evidence, repo_root=evidence.parent)
    assert result.machine_checks_passed
    assert not result.human_approvals_authenticated
    assert "approval" in result.limitation.lower()


@pytest.mark.parametrize("replacement", ["N/A", "", "PROVIDED / FAILED", "provided", "`PROVIDED`"])
def test_status_is_an_exact_token(checker: ModuleType, evidence: Path, replacement: str) -> None:
    evidence.write_text(
        evidence.read_text().replace(
            "| 2 | Report / manifest artifact hashes | PROVIDED |",
            f"| 2 | Report / manifest artifact hashes | {replacement} |",
        )
    )
    assert not checker.check_evidence(evidence, repo_root=evidence.parent).machine_checks_passed


@pytest.mark.parametrize("category", [1, 2, 4, 5])
def test_mandatory_categories_can_never_defer(
    checker: ModuleType, evidence: Path, category: int
) -> None:
    lines = evidence.read_text().splitlines()
    evidence.write_text(
        "\n".join(
            line.replace("| PROVIDED |", "| DEFERRED-BY-PHASE |")
            if line.startswith(f"| {category} |")
            else line
            for line in lines
        )
    )
    result = checker.check_evidence(evidence, repo_root=evidence.parent)
    assert any("never" in item for item in result.errors)


def test_missing_and_duplicate_categories_fail(checker: ModuleType, evidence: Path) -> None:
    evidence.write_text(evidence.read_text().replace("| 5 | Principal", "| 4 | Principal"))
    result = checker.check_evidence(evidence, repo_root=evidence.parent)
    assert any("exactly" in item for item in result.errors)


def test_hash_mismatch_and_traversal_fail(checker: ModuleType, evidence: Path) -> None:
    (evidence.parent / "report.json").write_text('{"modified": true}')
    assert any(
        "hash mismatch" in item
        for item in checker.check_evidence(evidence, repo_root=evidence.parent).errors
    )
    evidence.write_text(evidence.read_text().replace("`report.json`", "`../report.json`"))
    assert any(
        "outside" in item
        for item in checker.check_evidence(evidence, repo_root=evidence.parent).errors
    )


def test_changed_spec_fails_even_when_evidence_claims_new_hash(
    checker: ModuleType, evidence: Path
) -> None:
    spec = evidence.parent / "docs" / "SPEC.md"
    spec.write_text("changed specification")
    digest = hashlib.sha256(spec.read_bytes()).hexdigest()
    evidence.write_text(evidence.read_text().replace(checker.FROZEN_SPEC_SHA256, digest))
    assert any(
        "frozen" in item
        for item in checker.check_evidence(evidence, repo_root=evidence.parent).errors
    )


def test_signoffs_cannot_be_an_agent_or_same_person(checker: ModuleType, evidence: Path) -> None:
    original = evidence.read_text()
    evidence.write_text(original.replace("Human Reviewer", "Codex agent"))
    assert any(
        "human" in item
        for item in checker.check_evidence(evidence, repo_root=evidence.parent).errors
    )
    evidence.write_text(original.replace("Human Reviewer", "Human Principal"))
    assert any(
        "independent" in item
        for item in checker.check_evidence(evidence, repo_root=evidence.parent).errors
    )


def test_inherited_obligation_must_be_carried_verbatim_and_discharged(
    checker: ModuleType,
    evidence: Path,
) -> None:
    previous = evidence.parent / "previous.md"
    previous.write_text(
        evidence.read_text()
        + """
## Obligations this gate creates
| id | Category | Deferred at | Due at | Status |
|---|---|---|---|---|
| G0-2 | Gate evidence checker (§10.6, §13 P1) | Gate 0 | Gate 1 | open |
"""
    )
    evidence.write_text(evidence.read_text().replace("# Gate 0", "# Gate 1"))
    result = checker.check_evidence(evidence, repo_root=evidence.parent, previous=previous)
    assert any("G0-2" in item for item in result.errors)
    evidence.write_text(
        evidence.read_text().replace(
            "None — first gate.",
            """
| id | Category | Deferred at | Due at | Status | Evidence |
|---|---|---|---|---|---|
| G0-2 | Gate evidence checker (§10.6, §13 P1) | Gate 0 | Gate 1 | open | |
""",
        )
    )
    result = checker.check_evidence(evidence, repo_root=evidence.parent, previous=previous)
    assert any("due" in item for item in result.errors)
    (evidence.parent / "scripts").mkdir()
    checker_path = evidence.parent / "scripts" / "check_evidence.py"
    assert checker.__file__ is not None
    checker_path.write_bytes(Path(checker.__file__).read_bytes())
    digest = hashlib.sha256(checker_path.read_bytes()).hexdigest()
    evidence.write_text(
        evidence.read_text().replace(
            "| Gate 1 | open | |",
            f"| Gate 1 | PROVIDED | `scripts/check_evidence.py` SHA-256 `{digest}` |",
        )
    )
    result = checker.check_evidence(evidence, repo_root=evidence.parent, previous=previous)
    assert result.machine_checks_passed, result.errors


def test_ci_requires_immutable_run_and_commit(checker: ModuleType, evidence: Path) -> None:
    evidence.write_text(
        evidence.read_text().replace("actions/runs/123", "actions/workflows/ci.yml")
    )
    assert any(
        "CI" in item for item in checker.check_evidence(evidence, repo_root=evidence.parent).errors
    )


def test_missing_previous_pack_and_failed_evidence_are_not_ready(
    checker: ModuleType, evidence: Path
) -> None:
    evidence.write_text(
        evidence.read_text()
        .replace("# Gate 0", "# Gate 1")
        .replace(
            "| 2 | Report / manifest artifact hashes | PROVIDED |",
            "| 2 | Report / manifest artifact hashes | FAILED |",
        )
    )
    result = checker.check_evidence(evidence, repo_root=evidence.parent)
    assert not result.machine_checks_passed
    assert any("previous" in item for item in result.errors)


def test_observability_cannot_replace_exposition_with_a_screenshot(
    checker: ModuleType,
    evidence: Path,
) -> None:
    metrics = evidence.parent / "metrics.prom"
    metrics.rename(evidence.parent / "metrics.png")
    evidence.write_text(evidence.read_text().replace("`metrics.prom`", "`metrics.png`"))
    assert any(
        "exposition" in item
        for item in checker.check_evidence(evidence, repo_root=evidence.parent).errors
    )


def test_observability_existing_artifacts_cannot_be_deferred(
    checker: ModuleType,
    evidence: Path,
) -> None:
    evidence.write_text(
        evidence.read_text().replace(
            "| 3 | Observability evidence | PROVIDED |",
            "| 3 | Observability evidence | DEFERRED-BY-PHASE | owner P1 due Gate 1 §4.6;",
        )
    )
    assert any(
        "no-downgrade" in item
        for item in checker.check_evidence(evidence, repo_root=evidence.parent).errors
    )


def test_known_screenshot_obligation_cannot_be_discharged_with_json(
    checker: ModuleType,
    evidence: Path,
) -> None:
    digest = hashlib.sha256((evidence.parent / "report.json").read_bytes()).hexdigest()
    row = f"| G0-1 | Screenshot | Gate 0 | Gate 1 | PROVIDED | `report.json` SHA-256 `{digest}` |"
    evidence.write_text(evidence.read_text() + "\n## Obligations this gate creates\n" + row)
    assert any(
        "rendered image" in item
        for item in checker.check_evidence(evidence, repo_root=evidence.parent).errors
    )


def test_checker_cli_reports_machine_result_without_issuing_approval(
    checker: ModuleType,
    evidence: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert checker.main([str(evidence), "--repo-root", str(evidence.parent)]) == 0
    assert '"human_approvals_authenticated": false' in capsys.readouterr().out
    assert checker.main([str(evidence.parent / "missing.md")]) == 1
    assert '"machine_checks_passed": false' in capsys.readouterr().out
