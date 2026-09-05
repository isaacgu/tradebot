"""Fail-closed, purpose-scoped future research release authorization.

No production trust registry or release is supplied here. An independently pinned
registry is the operator-controlled trust root, not an authentication of human
identity or an approval inferred from a manifest. Normalized evidence receipts
are a FUTURE contract: existing QA_ONLY or pending evidence is not compatible.
Tokens defend ordinary API misuse in trusted Python, not hostile in-process code
or an operating-system adversary. This module never opens market payload files.
The host UTC clock is trusted for the authorization-time ceiling; maintaining its
correctness is an operational obligation, not established by these metadata checks.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from tradebot.data.storage import FileDigest, safe_segment
from tradebot.research.feed import SnapshotSpec

_SPEC_SHA256 = "dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_UTC_ISO = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)\Z"
)
_SCOPE_FIELDS = {"source", "venue", "instruments", "timeframe", "start_utc", "end_utc"}
_RECEIPT_ROLES = frozenset(
    {
        "admission",
        "calendar",
        "policy",
        "reference_result",
        "gate1",
        "gate2",
        "history",
        "stress",
        "tick_fidelity",
    }
)
_EVIDENCE_ROLES = _RECEIPT_ROLES | {"spec", "candidate", "producer_inventory"}
_DECISIONS = {"independent_review", "principal_approval"}
_CORE_FIELDS = {"schema_version", "kind", "purpose", "scope", "snapshot", "evidence", "lineage"}
_DECISION_FIELDS = {
    "person",
    "decision",
    "purpose",
    "dataset_id",
    "package_sha256",
    "decided_at_utc",
}
_ISSUER = object()
# Resource bound for individual metadata artifacts, not a data-quality/risk gate.
# Payload Parquet is never read here. Eight MiB exceeds current SPEC/receipts by
# a wide margin; larger inventories need an explicitly reviewed schema change.
_MAX_METADATA_BYTES = 8 * 1024 * 1024
type FileState = tuple[int, int, int, int, int]


class AuthorizationError(ValueError):
    """The requested financial-purpose data release has not been established."""


class ResearchPurpose(StrEnum):
    """Financial-purpose modes; synthetic engineering is deliberately not a member."""

    STRATEGY_TRAINING = "STRATEGY_TRAINING"
    ECONOMIC_EVALUATION = "ECONOMIC_EVALUATION"


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise AuthorizationError(f"{label} must be a UTC-aware datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise AuthorizationError(f"{label} must be expressed in UTC")
    return value.astimezone(UTC)


def _stamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or _UTC_ISO.fullmatch(value) is None:
        raise AuthorizationError(f"{label} must be a UTC ISO datetime with microsecond precision")
    try:
        return _utc(datetime.fromisoformat(value), label)
    except ValueError as exc:
        raise AuthorizationError(f"invalid {label}: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchScope:
    """One exact source/venue/FX/timeframe and UTC half-open observation scope."""

    source: str
    venue: str
    instruments: tuple[str, ...]
    timeframe: str
    start_utc: datetime
    end_utc: datetime

    def __post_init__(self) -> None:
        for name in ("source", "venue", "timeframe"):
            try:
                safe_segment(getattr(self, name), field=name)
            except (TypeError, ValueError) as exc:
                raise AuthorizationError(str(exc)) from exc
        if self.timeframe not in {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}:
            raise AuthorizationError("scope timeframe must be a supported canonical timeframe")
        if (
            type(self.instruments) is not tuple
            or not self.instruments
            or any(
                not isinstance(item, str) or item not in {"EURUSD", "GBPUSD"}
                for item in self.instruments
            )
            or tuple(sorted(set(self.instruments))) != self.instruments
        ):
            raise AuthorizationError("scope instruments must be sorted distinct EURUSD/GBPUSD")
        object.__setattr__(self, "start_utc", _utc(self.start_utc, "scope start_utc"))
        object.__setattr__(self, "end_utc", _utc(self.end_utc, "scope end_utc"))
        if self.start_utc >= self.end_utc:
            raise AuthorizationError("scope must be a nonempty half-open interval")


def scope_to_dict(scope: ResearchScope) -> dict[str, Any]:
    """Return the version-1 native scope representation used in release receipts."""
    if not isinstance(scope, ResearchScope):
        raise AuthorizationError("scope must be ResearchScope")
    return {
        "source": scope.source,
        "venue": scope.venue,
        "instruments": list(scope.instruments),
        "timeframe": scope.timeframe,
        "start_utc": scope.start_utc.isoformat(),
        "end_utc": scope.end_utc.isoformat(),
    }


def _object(value: object, fields: set[str] | frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise AuthorizationError(f"{label} must contain exactly: {', '.join(sorted(fields))}")
    return value


def _version(row: dict[str, Any], kind: str) -> None:
    if type(row["schema_version"]) is not int or row["schema_version"] != 1:
        raise AuthorizationError("schema_version must be integer 1")
    if row["kind"] != kind:
        raise AuthorizationError(f"kind must be {kind}")


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise AuthorizationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _scope(value: object) -> ResearchScope:
    row = _object(value, _SCOPE_FIELDS, "scope")
    if not isinstance(row["instruments"], list):
        raise AuthorizationError("scope instruments must be a JSON array")
    return ResearchScope(
        source=row["source"],
        venue=row["venue"],
        instruments=tuple(row["instruments"]),
        timeframe=row["timeframe"],
        start_utc=_stamp(row["start_utc"], "scope start_utc"),
        end_utc=_stamp(row["end_utc"], "scope end_utc"),
    )


def _canonical(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuthorizationError("release core must contain finite JSON-native values") from exc


def release_package_sha256(release_core: object) -> str:
    """Hash the exact core without decision records, avoiding a circular binding.

    Canonical encoding is UTF-8, sorted keys, compact separators, ASCII escaping,
    finite JSON values and one terminal LF. Complete release bytes are separately
    pinned by the operator registry after both decision artifacts are attached.
    """
    return hashlib.sha256(
        _canonical(_object(release_core, _CORE_FIELDS, "release core"))
    ).hexdigest()


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in pairs:
        if key in row:
            raise AuthorizationError(f"duplicate JSON key: {key}")
        row[key] = value
    return row


def _nonfinite(value: str) -> NoReturn:
    raise AuthorizationError(f"non-finite JSON value: {value}")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise AuthorizationError("non-finite JSON numeric overflow")
    return result


def _json(payload: bytes) -> object:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=_nonfinite,
            parse_float=_finite_float,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise AuthorizationError(f"invalid metadata JSON: {exc}") from exc


def _state(path: Path) -> FileState:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _regular_file(path: Path) -> Path:
    try:
        absolute = path.absolute()
        if absolute.resolve(strict=True) != absolute or not absolute.is_file():
            raise AuthorizationError("metadata path must be a regular file without symlinks")
        return absolute
    except OSError as exc:
        raise AuthorizationError(f"metadata file unavailable: {path}") from exc


@dataclass(frozen=True, slots=True)
class _VerifiedFile:
    path: Path
    sha256: str
    state: FileState

    def verify(self) -> None:
        _, current = _read(self.path, expected=self.sha256)
        if current.state != self.state:
            raise AuthorizationError(f"metadata file identity changed: {self.path}")


def _read(path: Path, *, expected: str | None = None) -> tuple[bytes, _VerifiedFile]:
    try:
        canonical = _regular_file(path)
        before = _state(canonical)
        if before[2] > _MAX_METADATA_BYTES:
            raise AuthorizationError("metadata exceeds the 8 MiB resource bound")
        with canonical.open("rb") as stream:
            payload = stream.read(_MAX_METADATA_BYTES + 1)
        after = _state(canonical)
        if len(payload) > _MAX_METADATA_BYTES:
            raise AuthorizationError("metadata exceeds the 8 MiB resource bound")
        digest = hashlib.sha256(payload).hexdigest()
        if before != after or _regular_file(canonical) != canonical:
            raise AuthorizationError(f"metadata changed while reading: {path}")
        if expected is not None and digest != expected:
            raise AuthorizationError(f"metadata SHA-256 differs: {path}")
        return payload, _VerifiedFile(canonical, digest, after)
    except OSError as exc:
        raise AuthorizationError(f"metadata file unavailable: {path}") from exc


@dataclass(frozen=True, slots=True)
class _ReleasePin:
    release_sha256: str
    purpose: ResearchPurpose
    dataset_id: str


@dataclass(frozen=True, slots=True, init=False)
class TrustedReleaseRegistry:
    """Opaque pinned trust state issued only after strict registry verification."""

    registry_sha256: str
    _entries: tuple[_ReleasePin, ...]
    _metadata: _VerifiedFile
    _issuer: object

    def __init__(self) -> None:
        raise TypeError("TrustedReleaseRegistry is issued by load_trusted_registry")


@dataclass(frozen=True, slots=True, init=False)
class ApprovedSnapshot:
    """Immutable, purpose-scoped authorization; not a payload-validation result."""

    spec: SnapshotSpec
    scope: ResearchScope
    purpose: ResearchPurpose
    release_sha256: str
    registry_sha256: str
    known_at: datetime
    _metadata: tuple[_VerifiedFile, ...]
    _issuer: object

    def __init__(self) -> None:
        raise TypeError("ApprovedSnapshot is issued by authorize_snapshot")

    def verify_unchanged(self) -> None:
        """Revalidate every bound metadata file; use before and after consumption."""
        if getattr(self, "_issuer", None) is not _ISSUER:
            raise AuthorizationError("authorization token was not issued by this validator")
        for item in self._metadata:
            item.verify()


def load_trusted_registry(path: Path, *, expected_sha256: str) -> TrustedReleaseRegistry:
    """Load a registry whose exact digest was independently pinned by trusted code.

    Passing an expected digest obtained from the same untrusted input is not an
    independent trust decision. No CLI derives or installs this digest for users.
    """
    expected = _digest(expected_sha256, "expected registry SHA-256")
    payload, metadata = _read(path, expected=expected)
    row = _object(_json(payload), {"schema_version", "kind", "releases"}, "registry")
    _version(row, "research-release-registry")
    if not isinstance(row["releases"], list):
        raise AuthorizationError("registry releases must be a JSON array")
    entries: list[_ReleasePin] = []
    seen: set[str] = set()
    for value in row["releases"]:
        entry = _object(value, {"release_sha256", "purpose", "dataset_id"}, "registry release")
        digest = _digest(entry["release_sha256"], "release SHA-256")
        if digest in seen:
            raise AuthorizationError("duplicate registry release SHA-256")
        seen.add(digest)
        try:
            purpose = ResearchPurpose(entry["purpose"])
        except (TypeError, ValueError) as exc:
            raise AuthorizationError("invalid registry purpose") from exc
        entries.append(_ReleasePin(digest, purpose, _digest(entry["dataset_id"], "dataset_id")))
    metadata.verify()
    token = object.__new__(TrustedReleaseRegistry)
    object.__setattr__(token, "registry_sha256", metadata.sha256)
    object.__setattr__(token, "_entries", tuple(entries))
    object.__setattr__(token, "_metadata", metadata)
    object.__setattr__(token, "_issuer", _ISSUER)
    return token


class _EvidenceReader:
    def __init__(self, root: Path, already_read: tuple[_VerifiedFile, ...]) -> None:
        try:
            self.root = root.absolute()
            if self.root.resolve(strict=True) != self.root or not self.root.is_dir():
                raise AuthorizationError("evidence root must be a directory without symlinks")
        except OSError as exc:
            raise AuthorizationError("evidence root unavailable") from exc
        self.metadata = list(already_read)
        self.seen = {item.path for item in already_read}

    def read(self, value: object, *, label: str) -> tuple[bytes, _VerifiedFile]:
        ref = _object(value, {"path", "sha256"}, f"{label} reference")
        relative = ref["path"]
        if not isinstance(relative, str):
            raise AuthorizationError("evidence path must be a canonical relative string")
        parts = PurePosixPath(relative).parts
        if (
            not parts
            or PurePosixPath(relative).is_absolute()
            or "/".join(parts) != relative
            or any(part in {".", ".."} for part in parts)
            or "\\" in relative
            or ":" in relative
            or "\x00" in relative
        ):
            raise AuthorizationError("evidence path must be canonical and remain within its root")
        path = self.root.joinpath(*parts)
        if not path.is_relative_to(self.root) or path in self.seen:
            raise AuthorizationError("duplicate or escaping evidence path")
        self.seen.add(path)
        result = _read(path, expected=_digest(ref["sha256"], f"{label} SHA-256"))
        self.metadata.append(result[1])
        return result


def _snapshot(value: object) -> SnapshotSpec:
    row = _object(
        value, {"schema_version", "venue", "timeframe", "files", "dataset_id"}, "snapshot"
    )
    if type(row["schema_version"]) is not int or row["schema_version"] != 1:
        raise AuthorizationError("snapshot schema_version must be integer 1")
    if not isinstance(row["files"], list):
        raise AuthorizationError("snapshot files must be a JSON array")
    files = []
    for item in row["files"]:
        ref = _object(item, {"path", "sha256"}, "snapshot file")
        files.append(FileDigest(ref["path"], ref["sha256"]))
    try:
        return SnapshotSpec(row["venue"], row["timeframe"], tuple(files), row["dataset_id"])
    except (TypeError, ValueError) as exc:
        raise AuthorizationError(f"invalid snapshot: {exc}") from exc


def _partitions(value: object, *, require_eligibility: bool = True) -> dict[str, str]:
    if not isinstance(value, list) or not value:
        raise AuthorizationError("partitions must be a nonempty JSON array")
    result: dict[str, str] = {}
    for item in value:
        fields = {"id", "sha256"} | ({"eligibility"} if require_eligibility else set())
        row = _object(item, fields, "source partition")
        identity = row["id"]
        if not isinstance(identity, str) or not identity.strip() or identity != identity.strip():
            raise AuthorizationError("partition id must be a nonempty trimmed string")
        if identity in result:
            raise AuthorizationError("duplicate source partition")
        if require_eligibility and row["eligibility"] != "APPROVED_FOR_PURPOSE":
            raise AuthorizationError("partition eligibility must be APPROVED_FOR_PURPOSE")
        result[identity] = _digest(row["sha256"], "partition SHA-256")
    return result


def _lineage(value: object, spec: SnapshotSpec) -> dict[str, dict[str, str]]:
    if not isinstance(value, list) or len(value) != len(spec.files):
        raise AuthorizationError("lineage must cover exactly the selected files")
    union: dict[str, str] = {}
    mapping: dict[str, dict[str, str]] = {}
    for item, selected in zip(value, spec.files, strict=True):
        row = _object(item, {"file", "source_partitions"}, "lineage")
        ref = _object(row["file"], {"path", "sha256"}, "lineage file")
        if ref != {"path": selected.path, "sha256": selected.sha256}:
            raise AuthorizationError("lineage file differs from selected snapshot")
        mapping[selected.path] = _partitions(row["source_partitions"])
        for identity, digest in mapping[selected.path].items():
            if identity in union and union[identity] != digest:
                raise AuthorizationError("inconsistent partition hashes across lineage")
            union[identity] = digest
    return mapping


def _receipt(
    value: object,
    *,
    role: str,
    purpose: ResearchPurpose,
    spec: SnapshotSpec,
    scope: ResearchScope,
) -> dict[str, Any]:
    fields = {"schema_version", "kind", "role", "status", "purpose", "dataset_id", "scope"}
    row = _object(
        value, fields | ({"partitions"} if role == "admission" else set()), f"{role} evidence"
    )
    _version(row, "research-authorization-evidence")
    if row["role"] != role or row["purpose"] != purpose or row["dataset_id"] != spec.dataset_id:
        raise AuthorizationError(f"{role} evidence role, purpose or dataset differs")
    if row["status"] != ("PASSED" if role == "reference_result" else "APPROVED"):
        raise AuthorizationError(f"{role} evidence status does not authorize this purpose")
    if _scope(row["scope"]) != scope:
        raise AuthorizationError(f"{role} evidence scope differs")
    return row


def _evidence(
    value: object,
    reader: _EvidenceReader,
    *,
    purpose: ResearchPurpose,
    spec: SnapshotSpec,
    scope: ResearchScope,
    lineage: dict[str, dict[str, str]],
) -> None:
    rows = _object(value, _EVIDENCE_ROLES, "evidence")
    partitions = {
        identity: digest for parents in lineage.values() for identity, digest in parents.items()
    }
    for role in sorted(_EVIDENCE_ROLES):
        payload, metadata = reader.read(rows[role], label=role)
        if role == "spec":
            if metadata.sha256 != _SPEC_SHA256:
                raise AuthorizationError("SPEC evidence differs from the frozen SPEC SHA-256")
            continue
        value = _json(payload)
        if role in _RECEIPT_ROLES:
            receipt = _receipt(value, role=role, purpose=purpose, spec=spec, scope=scope)
            if role == "admission" and _partitions(receipt["partitions"]) != partitions:
                raise AuthorizationError("admission partitions differ from exact lineage union")
        else:
            _production_evidence(
                value,
                role=role,
                purpose=purpose,
                spec=spec,
                scope=scope,
                lineage=lineage,
                partitions=partitions,
            )


def _production_evidence(
    value: object,
    *,
    role: str,
    purpose: ResearchPurpose,
    spec: SnapshotSpec,
    scope: ResearchScope,
    lineage: dict[str, dict[str, str]],
    partitions: dict[str, str],
) -> None:
    # These normalized future receipts are not today's native candidate/inventory.
    # Requiring full per-file ancestry prevents a PASSED but unrelated inventory
    # (or swapped raw parents preserving the union) from establishing lineage.
    context = {"schema_version", "kind", "purpose", "dataset_id", "scope"}
    fields = (
        {"status", "retrieval_status", "partitions"}
        if role == "candidate"
        else {"reproducibility_status", "lineage"}
    )
    row = _object(value, context | fields, f"{role} evidence")
    _version(
        row,
        "research-authorization-candidate"
        if role == "candidate"
        else "research-authorization-inventory",
    )
    if row["purpose"] != purpose or row["dataset_id"] != spec.dataset_id:
        raise AuthorizationError(f"{role} evidence purpose or dataset differs")
    if _scope(row["scope"]) != scope:
        raise AuthorizationError(f"{role} evidence scope differs")
    if role == "candidate":
        if row["status"] != "COMPLETE" or row["retrieval_status"] != "COMPLETE":
            raise AuthorizationError("candidate status and retrieval_status must be COMPLETE")
        if _partitions(row["partitions"], require_eligibility=False) != partitions:
            raise AuthorizationError("candidate partitions differ from exact lineage union")
    else:
        if row["reproducibility_status"] != "PASSED":
            raise AuthorizationError("producer_inventory reproducibility_status must be PASSED")
        if _lineage(row["lineage"], spec) != lineage:
            raise AuthorizationError("producer_inventory lineage differs from exact file ancestry")


def _decision(
    value: object,
    reader: _EvidenceReader,
    *,
    purpose: ResearchPurpose,
    spec: SnapshotSpec,
    package: str,
) -> tuple[str, datetime]:
    row = _object(value, _DECISION_FIELDS | {"artifact"}, "human decision")
    person = row["person"]
    if not isinstance(person, str) or not person.strip():
        raise AuthorizationError("decision person must be a nonempty string")
    if (
        row["decision"] != "APPROVED"
        or row["purpose"] != purpose
        or row["dataset_id"] != spec.dataset_id
        or row["package_sha256"] != package
    ):
        raise AuthorizationError("human decision does not approve the same purpose/dataset/package")
    stamp = _stamp(row["decided_at_utc"], "decision decided_at_utc")
    payload, _ = reader.read(row["artifact"], label="decision artifact")
    receipt = _object(_json(payload), _DECISION_FIELDS, "decision artifact")
    if receipt != {key: val for key, val in row.items() if key != "artifact"}:
        raise AuthorizationError("decision artifact differs from release decision")
    return person.strip().casefold(), stamp


def authorize_snapshot(
    spec: SnapshotSpec,
    *,
    purpose: ResearchPurpose,
    trusted_registry: TrustedReleaseRegistry | None,
    requested_scope: ResearchScope,
    release_path: Path,
    evidence_root: Path,
    known_at: datetime,
) -> ApprovedSnapshot:
    """Authorize metadata before constructing any feed or touching its data iterator.

    Only an exact operator-pinned future release, complete purpose-scoped evidence
    and consistent independent-review/Principal records can issue a token. Receipt
    status is necessary but not a trust root. Human identities are not authenticated.
    Historical known_at is supported, but cannot exceed host UTC sampled once here.
    """
    if not isinstance(purpose, ResearchPurpose):
        raise AuthorizationError("purpose must be an explicit ResearchPurpose")
    if (
        not isinstance(trusted_registry, TrustedReleaseRegistry)
        or getattr(trusted_registry, "_issuer", None) is not _ISSUER
    ):
        raise AuthorizationError("independently pinned trusted registry is required")
    if not isinstance(spec, SnapshotSpec) or not isinstance(requested_scope, ResearchScope):
        raise AuthorizationError("spec and requested_scope must be validated immutable types")
    known = _utc(known_at, "known_at")
    authorized_at = datetime.now(UTC)
    if known > authorized_at:
        raise AuthorizationError("known_at cannot exceed trusted host authorization time")
    trusted_registry._metadata.verify()
    payload, release_metadata = _read(release_path)
    pin = _ReleasePin(release_metadata.sha256, purpose, spec.dataset_id)
    if pin not in trusted_registry._entries:
        raise AuthorizationError("release bytes/purpose/dataset are not pinned in trusted registry")
    row = _object(_json(payload), _CORE_FIELDS | _DECISIONS, "release")
    _version(row, "approved-research-snapshot")
    if row["purpose"] != purpose:
        raise AuthorizationError("release purpose differs from requested purpose")
    scope = _scope(row["scope"])
    if scope != requested_scope:
        raise AuthorizationError("release scope must exactly match requested scope")
    if _snapshot(row["snapshot"]) != spec:
        raise AuthorizationError("release snapshot differs from requested selected-file manifest")
    instruments = tuple(sorted({PurePosixPath(item.path).parts[4] for item in spec.files}))
    if (scope.venue, scope.timeframe, scope.instruments) != (
        spec.venue,
        spec.timeframe,
        instruments,
    ):
        raise AuthorizationError("scope venue/timeframe/instruments differ from selected paths")
    reader = _EvidenceReader(evidence_root, (trusted_registry._metadata, release_metadata))
    lineage = _lineage(row["lineage"], spec)
    _evidence(row["evidence"], reader, purpose=purpose, spec=spec, scope=scope, lineage=lineage)
    package = release_package_sha256(
        {key: val for key, val in row.items() if key not in _DECISIONS}
    )
    reviewer, reviewed_at = _decision(
        row["independent_review"], reader, purpose=purpose, spec=spec, package=package
    )
    principal, approved_at = _decision(
        row["principal_approval"], reader, purpose=purpose, spec=spec, package=package
    )
    if reviewer == principal:
        raise AuthorizationError("independent reviewer and Principal must be distinct people")
    if not reviewed_at <= approved_at <= known:
        raise AuthorizationError("decision times must satisfy reviewer <= Principal <= known_at")
    for item in reader.metadata:
        item.verify()
    token = object.__new__(ApprovedSnapshot)
    for key, value in {
        "spec": spec,
        "scope": scope,
        "purpose": purpose,
        "release_sha256": release_metadata.sha256,
        "registry_sha256": trusted_registry.registry_sha256,
        "known_at": known,
        "_metadata": tuple(reader.metadata),
        "_issuer": _ISSUER,
    }.items():
        object.__setattr__(token, key, value)
    return token
