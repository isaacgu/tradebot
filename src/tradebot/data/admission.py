"""Conservative triage of a pinned, completed source-acquisition evidence set.

This audit freshly hashes compressed source files and reconciles recorded metrics;
it does not repeat the original tick-by-tick semantic analysis. Its trust anchor is
an independently supplied candidate SHA-256, never a checksum discovered beside it.
It issues no research snapshot, acceptance signature, or execution authorization.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, fields
from pathlib import Path, PurePosixPath
from typing import Any, cast

from tradebot.data.acquisition_probe import (
    AcquisitionPlan,
    ChunkEvidence,
    ChunkMetrics,
    ChunkRequest,
    parse_plan,
    summarise_dataset,
)

ADMISSION_SCHEMA_VERSION = 1
DEFECT_FIELDS = (
    "crossed_quotes",
    "locked_quotes",
    "bid_nonpositive",
    "ask_nonpositive",
    "timestamp_regressions",
    "time_field_mismatches",
    "negative_volume",
    "negative_volume_real",
)
_RAW_FORMAT = "tradebot-source-ticks-semantic-v1-tsv-gzip"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_FileState = tuple[int, int, int, int, int]
_ObservedFiles = dict[Path, tuple[str, _FileState]]


class AdmissionEvidenceError(ValueError):
    """Missing, changed, or contradictory evidence prevents manifest publication."""


def canonical_json(value: object) -> bytes:
    """Canonical JSON for existing checkpoint integrity and additive manifest hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AdmissionEvidenceError(message)


