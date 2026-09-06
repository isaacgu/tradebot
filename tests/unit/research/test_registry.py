"""Adversarial engineering preregistration/lifecycle evidence checks."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from tradebot.research.registry import Registry, RegistryError
from tradebot.research.report import canonical_bytes


def declaration() -> dict[str, object]:
    return {
        "evidence_class": "ENGINEERING_ONLY",
        "hypothesis": "synthetic causal plumbing",
        "parameters": {"lookbacks": [8, 16, 32, 64]},
    }


def registered(tmp_path: Path) -> tuple[Registry, str]:
    registry = Registry(tmp_path / "registry.sqlite3")
    return registry, registry.register(declaration())


def test_content_identity_and_idempotent_registration(tmp_path: Path) -> None:
    registry, experiment_id = registered(tmp_path)
    assert experiment_id == hashlib.sha256(canonical_bytes(declaration())).hexdigest()
    before = registry.audit(experiment_id)
    assert registry.register(dict(reversed(list(declaration().items())))) == experiment_id
    assert registry.audit(experiment_id) == before
    assert before["registry_event_count"] == 1
    assert before["counts"] == {"started": 0, "completed": 0, "failed": 0, "incomplete": 0}


def test_declarations_and_returned_audits_are_detached(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite3")
    original = declaration()
    experiment_id = registry.register(original)
    original["hypothesis"] = "changed"
    audit = registry.audit(experiment_id)
    cast(dict[str, object], audit["declaration"])["hypothesis"] = "also changed"
    assert registry.audit(experiment_id)["declaration"] == declaration()
    assert registry.register(original) != experiment_id


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"evidence_class": "REAL"},
        {"evidence_class": "ENGINEERING_ONLY", "x": float("nan")},
        {"evidence_class": "ENGINEERING_ONLY", "x": float("inf")},
        {"evidence_class": "ENGINEERING_ONLY", "x": (1, 2)},
        {"evidence_class": "ENGINEERING_ONLY", "x": {1: "ambiguous"}},
        {"evidence_class": "ENGINEERING_ONLY", "x": Path("x")},
    ],
)
def test_malformed_declarations_rejected(tmp_path: Path, value: dict[str, object]) -> None:
    registry = Registry(tmp_path / "registry.sqlite3")
    with pytest.raises(RegistryError):
        registry.register(value)


def test_attempt_requires_preregistration(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite3")
    with pytest.raises(RegistryError, match="preregistered"):
        registry.start_attempt("a" * 64, "attempt-1")
    with pytest.raises(RegistryError, match="unknown"):
        registry.audit("a" * 64)
    with pytest.raises(RegistryError, match="preregistered"):
        registry.finish_attempt("a" * 64, "attempt-1", status="COMPLETED")


@pytest.mark.parametrize("attempt_id", ["", "../escape", "a/b", "a b", "é", "a" * 129])
def test_unsafe_attempt_ids_rejected(tmp_path: Path, attempt_id: str) -> None:
    registry, experiment_id = registered(tmp_path)
    with pytest.raises(RegistryError, match="attempt_id"):
        registry.start_attempt(experiment_id, attempt_id)


@pytest.mark.parametrize("experiment_id", ["", "A" * 64, "x" * 64, "a" * 63])
def test_invalid_experiment_digests_rejected(tmp_path: Path, experiment_id: str) -> None:
    registry = Registry(tmp_path / "registry.sqlite3")
    with pytest.raises(RegistryError, match="experiment_id"):
        registry.start_attempt(experiment_id, "attempt-1")


def test_failed_completed_and_interrupted_attempts_retained(tmp_path: Path) -> None:
    registry, experiment_id = registered(tmp_path)
    registry.start_attempt(experiment_id, "failed")
    registry.finish_attempt(
        experiment_id,
        "failed",
        status="FAILED",
        error="synthetic intentional failure",
        artifacts={"partial.jsonl": "a" * 64},
    )
    registry.start_attempt(experiment_id, "completed")
    registry.finish_attempt(
        experiment_id, "completed", status="COMPLETED", artifacts={"trace.jsonl": "b" * 64}
    )
    registry.start_attempt(experiment_id, "interrupted")
    audit = Registry(registry.path).audit(experiment_id)
    assert audit["counts"] == {"started": 3, "completed": 1, "failed": 1, "incomplete": 1}
    assert audit["attempts"] == [
        {
            "attempt_id": "failed",
            "status": "FAILED",
            "error": "synthetic intentional failure",
            "artifacts": {"partial.jsonl": "a" * 64},
            "metadata": {},
        },
        {
            "attempt_id": "completed",
            "status": "COMPLETED",
            "error": None,
            "artifacts": {"trace.jsonl": "b" * 64},
            "metadata": {},
        },
        {
            "attempt_id": "interrupted",
            "status": "STARTED",
            "error": None,
            "artifacts": {},
            "metadata": {},
        },
    ]
    assert audit["registry_event_count"] == 6


def test_lifecycle_rejections_leave_evidence_unchanged(tmp_path: Path) -> None:
    registry, experiment_id = registered(tmp_path)
    before = registry.audit(experiment_id)
    with pytest.raises(RegistryError, match="STARTED"):
        registry.finish_attempt(experiment_id, "missing", status="COMPLETED")
    assert registry.audit(experiment_id) == before
    registry.start_attempt(experiment_id, "attempt")
    before = registry.audit(experiment_id)
    with pytest.raises(RegistryError, match="already exists"):
        registry.start_attempt(experiment_id, "attempt")
    assert registry.audit(experiment_id) == before
    registry.finish_attempt(experiment_id, "attempt", status="FAILED", error="failure")
    before = registry.audit(experiment_id)
    with pytest.raises(RegistryError, match="STARTED"):
        registry.finish_attempt(experiment_id, "attempt", status="COMPLETED")
    assert registry.audit(experiment_id) == before


@pytest.mark.parametrize(
    ("status", "artifacts", "error"),
    [
        ("STARTED", {}, None),
        ("COMPLETED", {}, "error"),
        ("FAILED", {}, None),
        ("FAILED", {}, "  "),
        ("COMPLETED", {"trace": "A" * 64}, None),
        ("COMPLETED", {"trace": "a" * 63}, None),
        ("COMPLETED", {"../trace": "a" * 64}, None),
    ],
)
def test_invalid_finish_is_not_committed(
    tmp_path: Path,
    status: str,
    artifacts: dict[str, str],
    error: str | None,
) -> None:
    registry, experiment_id = registered(tmp_path)
    registry.start_attempt(experiment_id, "attempt")
    before = registry.audit(experiment_id)
    with pytest.raises(RegistryError):
        registry.finish_attempt(
            experiment_id, "attempt", status=status, artifacts=artifacts, error=error
        )
    assert registry.audit(experiment_id) == before


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE registry_events SET body = '{}' WHERE seq = 1",
        "UPDATE registry_events SET event_sha256 = 'a' WHERE seq = 1",
        "DELETE FROM registry_events WHERE seq = 2",
        "DELETE FROM registry_meta",
        "UPDATE registry_meta SET head_sha256 = 'broken'",
        "PRAGMA user_version = 2",
        "CREATE TABLE unrelated (value TEXT)",
    ],
)
def test_corruption_rejected_by_every_operation(tmp_path: Path, sql: str) -> None:
    registry, experiment_id = registered(tmp_path)
    registry.start_attempt(experiment_id, "attempt")
    with sqlite3.connect(registry.path) as connection:
        connection.execute(sql)
    operations: list[Callable[[], object]] = [
        lambda: registry.audit(experiment_id),
        lambda: registry.register(declaration()),
        lambda: registry.start_attempt(experiment_id, "next"),
        lambda: registry.finish_attempt(experiment_id, "attempt", status="COMPLETED"),
        lambda: Registry(registry.path),
    ]
    for operation in operations:
        with pytest.raises(RegistryError):
            operation()


def test_interior_deleted_event_rejected(tmp_path: Path) -> None:
    registry, experiment_id = registered(tmp_path)
    registry.start_attempt(experiment_id, "attempt")
    registry.finish_attempt(experiment_id, "attempt", status="COMPLETED")
    with sqlite3.connect(registry.path) as connection:
        connection.execute("DELETE FROM registry_events WHERE seq = 2")
    with pytest.raises(RegistryError, match="contiguous"):
        registry.audit(experiment_id)


def test_self_consistent_hash_cannot_bypass_lifecycle_validation(tmp_path: Path) -> None:
    registry, experiment_id = registered(tmp_path)
    registry.start_attempt(experiment_id, "attempt")
    with sqlite3.connect(registry.path) as connection:
        body = connection.execute("SELECT body FROM registry_events WHERE seq = 2").fetchone()[0]
        event = json.loads(body)
        event["operation"] = "FINISH"
        event["payload"] = {"status": "COMPLETED", "artifacts": {}, "error": None}
        encoded = canonical_bytes(event)
        digest = hashlib.sha256(encoded).hexdigest()
        connection.execute(
            "UPDATE registry_events SET body = ?, event_sha256 = ? WHERE seq = 2",
            (encoded.decode(), digest),
        )
        connection.execute("UPDATE registry_meta SET head_sha256 = ?", (digest,))
    with pytest.raises(RegistryError, match="uniquely open"):
        registry.audit(experiment_id)


def test_concurrent_duplicate_start_commits_exactly_once(tmp_path: Path) -> None:
    registry, experiment_id = registered(tmp_path)

    def start() -> str:
        try:
            Registry(registry.path).start_attempt(experiment_id, "same-attempt")
        except RegistryError:
            return "rejected"
        return "started"

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: start(), range(4)))
    assert sorted(results) == ["rejected", "rejected", "rejected", "started"]
    assert registry.audit(experiment_id)["counts"] == {
        "started": 1,
        "completed": 0,
        "failed": 0,
        "incomplete": 1,
    }


def test_failed_transaction_does_not_leave_partial_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, experiment_id = registered(tmp_path)
    before = registry.audit(experiment_id)
    original = Registry._read_state
    calls = 0

    def fail_after_append(connection: sqlite3.Connection) -> dict[str, dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated interruption before transaction commit")
        return original(connection)

    with monkeypatch.context() as scoped:
        scoped.setattr(Registry, "_read_state", staticmethod(fail_after_append))
        with pytest.raises(RuntimeError, match="simulated interruption"):
            registry.start_attempt(experiment_id, "attempt")
    assert registry.audit(experiment_id) == before
    registry.start_attempt(experiment_id, "attempt")


def test_missing_existing_database_is_not_silently_recreated(tmp_path: Path) -> None:
    registry, experiment_id = registered(tmp_path)
    registry.path.rename(tmp_path / "retained.sqlite3")
    with pytest.raises(RegistryError, match="missing"):
        registry.audit(experiment_id)
    assert not registry.path.exists()


def test_malformed_database_rejected(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE not_registry (value TEXT)")
    with pytest.raises(RegistryError, match="schema"):
        Registry(path)


def test_existing_empty_database_is_not_initialised(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    path.touch()
    with pytest.raises(RegistryError, match="schema"):
        Registry(path)


def test_truncated_database_is_rejected(tmp_path: Path) -> None:
    registry, experiment_id = registered(tmp_path)
    registry.path.write_bytes(b"")
    with pytest.raises(RegistryError, match="schema"):
        Registry(registry.path)
    with pytest.raises(RegistryError, match="schema"):
        registry.audit(experiment_id)


def test_start_metadata_survives_failure_and_is_detached(tmp_path: Path) -> None:
    registry, experiment_id = registered(tmp_path)
    metadata: dict[str, object] = {"partition": "lockbox", "runner": "fixed-synthetic-v1"}
    registry.start_attempt(experiment_id, "denied", metadata=metadata)
    metadata["partition"] = "validation"
    expected = {"partition": "lockbox", "runner": "fixed-synthetic-v1"}
    audit = registry.audit(experiment_id)
    assert cast(list[dict[str, object]], audit["attempts"])[0]["metadata"] == expected
    registry.finish_attempt(experiment_id, "denied", status="FAILED", error="lockbox forbidden")
    audit = registry.audit(experiment_id)
    assert cast(list[dict[str, object]], audit["attempts"])[0]["metadata"] == expected


def test_invalid_start_metadata_is_not_committed(tmp_path: Path) -> None:
    registry, experiment_id = registered(tmp_path)
    before = registry.audit(experiment_id)
    with pytest.raises(RegistryError, match="JSON-native"):
        registry.start_attempt(experiment_id, "attempt", metadata={"bad": float("nan")})
    assert registry.audit(experiment_id) == before
