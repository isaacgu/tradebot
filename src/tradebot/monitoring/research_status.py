"""Bounded, read-only monitoring of a published engineering decision replay.

The mutable ``latest.json`` file is discovery metadata only. This consumer accepts
it only when it points to the exact immutable report shape produced by schema v1,
then verifies the report bytes and internal safety/count identities. It never opens
the decision trace, imports research modules, or starts a replay.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, cast

from prometheus_client import CollectorRegistry, Gauge

LATEST_MAX_BYTES = 16 * 1024
REPORT_MAX_BYTES = 2 * 1024 * 1024
SOURCE_MAX_BYTES = 4 * 1024 * 1024
MAX_INSTRUMENTS = 64
MAX_IMPLEMENTATION_FILES = 128
MAX_SOURCE_FILES = 1_024
MAX_COUNT = 10**12
SPEC_SHA256 = "dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37"

_DIGEST = re.compile(r"[0-9a-f]{64}")
_MODULE = re.compile(r"tradebot(?:\.[a-z_][a-z0-9_]*)*")
_INSTRUMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}/(?:EURUSD|GBPUSD)")
_SOURCE_KINDS = ("synthetic", "immutable_clean_snapshot")
_STATUSES = ("warmup", "suppressed", "abstain", "forecast")
_DATASET_DOMAIN = b"tradebot.clean-dataset.v1\n"


class ResearchArtifactError(ValueError):
    """A stable, non-sensitive classification for rejected replay evidence."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ResearchArtifactError("malformed_report")
    return cast(dict[str, Any], value)


