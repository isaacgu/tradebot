"""Triage is tied to original evidence, never a relaxed quality threshold."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from tradebot.data.acquisition_probe import (
    SourceTick,
    analyse_chunk,
    canonical_tick_bytes,
    compare_repeat_fetches,
    parse_plan,
    summarise_dataset,
)
from tradebot.data.admission import (
    AdmissionEvidenceError,
    _plan_report,
    canonical_json,
    prepare_admission,
)


def _write(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json(value)
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _seal_report(path: Path, report: dict[str, Any]) -> str:
    digest = _write(path, report)
    path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n")
    return digest


def _seal_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    unsigned = {key: value for key, value in checkpoint.items() if key != "integrity"}
    checkpoint["integrity"] = {
        "algorithm": "sha256",
        "payload_sha256": hashlib.sha256(canonical_json(unsigned)).hexdigest(),
    }
    _write(path, checkpoint)


@pytest.fixture
def corpus(tmp_path: Path) -> dict[str, Any]:
    from decimal import Decimal

    plan_path = tmp_path / "plan.json"
    plan_payload = {
        "schema_version": 1,
        "probe_id": "admission-test",
        "source": "FBS-Demo",
        "symbols": {"EURUSD": "EURUSD"},
        "repeat_fetches": 2,
        "chunk_sessions": 1,
        "purpose": "source_viability_not_gate_evidence",
        "windows": [
            {
                "id": "sample",
                "purpose": "admission contract tests",
                "start_session_date": "2024-09-29",
                "end_session_date_exclusive": "2024-10-03",
            }
        ],
    }
    _write(plan_path, plan_payload)
    plan = parse_plan(plan_payload)
    root = tmp_path / "raw-evidence"
    environment = {
        "probe_version": "fbs-tick-continuity-v1",
        "git_sha": "a" * 40,
        "spec_sha256": "b" * 64,
        "mt5_package_version": "stub",
        "runner_sha256": "c" * 64,
        "analysis_module_sha256": "d" * 64,
        "terminal_build": 1,
        "terminal_name": "stub",
        "terminal_company": "stub",
        "account_server": "FBS-Demo",
        "account_company": "FBS",
        "account_is_demo": True,
    }
    report: dict[str, Any] = {
        "status": "COMPLETE",
        "retrieval_status": "COMPLETE",
        "plan": _plan_report(plan, plan_path.name),
        "probe_version": environment["probe_version"],
        "git_sha": environment["git_sha"],
        "spec_sha256": environment["spec_sha256"],
        "package_version": environment["mt5_package_version"],
        "code_sha256": {
            "runner": environment["runner_sha256"],
            "analysis_module": environment["analysis_module_sha256"],
        },
        "terminal": {"build": 1, "name": "stub", "company": "stub"},
        "account": {"server": "FBS-Demo", "company": "FBS", "is_demo": True},
        "evidence_environment_sha256": hashlib.sha256(canonical_json(environment)).hexdigest(),
        "chunks": {},
    }
    checkpoints: list[Path] = []
    evidence = []
    for index, request in enumerate(plan.chunks):
        instant = int(request.start.timestamp() * 1000) + 1000
        tick = SourceTick(
            time=instant // 1000,
            time_msc=instant,
            bid=Decimal("1.1"),
            ask=Decimal(("1.2", "1.0", "1.1", "1.2")[index]),
            last=Decimal(0),
            volume=0,
            volume_real=Decimal(0),
            flags=6,
        )
        ticks = [] if index == 3 else [tick, tick]
        item = analyse_chunk(request, ticks, bid_flag_mask=2, ask_flag_mask=4)
        evidence.append(item)
        chunk = {
            "chunk_id": request.chunk_id,
            "logical_symbol": request.logical_symbol,
            "broker_symbol": request.broker_symbol,
            "window_id": request.window_id,
            "session_date": request.session_date.isoformat(),
            "start_utc": request.start.isoformat().replace("+00:00", "Z"),
            "end_utc": request.end.isoformat().replace("+00:00", "Z"),
            "semantic_sha256": item.semantic_sha256,
            "metrics": asdict(item.metrics),
        }
        raw_bytes = canonical_tick_bytes(ticks)
        compressed = gzip.compress(raw_bytes, mtime=0)
        relative = f"{plan.plan_hash}/{request.chunk_id}.source-ticks.tsv.gz"
        raw_path = root / relative
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(compressed)
        raw = {
            "path": relative,
            "format": "tradebot-source-ticks-semantic-v1-tsv-gzip",
            "semantic_sha256": item.semantic_sha256,
            "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
            "compressed_bytes": len(compressed),
            "uncompressed_bytes": len(raw_bytes),
        }
        fetches = [
            {
                "repeat": repeat,
                "tick_count": len(ticks),
                "metrics": asdict(item.metrics),
                "semantic_sha256": item.semantic_sha256,
                "shape": {"discarded_before_start": 0, "discarded_after_end": 0},
            }
            for repeat in (1, 2)
        ]
        comparisons = [asdict(compare_repeat_fetches(request, ticks, ticks))]
        report["chunks"][request.chunk_id] = {
            "evidence": chunk,
            "raw": raw,
            "fetches": fetches,
            "repeat_comparisons": comparisons,
        }
        checkpoint = {
            "schema_version": 1,
            "probe_version": environment["probe_version"],
            "plan_hash": plan.plan_hash,
            "source": plan.source,
            "environment": environment,
            "environment_sha256": report["evidence_environment_sha256"],
            "run_id": "stub",
            "completed_at_utc": "2026-09-05T00:00:00Z",
            "chunk": chunk,
            "raw": raw,
            "fetches": fetches,
            "repeat_comparisons": comparisons,
        }
        checkpoint_path = raw_path.with_suffix(".checkpoint.json")
        _seal_checkpoint(checkpoint_path, checkpoint)
        checkpoints.append(checkpoint_path)
    report["dataset"] = asdict(summarise_dataset(plan, evidence))
    candidate = tmp_path / "candidate.json"
    pin = _seal_report(candidate, report)
    # Use the JSON-shaped values that callers actually see.
    report = json.loads(candidate.read_bytes())
    return {
        "candidate": candidate,
        "expected_candidate_sha256": pin,
        "plan_path": plan_path,
        "work_root": root,
        "report": report,
        "checkpoints": checkpoints,
    }


def _run(corpus: dict[str, Any]) -> dict[str, Any]:
    return prepare_admission(
        **{
            key: corpus[key]
            for key in (
                "candidate",
                "expected_candidate_sha256",
                "plan_path",
                "work_root",
            )
        }
    )


def test_quotes_and_emptiness_are_quarantined_without_relaxable_threshold(
    corpus: dict[str, Any],
) -> None:
    result = _run(corpus)
    assert result["summary"]["quarantined_partitions"] == 3
    assert result["summary"]["qa_only_partitions"] == 1
    assert result["summary"]["primary_defects"]["crossed_quotes"] == 2
    assert result["summary"]["primary_defects"]["locked_quotes"] == 2
    assert result["summary"]["all_fetch_defects"]["crossed_quotes"] == 4
    assert result["summary"]["exact_adjacent_duplicates_retained"] == 3
    assert result["partitions"][0]["status"] == "QA_ONLY"
    assert result["partitions"][1]["reasons"] == ["crossed_quotes"]
    assert result["partitions"][2]["reasons"] == ["locked_quotes"]
    assert "EMPTY_SESSION" in result["partitions"][3]["reasons"][0]
    assert result["eligibility"] == {"strategy": False, "execution": False, "gate_approval": False}
    assert all(not row["eligible_for_strategy"] for row in result["partitions"])
    assert result["verification_scope"]["raw_semantic_metrics_recomputed"] is False
    assert result["operating_constraint"]["training_and_refinement"] == "DEMO_ONLY"


def test_forged_report_and_sidecar_cannot_replace_independent_pin(corpus: dict[str, Any]) -> None:
    corpus["report"]["dataset"]["crossed_quotes"] = 0
    _seal_report(corpus["candidate"], corpus["report"])
    with pytest.raises(AdmissionEvidenceError, match="independently pinned"):
        _run(corpus)


def test_candidate_must_be_complete(corpus: dict[str, Any]) -> None:
    corpus["report"]["status"] = "PARTIAL"
    corpus["expected_candidate_sha256"] = _seal_report(corpus["candidate"], corpus["report"])
    with pytest.raises(AdmissionEvidenceError, match="incomplete"):
        _run(corpus)


@pytest.mark.parametrize("target", ["checkpoint", "raw", "sidecar"])
def test_missing_evidence_never_produces_a_manifest(corpus: dict[str, Any], target: str) -> None:
    if target == "checkpoint":
        path = corpus["checkpoints"][0]
    elif target == "raw":
        first = next(iter(corpus["report"]["chunks"].values()))
        path = corpus["work_root"] / first["raw"]["path"]
    else:
        path = corpus["candidate"].with_suffix(".json.sha256")
    path.unlink()
    with pytest.raises(OSError):
        _run(corpus)


def test_raw_tampering_is_detected_without_a_semantic_rescan(corpus: dict[str, Any]) -> None:
    first = next(iter(corpus["report"]["chunks"].values()))
    path = corpus["work_root"] / first["raw"]["path"]
    encoded = bytearray(path.read_bytes())
    encoded[-1] ^= 1
    path.write_bytes(encoded)
    with pytest.raises(AdmissionEvidenceError, match="raw hash mismatch"):
        _run(corpus)


@pytest.mark.parametrize("reseal", [False, True])
def test_checkpoint_forgery_cannot_change_recorded_metrics(
    corpus: dict[str, Any], reseal: bool
) -> None:
    path = corpus["checkpoints"][1]
    checkpoint = json.loads(path.read_bytes())
    checkpoint["chunk"]["metrics"]["crossed_quotes"] = 0
    if reseal:
        _seal_checkpoint(path, checkpoint)
    else:
        _write(path, checkpoint)
    with pytest.raises(AdmissionEvidenceError, match="checkpoint"):
        _run(corpus)


def test_wrong_checkpoint_environment_is_rejected(corpus: dict[str, Any]) -> None:
    path = corpus["checkpoints"][0]
    checkpoint = json.loads(path.read_bytes())
    checkpoint["environment"]["account_server"] = "Other-Demo"
    _seal_checkpoint(path, checkpoint)
    with pytest.raises(AdmissionEvidenceError, match="checkpoint identity"):
        _run(corpus)


@pytest.mark.parametrize("mutation", ["missing_chunk", "dataset_hash", "count"])
def test_internal_identity_must_reconcile_even_with_supplied_pin(
    corpus: dict[str, Any], mutation: str
) -> None:
    report = corpus["report"]
    if mutation == "missing_chunk":
        report["chunks"].pop(next(iter(report["chunks"])))
    elif mutation == "dataset_hash":
        report["dataset"]["dataset_sha256"] = "f" * 64
    else:
        report["dataset"]["total_ticks"] += 1
    corpus["expected_candidate_sha256"] = _seal_report(corpus["candidate"], report)
    with pytest.raises(AdmissionEvidenceError, match=r"coverage|summary"):
        _run(corpus)


def test_duplicate_json_keys_are_not_last_value_wins(corpus: dict[str, Any]) -> None:
    candidate = corpus["candidate"]
    encoded = candidate.read_bytes().replace(b'{"account":', b'{"status":"PARTIAL","account":', 1)
    candidate.write_bytes(encoded)
    with pytest.raises(AdmissionEvidenceError, match="duplicate JSON key"):
        _run(corpus)


def test_no_inputs_are_changed_and_output_is_deterministic(corpus: dict[str, Any]) -> None:
    paths = [path for path in corpus["candidate"].parent.rglob("*") if path.is_file()]
    before = {path: path.read_bytes() for path in paths}
    assert _run(corpus) == _run(corpus)
    assert before == {path: path.read_bytes() for path in paths}


@pytest.mark.parametrize("target", ["raw", "checkpoint", "sidecar"])
def test_earlier_evidence_changing_during_later_partition_is_rejected(
    corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    from tradebot.data import admission

    first = next(iter(corpus["report"]["chunks"].values()))
    path = {
        "raw": corpus["work_root"] / first["raw"]["path"],
        "checkpoint": corpus["checkpoints"][0],
        "sidecar": corpus["candidate"].with_suffix(".json.sha256"),
    }[target]
    original_partition = admission._partition

    def mutate_earlier_file(**kwargs: Any) -> object:
        if kwargs["request"].index_in_window == 1:
            path.write_bytes(path.read_bytes() + b"\n")
        return original_partition(**kwargs)

    monkeypatch.setattr(admission, "_partition", mutate_earlier_file)
    with pytest.raises(AdmissionEvidenceError, match="evidence changed during audit"):
        _run(corpus)


def test_final_rehash_detects_changed_bytes_even_if_file_state_appears_unchanged(
    corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tradebot.data import admission

    first = next(iter(corpus["report"]["chunks"].values()))
    raw = corpus["work_root"] / first["raw"]["path"]
    original_state = admission._state
    recorded_state = original_state(raw)
    original_partition = admission._partition

    def unchanged_raw_state(path: Path) -> tuple[int, int, int, int, int]:
        return recorded_state if path == raw else original_state(path)

    def mutate_earlier_file(**kwargs: Any) -> object:
        if kwargs["request"].index_in_window == 1:
            changed = bytearray(raw.read_bytes())
            changed[-1] ^= 1
            raw.write_bytes(changed)
        return original_partition(**kwargs)

    monkeypatch.setattr(admission, "_state", unchanged_raw_state)
    monkeypatch.setattr(admission, "_partition", mutate_earlier_file)
    with pytest.raises(AdmissionEvidenceError, match="evidence hash changed during audit"):
        _run(corpus)


def test_final_rehash_checks_the_whole_set_again_after_its_last_file(
    corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tradebot.data import admission

    rows = list(corpus["report"]["chunks"].values())
    first_raw = corpus["work_root"] / rows[0]["raw"]["path"]
    last_raw = corpus["work_root"] / rows[-1]["raw"]["path"]
    original_hash = admission._hash_file

    def mutate_after_last_rehash(path: Path, observed: Any = None) -> tuple[str, int]:
        result = original_hash(path, observed)
        if path == last_raw and observed is None:
            first_raw.write_bytes(first_raw.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(admission, "_hash_file", mutate_after_last_rehash)
    with pytest.raises(AdmissionEvidenceError, match="evidence changed during audit"):
        _run(corpus)