def _object(value: object, label: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise AdmissionEvidenceError(f"{label} must be a lowercase SHA-256")
    return value


def _state(path: Path) -> _FileState:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _file(path: Path) -> Path:
    absolute = path.absolute()
    _require(absolute.resolve(strict=True) == absolute, f"noncanonical or linked path: {path}")
    _require(absolute.is_file(), f"evidence is not a regular file: {path}")
    return absolute


def _record_file(
    observed: _ObservedFiles | None, path: Path, digest: str, state: _FileState
) -> None:
    if observed is not None:
        previous = observed.get(path)
        _require(
            previous is None or previous == (digest, state),
            f"evidence changed between reads: {path}",
        )
        observed[path] = digest, state


def _hash_file(path: Path, observed: _ObservedFiles | None = None) -> tuple[str, int]:
    path = _file(path)
    before = _state(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    _require(before == _state(path), f"evidence changed while hashing: {path}")
    _require(path.resolve(strict=True) == path, f"evidence path changed: {path}")
    checksum = digest.hexdigest()
    _record_file(observed, path, checksum, before)
    return checksum, before[2]


def _read_bytes(path: Path, observed: _ObservedFiles | None = None) -> tuple[bytes, str]:
    path = _file(path)
    before = _state(path)
    encoded = path.read_bytes()
    _require(before == _state(path), f"JSON changed while reading: {path}")
    _require(path.resolve(strict=True) == path, f"evidence path changed: {path}")
    checksum = hashlib.sha256(encoded).hexdigest()
    _record_file(observed, path, checksum, before)
    return encoded, checksum


def _read_json(path: Path, observed: _ObservedFiles | None = None) -> tuple[dict[str, Any], str]:
    encoded, checksum = _read_bytes(path, observed)
    value = json.loads(encoded, object_pairs_hook=_unique_object)
    return _object(value, str(path)), checksum


def _verify_observed_files(observed: _ObservedFiles) -> None:
    """Rehash the read set, then reject earlier files changing during that pass."""
    for path, (expected_hash, expected_state) in observed.items():
        _require(_state(_file(path)) == expected_state, f"evidence changed during audit: {path}")
        _require(
            _hash_file(path)[0] == expected_hash, f"evidence hash changed during audit: {path}"
        )
    # Rehashing a large later source file takes time. The earlier files must still
    # have their originally observed identities when the final pass completes.
    for path, (_, expected_state) in observed.items():
        _require(_state(_file(path)) == expected_state, f"evidence changed during audit: {path}")


def _nonnegative(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise AdmissionEvidenceError(f"{label} must be a nonnegative integer")
    return value


def _metrics(value: object) -> ChunkMetrics:
    row = _object(value, "metrics")
    _require(set(row) == {field.name for field in fields(ChunkMetrics)}, "metrics schema mismatch")
    counts = (
        "tick_count",
        "exact_adjacent_duplicates",
        "same_millisecond_transitions",
        *DEFECT_FIELDS,
    )
    for name in counts:
        _nonnegative(row[name], name)
    total = row["tick_count"]
    _require(all(row[name] <= total for name in DEFECT_FIELDS), "defect count exceeds tick count")
    _require(row["crossed_quotes"] + row["locked_quotes"] <= total, "quote counts overlap")
    converted = dict(row)
    for name in ("flag_counts", "positive_spread_counts"):
        _require(type(row[name]) is list, f"{name} must be a list")
        for item in row[name]:
            _require(type(item) is list and len(item) == 2, f"invalid {name} pair")
            _nonnegative(item[1], f"{name} count")
        converted[name] = tuple(tuple(item) for item in row[name])
    return ChunkMetrics(**converted)


def _plan_report(plan: AcquisitionPlan, config_name: str) -> dict[str, Any]:
    return {
        "config_name": config_name,
        "plan_hash": plan.plan_hash,
        "probe_id": plan.probe_id,
        "source": plan.source,
        "purpose": plan.purpose,
        "repeat_fetches": plan.repeat_fetches,
        "chunk_sessions": plan.chunk_sessions,
        "session_boundary": "17:00 America/New_York",
        "expected_chunks": len(plan.chunks),
        "symbols": {item.logical: item.broker for item in plan.symbols},
        "windows": [
            {
                "id": item.id,
                "purpose": item.purpose,
                "start_session_date": item.start_session_date.isoformat(),
                "end_session_date_exclusive": item.end_session_date_exclusive.isoformat(),
                "expected_sessions_per_symbol": len(item.iter_session_dates()),
            }
            for item in plan.windows
        ],
    }


def _environment(report: dict[str, Any]) -> dict[str, Any]:
    terminal = _object(report.get("terminal"), "terminal")
    account = _object(report.get("account"), "account")
    code = _object(report.get("code_sha256"), "code hashes")
    _require(account.get("is_demo") is True, "candidate was not acquired on a demo account")
    return {
        "probe_version": report["probe_version"],
        "git_sha": report["git_sha"],
        "spec_sha256": report["spec_sha256"],
        "mt5_package_version": report["package_version"],
        "runner_sha256": code["runner"],
        "analysis_module_sha256": code["analysis_module"],
        "terminal_build": terminal["build"],
        "terminal_name": terminal["name"],
        "terminal_company": terminal["company"],
        "account_server": account["server"],
        "account_company": account["company"],
        "account_is_demo": account["is_demo"],
    }


def _raw_reference(
    row: object,
    root: Path,
    expected_path: str,
    semantic_sha256: str,
    observed: _ObservedFiles,
) -> dict[str, Any]:
    reference = _object(row, "raw reference")
    _require(
        set(reference)
        == {
            "path",
            "format",
            "semantic_sha256",
            "compressed_sha256",
            "compressed_bytes",
            "uncompressed_bytes",
        },
        "raw reference schema mismatch",
    )
    _require(reference["path"] == expected_path, "raw path does not match planned identity")
    _require(reference["format"] == _RAW_FORMAT, "unsupported raw format")
    _require(reference["semantic_sha256"] == semantic_sha256, "raw semantic identity mismatch")
    _digest(reference["compressed_sha256"], "compressed SHA")
    _nonnegative(reference["uncompressed_bytes"], "uncompressed bytes")
    path = root.joinpath(*PurePosixPath(expected_path).parts)
    _require(path.resolve(strict=True).is_relative_to(root), "raw path escaped work directory")
    observed_hash, observed_size = _hash_file(path, observed)
    _require(observed_hash == reference["compressed_sha256"], f"raw hash mismatch: {expected_path}")
    _require(observed_size == reference["compressed_bytes"], f"raw size mismatch: {expected_path}")
    return dict(reference)


def _partition(
    *,
    request: ChunkRequest,
    plan: AcquisitionPlan,
    recorded: object,
    root: Path,
    environment: dict[str, Any],
    observed: _ObservedFiles,
) -> tuple[dict[str, Any], ChunkEvidence, Counter[str]]:
    row = _object(recorded, "candidate partition")
    _require(set(row) == {"evidence", "raw", "fetches", "repeat_comparisons"}, "partition schema")
    base = f"{plan.plan_hash}/{request.chunk_id}"
    raw_path = f"{base}.source-ticks.tsv.gz"
    checkpoint_path = f"{base}.source-ticks.tsv.checkpoint.json"
    checkpoint, checkpoint_hash = _read_json(root / checkpoint_path, observed)
    _require(
        set(checkpoint)
        == {
            "schema_version",
            "probe_version",
            "plan_hash",
            "source",
            "environment",
            "environment_sha256",
            "run_id",
            "completed_at_utc",
            "chunk",
            "raw",
            "fetches",
            "repeat_comparisons",
            "integrity",
        },
        "checkpoint schema mismatch",
    )
    integrity = _object(checkpoint.get("integrity"), "checkpoint integrity")
    unsigned = {key: value for key, value in checkpoint.items() if key != "integrity"}
    _require(
        integrity
        == {
            "algorithm": "sha256",
            "payload_sha256": hashlib.sha256(canonical_json(unsigned)).hexdigest(),
        },
        f"checkpoint integrity mismatch: {request.chunk_id}",
    )
    identity = {
        "schema_version": 1,
        "probe_version": environment["probe_version"],
        "plan_hash": plan.plan_hash,
        "source": plan.source,
        "environment": environment,
        "environment_sha256": hashlib.sha256(canonical_json(environment)).hexdigest(),
    }
    _require(
        all(checkpoint.get(k) == v for k, v in identity.items()), "checkpoint identity mismatch"
    )
    for report_key, checkpoint_key in (
        ("evidence", "chunk"),
        ("raw", "raw"),
        ("fetches", "fetches"),
        ("repeat_comparisons", "repeat_comparisons"),
    ):
        _require(row[report_key] == checkpoint.get(checkpoint_key), "candidate/checkpoint mismatch")
    evidence = _object(row["evidence"], "evidence")
    planned = {
        "chunk_id": request.chunk_id,
        "logical_symbol": request.logical_symbol,
        "broker_symbol": request.broker_symbol,
        "window_id": request.window_id,
        "session_date": request.session_date.isoformat(),
        "start_utc": request.start.isoformat().replace("+00:00", "Z"),
        "end_utc": request.end.isoformat().replace("+00:00", "Z"),
    }
    _require(
        all(evidence.get(k) == v for k, v in planned.items()), "planned chunk identity mismatch"
    )
    semantic = _digest(evidence.get("semantic_sha256"), "semantic SHA")
    metrics = _metrics(evidence.get("metrics"))
    raw_references = [_raw_reference(row["raw"], root, raw_path, semantic, observed)]
    fetches, comparisons = row["fetches"], row["repeat_comparisons"]
    _require(type(fetches) is list and len(fetches) == plan.repeat_fetches, "fetch count mismatch")
    _require(
        type(comparisons) is list and len(comparisons) == plan.repeat_fetches - 1,
        "repeat count mismatch",
    )
    totals: Counter[str] = Counter()
    per_fetch: list[dict[str, Any]] = []
    revisions = 0
    for repeat, item in enumerate(fetches, start=1):
        fetch = _object(item, "fetch")
        _require(
            type(fetch.get("repeat")) is int and fetch["repeat"] == repeat, "fetch index mismatch"
        )
        fetch_metrics = _metrics(fetch.get("metrics"))
        _require(fetch.get("tick_count") == fetch_metrics.tick_count, "fetch row count mismatch")
        fetch_semantic = _digest(fetch.get("semantic_sha256"), "fetch semantic SHA")
        if repeat == 1:
            _require(
                fetch_metrics == metrics and fetch_semantic == semantic, "primary metrics mismatch"
            )
        else:
            comparison = _object(comparisons[repeat - 2], "repeat comparison")
            identical = comparison.get("identical")
            _require(type(identical) is bool, "repeat verdict must be boolean")
            _require(
                comparison.get("chunk_id") == request.chunk_id
                and comparison.get("first_sha256") == semantic
                and comparison.get("second_sha256") == fetch_semantic
                and comparison.get("first_count") == metrics.tick_count
                and comparison.get("second_count") == fetch_metrics.tick_count,
                "repeat comparison identity mismatch",
            )
            if identical:
                _require(
                    fetch_semantic == semantic and fetch_metrics == metrics,
                    "false identical repeat",
                )
                _require(fetch.get("preserved_raw") is None, "unexpected identical repeat raw")
            else:
                revisions += 1
                _require(fetch_semantic != semantic, "false revised repeat")
                repeated_path = f"{base}.repeat-{repeat}.source-ticks.tsv.gz"
                raw_references.append(
                    _raw_reference(
                        fetch.get("preserved_raw"), root, repeated_path, fetch_semantic, observed
                    )
                )
        defects = {field: getattr(fetch_metrics, field) for field in DEFECT_FIELDS}
        shape = _object(fetch.get("shape"), "fetch shape")
        for field in ("discarded_before_start", "discarded_after_end"):
            defects[field] = _nonnegative(shape.get(field), field)
        totals.update(defects)
        per_fetch.append(
            {"repeat": repeat, "tick_count": fetch_metrics.tick_count, "defects": defects}
        )
    reasons = [field for field, count in sorted(totals.items()) if count]
    if metrics.tick_count == 0:
        reasons.append("EMPTY_SESSION_REQUIRES_CALENDAR_AND_SOURCE_ADJUDICATION")
    if revisions:
        reasons.append("SOURCE_REVISION_REQUIRES_ADJUDICATION")
    status = "QUARANTINED" if reasons else "QA_ONLY"
    return (
        {
            **planned,
            "status": status,
            "reasons": reasons,
            "tick_count": metrics.tick_count,
            "primary_defects": {field: getattr(metrics, field) for field in DEFECT_FIELDS},
            "all_fetch_defects": dict(totals),
            "fetch_evidence": per_fetch,
            "exact_adjacent_duplicates_retained": metrics.exact_adjacent_duplicates,
            "semantic_sha256": semantic,
            "raw_artifacts": raw_references,
            "checkpoint": {
                "path": checkpoint_path,
                "sha256": checkpoint_hash,
                "integrity": integrity,
            },
            "eligible_for_strategy": False,
            "eligible_for_execution": False,
        },
        ChunkEvidence(request=request, semantic_sha256=semantic, metrics=metrics),
        totals,
    )


def prepare_admission(
    *,
    candidate: Path,
    expected_candidate_sha256: str,
    plan_path: Path,
    work_root: Path,
) -> dict[str, Any]:
    """Verify the entire planned evidence set and return an unsigned triage manifest.

    Missing or changed evidence raises; no partial eligible manifest is returned.
    A checksum pin is a content identity, not proof of source truth or human approval.
    No promotion input exists: this version can return only QUARANTINED or QA_ONLY.
    """
    observed: _ObservedFiles = {}
    module_hash = _hash_file(Path(__file__).resolve(), observed)[0]
    pin = _digest(expected_candidate_sha256, "independently pinned candidate SHA")
    report, actual_hash = _read_json(candidate, observed)
    _require(actual_hash == pin, "candidate does not match independently pinned SHA")
    sidecar = candidate.with_suffix(candidate.suffix + ".sha256")
    sidecar_bytes, sidecar_hash = _read_bytes(sidecar, observed)
    _require(
        sidecar_bytes.decode("ascii").strip() == f"{pin}  {candidate.name}",
        "candidate sidecar mismatch",
    )
    _require(
        report.get("status") == "COMPLETE" and report.get("retrieval_status") == "COMPLETE",
        "acquisition is incomplete",
    )
    plan_payload, plan_file_hash = _read_json(plan_path, observed)
    plan = parse_plan(plan_payload)
    _require(report.get("plan") == _plan_report(plan, plan_path.name), "candidate plan mismatch")
    environment = _environment(report)
    env_hash = hashlib.sha256(canonical_json(environment)).hexdigest()
    _require(
        report.get("evidence_environment_sha256") == env_hash, "candidate environment mismatch"
    )
    root = work_root.absolute()
    _require(root.resolve(strict=True) == root and root.is_dir(), "invalid work directory")
    chunks = _object(report.get("chunks"), "candidate chunks")
    _require(
        set(chunks) == {item.chunk_id for item in plan.chunks}, "planned chunk coverage mismatch"
    )
    partitions: list[dict[str, Any]] = []
    evidence: list[ChunkEvidence] = []
    all_fetch_defects: Counter[str] = Counter()
    for request in plan.chunks:
        partition, item, defects = _partition(
            request=request,
            plan=plan,
            recorded=chunks[request.chunk_id],
            root=root,
            environment=environment,
            observed=observed,
        )
        partitions.append(partition)
        evidence.append(item)
        all_fetch_defects.update(defects)
    dataset = asdict(summarise_dataset(plan, evidence))
    _require(
        canonical_json(dataset) == canonical_json(report.get("dataset")),
        "candidate dataset summary mismatch",
    )
    _verify_observed_files(observed)
    counts = Counter(item["status"] for item in partitions)
    ticks = Counter[str]()
    for partition in partitions:
        ticks[partition["status"]] += partition["tick_count"]
    return {
        "schema_version": ADMISSION_SCHEMA_VERSION,
        "purpose": "source_data_quarantine_and_qa_preparation",
        "status": "PREPARED_REQUIRES_ACCEPTANCE",
        "evidence_integrity": "VERIFIED",
        "admission_module_sha256": module_hash,
        "candidate": {
            "filename": candidate.name,
            "sha256": actual_hash,
            "sidecar_sha256": sidecar_hash,
        },
        "plan": {
            "filename": plan_path.name,
            "file_sha256": plan_file_hash,
            "plan_hash": plan.plan_hash,
        },
        "dataset_sha256": dataset["dataset_sha256"],
        "environment_sha256": env_hash,
        "summary": {
            "partitions": len(partitions),
            "total_primary_ticks": dataset["total_ticks"],
            "quarantined_partitions": counts["QUARANTINED"],
            "qa_only_partitions": counts["QA_ONLY"],
            "quarantined_primary_ticks": ticks["QUARANTINED"],
            "qa_only_primary_ticks": ticks["QA_ONLY"],
            "empty_partitions": len(dataset["empty_chunk_ids"]),
            "primary_defects": {field: dataset[field] for field in DEFECT_FIELDS},
            "all_fetch_defects": dict(all_fetch_defects),
            "exact_adjacent_duplicates_retained": dataset["exact_adjacent_duplicates"],
        },
        "eligibility": {"strategy": False, "execution": False, "gate_approval": False},
        "operating_constraint": {
            "training_and_refinement": "DEMO_ONLY",
            "permission_evidence": (
                "Principal reports FBS permits account-holder data use for this personal project; "
                "no further permission verification requested"
            ),
            "permission_record": "docs/reports/fbs_data_permission.md",
            "live_trading_authorized_by_this_manifest": False,
        },
        "required_acceptance_evidence": [
            "Dated expected-liquidity calendar with source evidence and required approval",
            "Pinned reference-month/cross-source evaluation under the personal/demo "
            "usage constraint",
            "Principal-signed quality thresholds and resolved partition adjudications",
            "Five documented human bar checks and independent reviewer/Principal signoffs",
            "Approved immutable research snapshot with chronological split evidence",
        ],
        "verification_scope": {
            "compressed_raw_sha256_recomputed": True,
            "checkpoint_payload_integrity_recomputed": True,
            "dataset_identity_recomputed": True,
            "raw_semantic_metrics_recomputed": False,
            "complete_evidence_set_rechecked": True,
            "metric_authority": (
                "Recorded candidate/checkpoint metrics bound to the caller-pinned candidate "
                "and verified compressed bytes"
            ),
            "promotion_enforced_at_research_feed": False,
        },
        "limitations": [
            "This manifest is an additive triage record, not a SnapshotSpec or research-feed "
            "access control.",
            "A checksum binds content; it is not a digital signature, broker authenticity proof, "
            "or acceptance approval.",
            "Compressed bytes are verified freshly; semantic metrics are reconciled against "
            "recorded evidence without another raw tick scan.",
            "Quarantine is recorded by partition identity; raw files are neither moved, edited, "
            "filtered, nor deleted.",
            "All repeated source ticks remain preserved; exact adjacent duplicates alone "
            "are not a rejection rule.",
            "Empty sessions remain unadjudicated; no closure, outage, or replacement "
            "observations are invented.",
            "Coverage is limited to the planned sessions; it does not establish continuous "
            "historical availability.",
        ],
        "partitions": partitions,
    }
