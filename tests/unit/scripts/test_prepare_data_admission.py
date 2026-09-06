"""The offline CLI cannot publish acceptance or overwrite collected evidence."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

corpus = importlib.import_module("tests.unit.data.test_admission").corpus


def _argv(evidence: dict[str, Any], output: Path) -> list[str]:
    return [
        "--candidate",
        str(evidence["candidate"]),
        "--expected-candidate-sha256",
        evidence["expected_candidate_sha256"],
        "--plan",
        str(evidence["plan_path"]),
        "--work-dir",
        str(evidence["work_root"]),
        "--output",
        str(output),
    ]


def test_cli_publishes_and_refuses_to_replace(corpus: dict[str, Any], tmp_path: Path) -> None:
    module = importlib.import_module("scripts.prepare_data_admission")
    output = tmp_path / "admission.json"
    assert module.main(_argv(corpus, output)) == 0
    report = json.loads(output.read_bytes())
    assert report["eligibility"]["strategy"] is False
    assert output.with_suffix(".json.sha256").is_file()
    encoded = output.read_bytes()
    with pytest.raises(SystemExit) as raised:
        module.main(_argv(corpus, output))
    assert raised.value.code == 2
    assert output.read_bytes() == encoded


def test_cli_failure_has_no_partial_manifest(corpus: dict[str, Any], tmp_path: Path) -> None:
    module = importlib.import_module("scripts.prepare_data_admission")
    output = tmp_path / "admission.json"
    corpus["checkpoints"][0].unlink()
    with pytest.raises(SystemExit) as raised:
        module.main(_argv(corpus, output))
    assert raised.value.code == 2
    assert not output.exists()
    assert not output.with_suffix(".json.sha256").exists()


def test_cli_will_not_write_into_acquisition_tree(corpus: dict[str, Any]) -> None:
    module = importlib.import_module("scripts.prepare_data_admission")
    output = corpus["work_root"] / "admission.json"
    with pytest.raises(SystemExit):
        module.main(_argv(corpus, output))
    assert not output.exists()


@pytest.mark.parametrize("swap_stage", ["during_audit", "during_mkdir"])
def test_cli_rechecks_parent_before_publishing_into_a_swapped_directory(
    corpus: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swap_stage: str,
) -> None:
    module = importlib.import_module("scripts.prepare_data_admission")
    parent = tmp_path / "safe-output"
    parent.mkdir()
    output = parent / "admission.json"

    def swap_parent() -> None:
        parent.rmdir()
        try:
            parent.symlink_to(corpus["work_root"], target_is_directory=True)
        except OSError:
            pytest.skip("This host does not permit directory symlinks")

    if swap_stage == "during_audit":
        original = module.prepare_admission

        def swap_after_audit(**kwargs: Any) -> dict[str, Any]:
            report: dict[str, Any] = original(**kwargs)
            swap_parent()
            return report

        monkeypatch.setattr(module, "prepare_admission", swap_after_audit)
    else:
        original_mkdir = Path.mkdir

        def swap_during_mkdir(path: Path, *args: Any, **kwargs: Any) -> None:
            if path == parent:
                swap_parent()
            else:
                original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", swap_during_mkdir)

    with pytest.raises(SystemExit) as rejected:
        module.main(_argv(corpus, output))
    assert rejected.value.code == 2
    assert not (corpus["work_root"] / "admission.json").exists()
    assert not (corpus["work_root"] / "admission.json.sha256").exists()


def test_cli_rejects_a_checksum_alias_before_writing_the_manifest(
    corpus: dict[str, Any], tmp_path: Path
) -> None:
    module = importlib.import_module("scripts.prepare_data_admission")
    output = tmp_path / "admission.json"
    checksum = output.with_suffix(".json.sha256")
    target = corpus["work_root"] / "missing-checksum"
    try:
        checksum.symlink_to(target)
    except OSError:
        pytest.skip("This host does not permit file symlinks")
    with pytest.raises(SystemExit) as rejected:
        module.main(_argv(corpus, output))
    assert rejected.value.code == 2
    assert not output.exists()
    assert not target.exists()
