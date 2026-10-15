"""Invented trust fixtures only: these do not approve collected market data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tradebot.data.storage import FileDigest, dataset_id
from tradebot.research.authorization import (
    ApprovedSnapshot,
    AuthorizationError,
    ResearchPurpose,
    ResearchScope,
    TrustedReleaseRegistry,
    authorize_snapshot,
    load_trusted_registry,
    release_package_sha256,
    scope_to_dict,
)
from tradebot.research.feed import SnapshotSpec

START = datetime(2020, 1, 1, tzinfo=UTC)
END = datetime(2020, 2, 1, tzinfo=UTC)
KNOWN = datetime(2020, 3, 1, tzinfo=UTC)
PURPOSE = ResearchPurpose.STRATEGY_TRAINING
ROLES = (
    "admission",
    "calendar",
    "policy",
    "reference_result",
    "gate1",
    "gate2",
    "history",
    "stress",
    "tick_fidelity",
)


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def write_json(path: Path, value: object) -> str:
    path.write_bytes(canonical(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Fixture:
    root: Path
    spec: SnapshotSpec
    scope: ResearchScope
    release: dict[str, Any]
    purpose: ResearchPurpose = PURPOSE

    def pin(self) -> TrustedReleaseRegistry:
        core = {
            k: v
            for k, v in self.release.items()
            if k
            not in {
                "independent_review",
                "principal_approval",
            }
        }
        package = hashlib.sha256(canonical(core)).hexdigest()
        for key, person, stamp in (
            ("independent_review", "Synthetic Reviewer", "2020-02-01T00:00:00+00:00"),
            ("principal_approval", "Synthetic Principal", "2020-02-02T00:00:00+00:00"),
        ):
            decision = {
                "person": person,
                "decision": "APPROVED",
                "purpose": str(self.purpose),
                "dataset_id": self.spec.dataset_id,
                "package_sha256": package,
                "decided_at_utc": stamp,
            }
            self.release[key] = decision | {
                "artifact": {
                    "path": f"{key}.json",
                    "sha256": write_json(self.root / f"{key}.json", decision),
                }
            }
        return self.repin()

    def repin(self) -> TrustedReleaseRegistry:
        release_sha = write_json(self.root / "release.json", self.release)
        registry = {
            "schema_version": 1,
            "kind": "research-release-registry",
            "releases": [
                {
                    "release_sha256": release_sha,
                    "purpose": str(self.purpose),
                    "dataset_id": self.spec.dataset_id,
                }
            ],
        }
        registry_sha = write_json(self.root / "registry.json", registry)
        return load_trusted_registry(self.root / "registry.json", expected_sha256=registry_sha)

    def set_evidence(self, role: str, field: str, value: object) -> None:
        path = self.root / self.release["evidence"][role]["path"]
        receipt = json.loads(path.read_bytes())
        receipt[field] = value
        self.release["evidence"][role]["sha256"] = write_json(path, receipt)

    def authorize(self, registry: TrustedReleaseRegistry | None) -> ApprovedSnapshot:
        return authorize_snapshot(
            self.spec,
            purpose=self.purpose,
            trusted_registry=registry,
            requested_scope=self.scope,
            release_path=self.root / "release.json",
            evidence_root=self.root,
            known_at=KNOWN,
        )


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    return make_fixture(tmp_path)


def make_fixture(tmp_path: Path) -> Fixture:
    """Build invented metadata for integration tests, never real-data approvals."""
    scope = ResearchScope(
        source="synthetic-source",
        venue="synthetic-venue",
        instruments=("EURUSD",),
        timeframe="1m",
        start_utc=START,
        end_utc=END,
    )
    files = (
        FileDigest(
            f"clean/bars/synthetic-venue/1m/EURUSD/2020/01/part-{'a' * 64}.parquet",
            "b" * 64,
        ),
    )
    spec = SnapshotSpec(scope.venue, scope.timeframe, files, dataset_id(files))
    partitions = [
        {"id": "invented-partition", "sha256": "c" * 64, "eligibility": "APPROVED_FOR_PURPOSE"}
    ]
    lineage = [
        {
            "file": {"path": files[0].path, "sha256": files[0].sha256},
            "source_partitions": partitions,
        }
    ]
    evidence: dict[str, Any] = {}
    for role in ROLES:
        receipt: dict[str, Any] = {
            "schema_version": 1,
            "kind": "research-authorization-evidence",
            "role": role,
            "status": "PASSED" if role == "reference_result" else "APPROVED",
            "purpose": str(PURPOSE),
            "dataset_id": spec.dataset_id,
            "scope": scope_to_dict(scope),
        }
        if role == "admission":
            receipt["partitions"] = partitions
        evidence[role] = {
            "path": f"{role}.json",
            "sha256": write_json(tmp_path / f"{role}.json", receipt),
        }
    context = {
        "schema_version": 1,
        "purpose": str(PURPOSE),
        "dataset_id": spec.dataset_id,
        "scope": scope_to_dict(scope),
    }
    for role, receipt in (
        (
            "candidate",
            context
            | {
                "kind": "research-authorization-candidate",
                "status": "COMPLETE",
                "retrieval_status": "COMPLETE",
                "partitions": [{"id": item["id"], "sha256": item["sha256"]} for item in partitions],
            },
        ),
        (
            "producer_inventory",
            context
            | {
                "kind": "research-authorization-inventory",
                "reproducibility_status": "PASSED",
                "lineage": lineage,
            },
        ),
    ):
        evidence[role] = {
            "path": f"{role}.json",
            "sha256": write_json(tmp_path / f"{role}.json", receipt),
        }
    spec_path = Path(__file__).resolve().parents[3] / "docs" / "SPEC.md"
    (tmp_path / "SPEC.md").write_bytes(spec_path.read_bytes())
    evidence["spec"] = {
        "path": "SPEC.md",
        "sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
    }
    release = {
        "schema_version": 1,
        "kind": "approved-research-snapshot",
        "purpose": str(PURPOSE),
        "scope": scope_to_dict(scope),
        "snapshot": {
            "schema_version": 1,
            "venue": spec.venue,
            "timeframe": spec.timeframe,
            "dataset_id": spec.dataset_id,
            "files": [{"path": item.path, "sha256": item.sha256} for item in files],
        },
        "evidence": evidence,
        "lineage": lineage,
    }
    return Fixture(tmp_path, spec, scope, release)


def test_synthetic_pinned_release_issues_frozen_token_without_market_file_access(
    fixture: Fixture,
) -> None:
    registry = fixture.pin()
    token = fixture.authorize(registry)
    assert token.spec == fixture.spec
    assert token.scope == fixture.scope
    assert token.purpose is PURPOSE
    assert token.known_at == KNOWN
    assert (
        token.registry_sha256
        == hashlib.sha256((fixture.root / "registry.json").read_bytes()).hexdigest()
    )
    assert (
        token.release_sha256
        == hashlib.sha256((fixture.root / "release.json").read_bytes()).hexdigest()
    )
    assert not (fixture.root / fixture.spec.files[0].path).exists()
    token.verify_unchanged()
    with pytest.raises(FrozenInstanceError):
        token.purpose = ResearchPurpose.ECONOMIC_EVALUATION  # type: ignore[misc]


def test_package_digest_matches_independent_canonical_implementation(fixture: Fixture) -> None:
    assert (
        release_package_sha256(fixture.release)
        == hashlib.sha256(canonical(fixture.release)).hexdigest()
    )
    fixture.pin()
    with pytest.raises(AuthorizationError):
        release_package_sha256(fixture.release)


@pytest.mark.parametrize("token_type", [ApprovedSnapshot, TrustedReleaseRegistry])
def test_tokens_cannot_be_constructed_by_callers(token_type: type[object]) -> None:
    with pytest.raises(TypeError, match="issued"):
        token_type()


def test_missing_trust_and_invalid_purpose_deny_before_paths(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("authorization touched a path before checking trust and purpose")

    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    with pytest.raises(AuthorizationError, match="trust"):
        fixture.authorize(None)
    with pytest.raises(AuthorizationError, match="purpose"):
        authorize_snapshot(
            fixture.spec,
            purpose="STRATEGY_TRAINING",  # type: ignore[arg-type]
            trusted_registry=None,
            requested_scope=fixture.scope,
            release_path=fixture.root,
            evidence_root=fixture.root,
            known_at=KNOWN,
        )


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("status", ["FAILED", "INDETERMINATE", "NOT_APPROVED", "QA_ONLY", "DRAFT"])
def test_pinned_failed_or_pending_evidence_is_not_approval(
    fixture: Fixture, role: str, status: str
) -> None:
    fixture.set_evidence(role, "status", status)
    with pytest.raises(AuthorizationError, match="status"):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize(
    "role,field,value",
    [
        ("candidate", "status", "INCOMPLETE"),
        ("candidate", "retrieval_status", "PARTIAL"),
        ("producer_inventory", "reproducibility_status", "FAILED"),
    ],
)
def test_native_completion_fields_are_required(
    fixture: Fixture, role: str, field: str, value: str
) -> None:
    fixture.set_evidence(role, field, value)
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize("eligibility", ["QA_ONLY", "QUARANTINED", "APPROVED", "UNKNOWN"])
@pytest.mark.parametrize("where", ["lineage", "admission"])
def test_pinning_cannot_promote_ineligible_partitions(
    fixture: Fixture, eligibility: str, where: str
) -> None:
    if where == "lineage":
        fixture.release["lineage"][0]["source_partitions"][0]["eligibility"] = eligibility
    else:
        fixture.set_evidence(
            "admission",
            "partitions",
            [
                {
                    "id": "invented-partition",
                    "sha256": "c" * 64,
                    "eligibility": eligibility,
                }
            ],
        )
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "ECONOMIC_EVALUATION"),
        ("dataset_id", "d" * 64),
        ("role", "gate1"),
        ("schema_version", True),
        ("extra", "ignored?"),
    ],
)
def test_receipt_context_and_unknown_fields_reject(
    fixture: Fixture, field: str, value: object
) -> None:
    fixture.set_evidence("gate2", field, value)
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize("role", ROLES)
def test_receipt_scope_must_match_every_role(fixture: Fixture, role: str) -> None:
    bad = scope_to_dict(replace(fixture.scope, source="another-source"))
    fixture.set_evidence(role, "scope", bad)
    with pytest.raises(AuthorizationError, match="scope"):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "other"},
        {"venue": "other"},
        {"timeframe": "5m"},
        {"instruments": ("EURUSD", "GBPUSD")},
        {"start_utc": START + timedelta(days=1)},
        {"end_utc": END + timedelta(days=1)},
    ],
)
def test_scope_is_exact_not_superset_or_subset(fixture: Fixture, changes: dict[str, Any]) -> None:
    registry = fixture.pin()
    fixture.scope = replace(fixture.scope, **changes)
    with pytest.raises(AuthorizationError):
        fixture.authorize(registry)


@pytest.mark.parametrize(
    "changes",
    [
        {"instruments": ("GBPUSD", "EURUSD")},
        {"instruments": ("EURUSD", "EURUSD")},
        {"instruments": ()},
        {"instruments": ("US500",)},
        {"instruments": ["EURUSD"]},
        {"source": "../bad"},
        {"venue": ""},
        {"timeframe": ""},
        {"start_utc": START.replace(tzinfo=None)},
        {"start_utc": END},
        {"end_utc": START},
        {"start_utc": "2020-01-01"},
    ],
)
def test_scope_rejects_ambiguous_values(fixture: Fixture, changes: dict[str, Any]) -> None:
    with pytest.raises((AuthorizationError, TypeError, ValueError)):
        replace(fixture.scope, **changes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "ECONOMIC_EVALUATION"),
        ("kind", "unapproved-research-snapshot"),
        ("schema_version", True),
        ("extra", 1),
        ("lineage", []),
    ],
)
def test_release_context_and_required_exact_fields(
    fixture: Fixture, field: str, value: object
) -> None:
    fixture.release[field] = value
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_role",
        "duplicate_lineage",
        "wrong_file",
        "empty_partitions",
        "extra_partition",
        "duplicate_partition",
        "unselected_instrument",
    ],
)
def test_lineage_and_required_evidence_are_exact(fixture: Fixture, mutation: str) -> None:
    if mutation == "missing_role":
        del fixture.release["evidence"]["gate2"]
    elif mutation == "duplicate_lineage":
        fixture.release["lineage"] *= 2
    elif mutation == "wrong_file":
        fixture.release["lineage"][0]["file"]["sha256"] = "d" * 64
    elif mutation == "empty_partitions":
        fixture.release["lineage"][0]["source_partitions"] = []
    elif mutation == "extra_partition":
        fixture.set_evidence(
            "admission",
            "partitions",
            [{"id": "other", "sha256": "d" * 64, "eligibility": "APPROVED_FOR_PURPOSE"}],
        )
    elif mutation == "duplicate_partition":
        fixture.release["lineage"][0]["source_partitions"] *= 2
    else:
        fixture.release["scope"]["instruments"] = ["EURUSD", "GBPUSD"]
        fixture.scope = replace(fixture.scope, instruments=("EURUSD", "GBPUSD"))
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize(
    "path",
    [
        "../gate2.json",
        "/gate2.json",
        "nested/../gate2.json",
        "./gate2.json",
        "a\\gate2.json",
        "C:gate2.json",
        "a//gate2.json",
        "",
    ],
)
def test_evidence_path_escape_or_noncanonical_rejected(fixture: Fixture, path: str) -> None:
    fixture.release["evidence"]["gate2"]["path"] = path
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.pin())


def test_duplicate_evidence_reference_rejected(fixture: Fixture) -> None:
    fixture.release["evidence"]["gate2"] = fixture.release["evidence"]["gate1"]
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize(
    "target", ["registry.json", "release.json", "gate2.json", "SPEC.md", "principal_approval.json"]
)
def test_any_metadata_tamper_denies_before_issue_and_after_issue(
    fixture: Fixture, target: str
) -> None:
    registry = fixture.pin()
    token = fixture.authorize(registry)
    path = fixture.root / target
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(AuthorizationError):
        token.verify_unchanged()
    with pytest.raises(AuthorizationError):
        fixture.authorize(registry)


def test_wrong_frozen_spec_denies_even_if_rehashed_and_pinned(fixture: Fixture) -> None:
    path = fixture.root / "SPEC.md"
    path.write_bytes(b"invented substitute spec")
    fixture.release["evidence"]["spec"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(AuthorizationError, match="SPEC"):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize(
    "key,field,value",
    [
        ("principal_approval", "person", "  synthetic REVIEWER  "),
        ("independent_review", "person", " "),
        ("principal_approval", "decision", "PENDING"),
        ("principal_approval", "purpose", "ECONOMIC_EVALUATION"),
        ("principal_approval", "dataset_id", "d" * 64),
        ("principal_approval", "package_sha256", "e" * 64),
        ("principal_approval", "decided_at_utc", "2020-03-02T00:00:00Z"),
        ("principal_approval", "decided_at_utc", "2020-01-01T00:00:00Z"),
        ("independent_review", "decided_at_utc", "2020-02-01T00:00:00"),
        ("independent_review", "decided_at_utc", "2020-02-01T00:00:00+02:00"),
        ("independent_review", "extra", True),
    ],
)
def test_human_decision_consistency_is_enforced(
    fixture: Fixture, key: str, field: str, value: object
) -> None:
    fixture.pin()
    fixture.release[key][field] = value
    receipt = {k: v for k, v in fixture.release[key].items() if k != "artifact"}
    fixture.release[key]["artifact"]["sha256"] = write_json(fixture.root / f"{key}.json", receipt)
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.repin())


def test_decision_artifact_must_equal_release_record(fixture: Fixture) -> None:
    fixture.pin()
    fixture.release["principal_approval"]["person"] = "Different Synthetic Person"
    with pytest.raises(AuthorizationError, match="artifact"):
        fixture.authorize(fixture.repin())


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"bad":NaN}',
        b'{"bad":Infinity}',
        b"[]",
        b"null",
        b"not-json",
    ],
)
def test_registry_strict_json_rejects_invalid_or_duplicate_input(
    tmp_path: Path, raw: bytes
) -> None:
    path = tmp_path / "registry.json"
    path.write_bytes(raw)
    with pytest.raises(AuthorizationError):
        load_trusted_registry(path, expected_sha256=hashlib.sha256(raw).hexdigest())


def test_registry_digest_must_be_independently_correct(fixture: Fixture) -> None:
    fixture.pin()
    with pytest.raises(AuthorizationError):
        load_trusted_registry(fixture.root / "registry.json", expected_sha256="0" * 64)


@pytest.mark.parametrize("target", ["registry.json", "release.json", "gate2.json"])
def test_oversized_metadata_rejected_without_unbounded_reads(fixture: Fixture, target: str) -> None:
    registry = fixture.pin()
    path = fixture.root / target
    path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    with pytest.raises(AuthorizationError, match="resource bound"):
        if target == "registry.json":
            load_trusted_registry(
                path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
            )
        else:
            fixture.authorize(registry)


def test_numeric_overflow_is_nonfinite_json(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    raw = b'{"bad":1e9999}'
    path.write_bytes(raw)
    with pytest.raises(AuthorizationError, match="non-finite"):
        load_trusted_registry(path, expected_sha256=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "duplicate",
        "wrong_purpose",
        "wrong_dataset",
        "wrong_release",
        "extra",
        "bad_digest",
    ],
)
def test_registry_context_pin_cannot_be_substituted(fixture: Fixture, mutation: str) -> None:
    fixture.pin()
    path = fixture.root / "registry.json"
    row = json.loads(path.read_bytes())
    if mutation == "empty":
        row["releases"] = []
    elif mutation == "duplicate":
        row["releases"] *= 2
    elif mutation == "wrong_purpose":
        row["releases"][0]["purpose"] = "ECONOMIC_EVALUATION"
    elif mutation == "wrong_dataset":
        row["releases"][0]["dataset_id"] = "d" * 64
    elif mutation == "wrong_release":
        row["releases"][0]["release_sha256"] = "e" * 64
    elif mutation == "extra":
        row["releases"][0]["approved"] = True
    else:
        row["releases"][0]["release_sha256"] = "NOT-A-HASH"
    with pytest.raises(AuthorizationError):
        registry = load_trusted_registry(path, expected_sha256=write_json(path, row))
        fixture.authorize(registry)


@pytest.mark.parametrize("target", ["gate2.json", "release.json", "registry.json"])
def test_symlink_metadata_rejected(fixture: Fixture, target: str) -> None:
    fixture.pin()
    original = fixture.root / target
    actual = fixture.root / f"actual-{target}"
    original.rename(actual)
    try:
        original.symlink_to(actual)
    except OSError:
        pytest.skip("symlink capability unavailable")
    with pytest.raises(AuthorizationError):
        registry = load_trusted_registry(
            fixture.root / "registry.json",
            expected_sha256=hashlib.sha256(
                (fixture.root / "registry.json").read_bytes()
            ).hexdigest(),
        )
        fixture.authorize(registry)


@pytest.mark.parametrize(
    "stamp",
    [
        "2020-01-01T00:00:00.000000999Z",
        "2020-01-01T00:00:00.1234567+00:00",
        "2020-W01-3T00:00:00Z",
        "2020-01-01 00:00:00Z",
        "2020-01-01T00:00:00+00:00:00.1",
        "2020-01-01T00:00:00.1-00:00",
        "2020-02-31T00:00:00Z",
        1,
    ],
)
def test_unrepresentable_or_noncanonical_scope_stamps_reject(
    fixture: Fixture, stamp: object
) -> None:
    fixture.release["scope"]["start_utc"] = stamp
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.pin())


def test_just_after_known_at_nanosecond_decision_is_not_truncated_into_approval(
    fixture: Fixture,
) -> None:
    fixture.pin()
    decision = fixture.release["principal_approval"]
    decision["decided_at_utc"] = "2020-03-01T00:00:00.000000999Z"
    decision["artifact"]["sha256"] = write_json(
        fixture.root / "principal_approval.json",
        {k: v for k, v in decision.items() if k != "artifact"},
    )
    with pytest.raises(AuthorizationError, match="microsecond"):
        fixture.authorize(fixture.repin())


@pytest.mark.parametrize("role", ["candidate", "producer_inventory"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "ECONOMIC_EVALUATION"),
        ("dataset_id", "d" * 64),
        ("extra", True),
        ("schema_version", True),
    ],
)
def test_producer_context_cannot_be_substituted(
    fixture: Fixture, role: str, field: str, value: object
) -> None:
    fixture.set_evidence(role, field, value)
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize("role", ["candidate", "producer_inventory"])
def test_native_completion_claim_without_bound_inventory_is_not_accepted(
    fixture: Fixture, role: str
) -> None:
    native = (
        {"status": "COMPLETE", "retrieval_status": "COMPLETE"}
        if role == "candidate"
        else {"reproducibility_status": "PASSED"}
    )
    fixture.release["evidence"][role]["sha256"] = write_json(fixture.root / f"{role}.json", native)
    with pytest.raises(AuthorizationError):
        fixture.authorize(fixture.pin())


@pytest.mark.parametrize("role", ["candidate", "producer_inventory"])
def test_producer_scope_is_exact(fixture: Fixture, role: str) -> None:
    fixture.set_evidence(
        role, "scope", scope_to_dict(replace(fixture.scope, source="wrong-source"))
    )
    with pytest.raises(AuthorizationError, match="scope"):
        fixture.authorize(fixture.pin())


def test_candidate_partition_union_is_exact(fixture: Fixture) -> None:
    fixture.set_evidence("candidate", "partitions", [{"id": "wrong", "sha256": "d" * 64}])
    with pytest.raises(AuthorizationError, match="candidate partitions"):
        fixture.authorize(fixture.pin())


def test_full_inventory_ancestry_cannot_swap_parents_preserving_union(fixture: Fixture) -> None:
    second = FileDigest(fixture.spec.files[0].path.replace("a" * 64, "d" * 64), "f" * 64)
    files = (*fixture.spec.files, second)
    fixture.spec = SnapshotSpec(
        fixture.spec.venue, fixture.spec.timeframe, files, dataset_id(files)
    )
    fixture.release["snapshot"]["dataset_id"] = fixture.spec.dataset_id
    fixture.release["snapshot"]["files"].append({"path": second.path, "sha256": second.sha256})
    fixture.release["lineage"].append(
        {
            "file": {"path": second.path, "sha256": second.sha256},
            "source_partitions": [
                {"id": "second-parent", "sha256": "e" * 64, "eligibility": "APPROVED_FOR_PURPOSE"}
            ],
        }
    )
    parents = [parent for row in fixture.release["lineage"] for parent in row["source_partitions"]]
    for role in (*ROLES, "candidate", "producer_inventory"):
        fixture.set_evidence(role, "dataset_id", fixture.spec.dataset_id)
    fixture.set_evidence("admission", "partitions", parents)
    fixture.set_evidence(
        "candidate", "partitions", [{"id": p["id"], "sha256": p["sha256"]} for p in parents]
    )
    fixture.set_evidence("producer_inventory", "lineage", fixture.release["lineage"])
    fixture.authorize(fixture.pin()).verify_unchanged()
    swapped = json.loads(canonical(fixture.release["lineage"]))
    swapped[0]["source_partitions"], swapped[1]["source_partitions"] = (
        swapped[1]["source_partitions"],
        swapped[0]["source_partitions"],
    )
    fixture.set_evidence("producer_inventory", "lineage", swapped)
    with pytest.raises(AuthorizationError, match="exact file ancestry"):
        fixture.authorize(fixture.pin())


def test_metadata_rechecked_after_all_receipts_are_read(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tradebot.research.authorization as module

    registry = fixture.pin()
    parse = module._json
    mutated = False

    def mutate_after_earlier_evidence_read(payload: bytes) -> object:
        nonlocal mutated
        result = parse(payload)
        if isinstance(result, dict) and result.get("role") == "tick_fidelity" and not mutated:
            path = fixture.root / "gate2.json"
            path.write_bytes(path.read_bytes() + b" ")
            mutated = True
        return result

    monkeypatch.setattr(module, "_json", mutate_after_earlier_evidence_read)
    with pytest.raises(AuthorizationError, match="SHA-256 differs"):
        fixture.authorize(registry)
    assert mutated


@pytest.mark.parametrize("target", ["gate2.json", "principal_approval.json"])
def test_missing_metadata_does_not_issue_token(fixture: Fixture, target: str) -> None:
    registry = fixture.pin()
    (fixture.root / target).unlink()
    with pytest.raises(AuthorizationError, match="unavailable"):
        fixture.authorize(registry)


def test_economic_purpose_requires_its_own_complete_pinned_fixture(fixture: Fixture) -> None:
    fixture.purpose = ResearchPurpose.ECONOMIC_EVALUATION
    fixture.release["purpose"] = str(fixture.purpose)
    for role in (*ROLES, "candidate", "producer_inventory"):
        fixture.set_evidence(role, "purpose", str(fixture.purpose))
    token = fixture.authorize(fixture.pin())
    assert token.purpose is ResearchPurpose.ECONOMIC_EVALUATION
    token.verify_unchanged()


def test_future_known_at_cannot_issue_valid_historical_release(fixture: Fixture) -> None:
    registry = fixture.pin()
    with pytest.raises(AuthorizationError, match="trusted host authorization time"):
        authorize_snapshot(
            fixture.spec,
            purpose=PURPOSE,
            trusted_registry=registry,
            requested_scope=fixture.scope,
            release_path=fixture.root / "release.json",
            evidence_root=fixture.root,
            known_at=datetime.now(UTC) + timedelta(days=1),
        )


def test_future_known_at_cannot_activate_pinned_future_decisions(fixture: Fixture) -> None:
    fixture.pin()
    future = datetime.now(UTC) + timedelta(days=1)
    for key, stamp in (
        ("independent_review", future),
        ("principal_approval", future + timedelta(hours=1)),
    ):
        decision = fixture.release[key]
        decision["decided_at_utc"] = stamp.isoformat()
        decision["artifact"]["sha256"] = write_json(
            fixture.root / f"{key}.json",
            {k: v for k, v in decision.items() if k != "artifact"},
        )
    registry = fixture.repin()
    with pytest.raises(AuthorizationError, match="trusted host authorization time"):
        authorize_snapshot(
            fixture.spec,
            purpose=PURPOSE,
            trusted_registry=registry,
            requested_scope=fixture.scope,
            release_path=fixture.root / "release.json",
            evidence_root=fixture.root,
            known_at=future + timedelta(hours=2),
        )


def test_future_known_at_denies_before_metadata_access(
    fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tradebot.research.authorization as module

    registry = fixture.pin()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("future-known-at authorization touched metadata")

    monkeypatch.setattr(module, "_read", forbidden)
    with pytest.raises(AuthorizationError, match="trusted host authorization time"):
        authorize_snapshot(
            fixture.spec,
            purpose=PURPOSE,
            trusted_registry=registry,
            requested_scope=fixture.scope,
            release_path=fixture.root / "release.json",
            evidence_root=fixture.root,
            known_at=datetime.now(UTC) + timedelta(days=1),
        )