def _list(value: object, *, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ResearchArtifactError("malformed_report")
    return value


def _reject_constant(_value: str) -> NoReturn:
    raise ResearchArtifactError("nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResearchArtifactError("duplicate_json_key")
        result[key] = value
    return result


def _finite_tree(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ResearchArtifactError("nonfinite_json")
    if isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)


def _read_regular(path: Path, maximum: int) -> tuple[bytes, int]:
    if path.is_symlink():
        raise ResearchArtifactError("symlink_rejected")
    try:
        metadata = path.stat()
    except FileNotFoundError:
        raise
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
        raise ResearchArtifactError("unsafe_or_oversize_file")
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise ResearchArtifactError("unsafe_or_oversize_file")
    return payload, metadata.st_mtime_ns


def _read_json(path: Path, maximum: int) -> tuple[dict[str, Any], bytes, int]:
    payload, modified = _read_regular(path, maximum)
    try:
        decoded = payload.decode("utf-8")
        value = json.loads(
            decoded,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except ResearchArtifactError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise ResearchArtifactError("malformed_json") from exc
    _finite_tree(value)
    return _object(value), payload, modified


def _digest(value: object, *, code: str = "invalid_digest") -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ResearchArtifactError(code)
    return value


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise ResearchArtifactError("invalid_count")
    return value


def _canonical_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResearchArtifactError("malformed_report") from exc


def _relative_manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value:
        raise ResearchArtifactError("unsafe_manifest_path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ResearchArtifactError("unsafe_manifest_path")
    return value


def _dataset_identity(value: object) -> tuple[str, int]:
    rows = _list(value, maximum=MAX_SOURCE_FILES)
    if not rows:
        raise ResearchArtifactError("missing_source_manifest")
    manifest: list[tuple[str, str]] = []
    for item in rows:
        row = _object(item)
        manifest.append((_relative_manifest_path(row.get("path")), _digest(row.get("sha256"))))
    if manifest != sorted(manifest) or len({path for path, _ in manifest}) != len(manifest):
        raise ResearchArtifactError("invalid_source_manifest")
    digest = hashlib.sha256(_DATASET_DOMAIN)
    for path, checksum in manifest:
        digest.update(path.encode("utf-8"))
        digest.update(b"\t")
        digest.update(checksum.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), len(manifest)


def _status_counts(value: object) -> dict[str, int]:
    source = _object(value)
    if any(key not in _STATUSES for key in source):
        raise ResearchArtifactError("unsupported_decision_status")
    return {status: _count(source.get(status, 0)) for status in _STATUSES}


def _safe_instrument(value: object) -> str:
    if not isinstance(value, str) or _INSTRUMENT.fullmatch(value) is None:
        raise ResearchArtifactError("unsafe_instrument")
    return value


def _implementation_rows(identity: Mapping[str, Any]) -> list[tuple[str, str]]:
    values = _list(identity.get("implementation"), maximum=MAX_IMPLEMENTATION_FILES)
    if not values:
        raise ResearchArtifactError("missing_implementation_identity")
    rows: list[tuple[str, str]] = []
    for value in values:
        item = _object(value)
        module = item.get("path")
        if not isinstance(module, str) or _MODULE.fullmatch(module) is None:
            raise ResearchArtifactError("unsafe_implementation_module")
        rows.append((module, _digest(item.get("sha256"))))
    if len({module for module, _ in rows}) != len(rows):
        raise ResearchArtifactError("duplicate_implementation_module")
    expected = _digest(identity.get("implementation_sha256"))
    if hashlib.sha256(_canonical_bytes(values)).hexdigest() != expected:
        raise ResearchArtifactError("implementation_identity_mismatch")
    return rows


def _validate_spec_identity(identity: Mapping[str, Any], repository: Path) -> None:
    reported = _digest(identity.get("spec_sha256"))
    if reported != SPEC_SHA256 or identity.get("spec_version") != "1.0":
        raise ResearchArtifactError("unsupported_spec_identity")
    try:
        root = repository.resolve(strict=True)
        spec = root / "docs/SPEC.md"
        if _path_has_symlink(spec, root):
            raise ResearchArtifactError("symlink_rejected")
        payload, _ = _read_regular(spec, SOURCE_MAX_BYTES)
    except FileNotFoundError as exc:
        raise ResearchArtifactError("current_spec_missing") from exc
    if hashlib.sha256(payload).hexdigest() != reported:
        raise ResearchArtifactError("current_spec_mismatch")


def _path_has_symlink(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    if current.is_symlink():
        return True
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _implementation_current(repository: Path, rows: Sequence[tuple[str, str]]) -> bool | None:
    try:
        root = repository.resolve(strict=True)
    except OSError:
        return None
    if not root.is_dir() or root.is_symlink():
        return None
    current = True
    for module, expected in rows:
        stem = root / "src" / Path(*module.split("."))
        candidates = (stem.with_suffix(".py"), stem / "__init__.py")
        existing = [path for path in candidates if path.is_file()]
        if len(existing) != 1 or _path_has_symlink(existing[0], root):
            return None
        try:
            payload, _ = _read_regular(existing[0], SOURCE_MAX_BYTES)
        except (OSError, ResearchArtifactError):
            return None
        current &= hashlib.sha256(payload).hexdigest() == expected
    return current


def _unknown(artifact_state: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_state": artifact_state,
        "artifact_reason": reason,
        "overview_state": "UNKNOWN",
        "evidence_class": "unknown",
        "source_kind": "unknown",
        "source_scope": "UNKNOWN",
        "run_id": None,
        "report_sha256": None,
        "bars_processed": None,
        "decisions_by_status": dict.fromkeys(_STATUSES),
        "implementation_current": None,
        "source_manifest_files": None,
        "safety": {
            "execution_enabled": None,
            "orders_created": None,
            "costs_modelled": None,
            "pnl_reported": None,
        },
        "limitations": [
            "No verified engineering replay is available; missing or rejected evidence is unknown.",
            "This monitor never runs a replay or reads its detailed decision trace.",
        ],
    }


def _validated_status(
    pointer: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    report_sha256: str,
    repository: Path,
) -> dict[str, Any]:
    run_id = _digest(pointer.get("run_id"), code="invalid_run_id")
    if (
        type(pointer.get("schema_version")) is not int
        or pointer.get("schema_version") != 1
        or pointer.get("evidence_class") != "engineering-decision-replay-only"
        or pointer.get("report") != f"{run_id}/report.json"
        or _digest(pointer.get("sha256")) != report_sha256
    ):
        raise ResearchArtifactError("invalid_pointer_contract")
    if (
        type(report.get("schema_version")) is not int
        or report.get("schema_version") != 1
        or report.get("run_id") != run_id
        or report.get("status") != "COMPLETED"
        or report.get("evidence_class") != "engineering-decision-replay-only"
    ):
        raise ResearchArtifactError("invalid_report_contract")

    safety_expected: dict[str, object] = {
        "execution_enabled": False,
        "orders_created": 0,
        "costs_modelled": False,
        "pnl_reported": False,
    }
    if any(
        type(report.get(key)) is not type(value) or report.get(key) != value
        for key, value in safety_expected.items()
    ):
        raise ResearchArtifactError("unsafe_capability_claim")
    if (
        report.get("economic_evaluation") != "NOT_PERFORMED"
        or report.get("data_acceptance") != "NOT_ASSERTED"
        or report.get("gate_approvals_claimed") != []
        or report.get("forecast_scaling") != "UNCALIBRATED"
    ):
        raise ResearchArtifactError("unsafe_evidence_claim")

    identity = _object(report.get("identity"))
    _validate_spec_identity(identity, repository)
    provenance = _object(identity.get("provenance"))
    source_kind = provenance.get("source_kind")
    if source_kind not in _SOURCE_KINDS:
        raise ResearchArtifactError("unsupported_source_kind")
    calculated_dataset, source_files = _dataset_identity(provenance.get("source_manifest"))
    if _digest(provenance.get("dataset_id")) != calculated_dataset:
        raise ResearchArtifactError("dataset_identity_mismatch")
    implementation = _implementation_rows(identity)

    config = _object(identity.get("config"))
    if hashlib.sha256(_canonical_bytes(config)).hexdigest() != _digest(
        identity.get("config_sha256")
    ):
        raise ResearchArtifactError("config_identity_mismatch")
    configured = [
        _safe_instrument(value)
        for value in _list(config.get("instruments"), maximum=MAX_INSTRUMENTS)
    ]
    if not configured or configured != sorted(set(configured)):
        raise ResearchArtifactError("invalid_instrument_selection")
    if type(config.get("timeframe_seconds")) is not int or config["timeframe_seconds"] < 1:
        raise ResearchArtifactError("invalid_timeframe")

    bars = _count(report.get("bars_processed"))
    if bars < 1:
        raise ResearchArtifactError("empty_replay")
    totals = _status_counts(report.get("decisions_by_status"))
    if sum(totals.values()) != bars:
        raise ResearchArtifactError("decision_count_mismatch")
    instruments = _object(report.get("instruments"))
    if set(instruments) != set(configured):
        raise ResearchArtifactError("instrument_count_mismatch")
    by_instrument: dict[str, dict[str, int]] = {}
    for instrument in configured:
        by_instrument[instrument] = _status_counts(instruments[instrument])
        if sum(by_instrument[instrument].values()) < 1:
            raise ResearchArtifactError("empty_instrument")
    for status in _STATUSES:
        if sum(values[status] for values in by_instrument.values()) != totals[status]:
            raise ResearchArtifactError("instrument_count_mismatch")

    trace = _object(report.get("trace"))
    trace_sha256 = _digest(trace.get("sha256"))
    if trace.get("path") != "decisions.jsonl" or _count(trace.get("records")) != bars:
        raise ResearchArtifactError("invalid_trace_identity")
    expected_run_id = hashlib.sha256(_canonical_bytes([identity, trace_sha256])).hexdigest()
    if run_id != expected_run_id:
        raise ResearchArtifactError("run_identity_mismatch")

    return {
        "schema_version": 1,
        "artifact_state": "verified",
        "artifact_reason": "verified_report",
        "overview_state": "ENGINEERING_ONLY",
        "evidence_class": "engineering-decision-replay-only",
        "source_kind": source_kind,
        "source_scope": (
            "SYNTHETIC_ENGINEERING_ONLY"
            if source_kind == "synthetic"
            else "CLEAN_SNAPSHOT_ENGINEERING_ONLY"
        ),
        "run_id": run_id,
        "report_sha256": report_sha256,
        "bars_processed": bars,
        "decisions_by_status": totals,
        "implementation_current": _implementation_current(repository, implementation),
        "source_manifest_files": source_files,
        "safety": safety_expected,
        "limitations": [
            "Engineering replay only; no live signal, call, trade, order, cost or PnL evidence.",
            "Forecast values are uncalibrated research signals, not probabilities or sizes.",
            "Detailed decision traces are intentionally not read by monitoring.",
        ],
    }


def research_status(output_root: Path, repository: Path) -> dict[str, Any]:
    """Return a safe summary; missing or rejected artifacts remain explicitly unknown."""
    if output_root.is_symlink():
        return _unknown("invalid", "unsafe_output_root")
    if not output_root.exists():
        return _unknown("missing", "latest_missing")
    if not output_root.is_dir():
        return _unknown("invalid", "unsafe_output_root")
    try:
        root = output_root.resolve(strict=True)
        pointer_path = output_root / "latest.json"
        pointer, _, _ = _read_json(pointer_path, LATEST_MAX_BYTES)
        run_id = _digest(pointer.get("run_id"), code="invalid_run_id")
        expected_relative = f"{run_id}/report.json"
        if pointer.get("report") != expected_relative:
            raise ResearchArtifactError("unsafe_report_path")
        run_directory = output_root / run_id
        report_path = run_directory / "report.json"
        if run_directory.is_symlink() or not run_directory.is_dir() or report_path.is_symlink():
            raise ResearchArtifactError("symlink_rejected")
        resolved_report = report_path.resolve(strict=True)
        if resolved_report != root / run_id / "report.json":
            raise ResearchArtifactError("unsafe_report_path")
        resolved_report.relative_to(root)
        report, report_bytes, _ = _read_json(report_path, REPORT_MAX_BYTES)
        report_hash = hashlib.sha256(report_bytes).hexdigest()
        return _validated_status(
            pointer,
            report,
            report_sha256=report_hash,
            repository=repository,
        )
    except FileNotFoundError:
        return _unknown("missing", "latest_or_report_missing")
    except ResearchArtifactError as exc:
        return _unknown("invalid", exc.code)
    except (OSError, RuntimeError, TypeError, KeyError):
        return _unknown("invalid", "unreadable_or_malformed_artifact")


def add_research_metrics(registry: CollectorRegistry, status: Mapping[str, Any]) -> None:
    """Add bounded research summary metrics; invalid/missing numeric results stay absent."""
    artifact = Gauge(
        "tradebot_research_artifact_state",
        "Verified, missing or rejected engineering replay evidence (one-hot).",
        ("state",),
        registry=registry,
    )
    for state in ("verified", "missing", "invalid"):
        artifact.labels(state).set(int(status["artifact_state"] == state))
    overview = Gauge(
        "tradebot_research_overview_state",
        "Engineering-only or unknown replay overview (one-hot).",
        ("state",),
        registry=registry,
    )
    for state in ("engineering_only", "unknown"):
        overview.labels(state).set(
            int((status["overview_state"] == "ENGINEERING_ONLY") == (state == "engineering_only"))
        )
    if status["artifact_state"] != "verified":
        return

    source_kind = cast(str, status["source_kind"])
    source = Gauge(
        "tradebot_research_source_kind",
        "Synthetic or immutable-clean-snapshot engineering source (one-hot).",
        ("kind",),
        registry=registry,
    )
    for kind in _SOURCE_KINDS:
        source.labels(kind).set(int(source_kind == kind))
    Gauge(
        "tradebot_research_bars_processed",
        "Bars processed by the verified engineering replay.",
        registry=registry,
    ).set(cast(float, status["bars_processed"]))
    current = status["implementation_current"]
    if current is not None:
        Gauge(
            "tradebot_research_implementation_current",
            "1 when reported implementation hashes match current source; 0 when changed.",
            registry=registry,
        ).set(int(cast(bool, current)))
    decision_counts = Gauge(
        "tradebot_research_decisions",
        "Verified engineering decisions by status.",
        ("status",),
        registry=registry,
    )
    counts = cast(Mapping[str, int], status["decisions_by_status"])
    for decision_status in _STATUSES:
        decision_counts.labels(decision_status).set(counts[decision_status])
