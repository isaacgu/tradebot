"""Publish a reproducible, immutable replay summary and streamed decision trace."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from tradebot.data.storage import FileDigest, dataset_id, sha256_path
from tradebot.research.authorization import AuthorizationError, ResearchPurpose
from tradebot.research.engine import ReplayConfig, iter_decisions, validate_replay_request
from tradebot.research.feed import ReplayBar
from tradebot.research.guarded import ApprovedSnapshotStream

SPEC_SHA256 = "dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37"
_IMPLEMENTATION_MODULES = (
    "tradebot",
    "tradebot.core",
    "tradebot.core.bus",
    "tradebot.core.clock",
    "tradebot.core.errors",
    "tradebot.core.ports",
    "tradebot.core.timestamps",
    "tradebot.core.types",
    "tradebot.data",
    "tradebot.data.storage",
    "tradebot.features",
    "tradebot.features.causal",
    "tradebot.strategies",
    "tradebot.strategies.momentum",
    "tradebot.research",
    "tradebot.research.authorization",
    "tradebot.research.guarded",
    "tradebot.research.engine",
    "tradebot.research.feed",
    "tradebot.research.report",
    "tradebot.research.demo",
    "tradebot.research.__main__",
)


def json_value(value: object) -> object:
    """Convert audit dataclasses to JSON without rounding exact prices or timestamps."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported audit value: {type(value).__name__}")


def canonical_bytes(value: object) -> bytes:
    """Encode canonical UTF-8 JSON with finite numbers and a single LF terminator."""
    return (
        json.dumps(json_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def implementation_identity() -> tuple[FileDigest, ...]:
    """Hash explicitly named implementation modules without absolute-path dependence."""
    result: list[FileDigest] = []
    for name in _IMPLEMENTATION_MODULES:
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin is None:
            raise RuntimeError(f"implementation module has no source: {name}")
        result.append(FileDigest(name, sha256_path(Path(spec.origin))))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ReplayProvenance:
    """Input identity; callers must supply the manifest actually consumed by the feed."""

    dataset_id: str
    source_kind: str
    source_manifest: tuple[FileDigest, ...]
    git_sha: str

    def __post_init__(self) -> None:
        if type(self.source_manifest) is not tuple or any(
            not isinstance(item, FileDigest) for item in self.source_manifest
        ):
            raise TypeError("source_manifest must be a tuple of FileDigest values")
        if not re.fullmatch(r"[0-9a-f]{64}", self.dataset_id):
            raise ValueError("dataset_id must be a SHA-256 digest")
        if self.source_kind not in {"synthetic", "immutable_clean_snapshot"}:
            raise ValueError("unsupported source kind")
        if self.git_sha != "UNCOMMITTED" and not re.fullmatch(r"[0-9a-f]{40}", self.git_sha):
            raise ValueError("git_sha must be a full commit or UNCOMMITTED")
        if not self.source_manifest:
            raise ValueError("source manifest is required")
        if dataset_id(self.source_manifest) != self.dataset_id:
            raise ValueError("dataset_id differs from the source manifest")


@dataclass(frozen=True, slots=True)
class PublishedReplay:
    """Paths of a complete content-addressed run, all rooted under output_root."""

    run_id: str
    directory: Path
    report_sha256: str
    decisions_sha256: str


def _durable_write(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _verify_existing(directory: Path, expected: Mapping[str, str]) -> None:
    if not directory.is_dir() or directory.is_symlink():
        raise FileExistsError("replay output already exists and is not a run directory")
    for name, digest in expected.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or sha256_path(path) != digest:
            raise FileExistsError(f"existing immutable run differs: {name}")


def publish_replay(
    records: Iterable[ReplayBar],
    config: ReplayConfig,
    provenance: ReplayProvenance,
    *,
    output_root: Path,
    purpose: ResearchPurpose | None = None,
) -> PublishedReplay:
    """Stream an engineering replay, publish after complete validation, then update latest.

    Memory is bounded by each strategy's feature history and one latest decision
    per configured instrument. No run is published if input or execution fails.
    """
    validate_replay_request(records, config, purpose=purpose)
    if isinstance(records, ApprovedSnapshotStream) and (
        provenance.source_kind != "immutable_clean_snapshot"
        or provenance.dataset_id != records.spec.dataset_id
        or provenance.source_manifest != records.spec.files
    ):
        raise AuthorizationError("provenance differs from the guard-owned selected snapshot")
    before = implementation_identity()
    runtime = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.system(),
        "pyarrow": importlib.metadata.version("pyarrow"),
        "tzdata": importlib.metadata.version("tzdata"),
    }
    identity = {
        "spec_version": "1.0",
        "spec_sha256": SPEC_SHA256,
        "config": asdict(config),
        "config_sha256": hashlib.sha256(canonical_bytes(config)).hexdigest(),
        "implementation": before,
        "implementation_sha256": hashlib.sha256(canonical_bytes(before)).hexdigest(),
        "runtime": runtime,
        "randomness": "NONE",
        "provenance": provenance,
        "authorized_use": (
            None
            if not isinstance(records, ApprovedSnapshotStream)
            else {
                "purpose": records.purpose.value,
                "release_sha256": records.release_sha256,
                "registry_sha256": records.registry_sha256,
                "scope": asdict(records.scope),
                "known_at": records.known_at,
                "authority": "OPERATOR_PINNED_RELEASE_NOT_IDENTITY_AUTH",
                "human_identities_authenticated": False,
            }
        ),
    }
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    instrument_counts: dict[str, Counter[str]] = {name: Counter() for name in config.instruments}
    flags: dict[str, Counter[str]] = {}
    latest: dict[str, object] = {}
    trace_digest = hashlib.sha256()
    count = 0
    first_available: datetime | None = None
    last_available: datetime | None = None
    with tempfile.TemporaryDirectory(dir=output_root, prefix=".pending-") as temporary:
        staging = Path(temporary)
        trace_path = staging / "decisions.jsonl"
        with trace_path.open("xb") as trace:
            for decision in iter_decisions(records, config, purpose=purpose):
                payload = canonical_bytes(decision)
                trace.write(payload)
                trace_digest.update(payload)
                count += 1
                counts[decision.status] += 1
                instrument_counts[decision.instrument][decision.status] += 1
                flags.setdefault(decision.source, Counter()).update(decision.quality_flags)
                latest[decision.instrument] = json_value(decision)
                if first_available is None:
                    first_available = decision.available_at
                last_available = decision.available_at
            trace.flush()
            os.fsync(trace.fileno())
        if implementation_identity() != before:
            raise RuntimeError("implementation changed while the replay was running")
        if isinstance(records, ApprovedSnapshotStream):
            records.verify_completed()
        trace_sha256 = trace_digest.hexdigest()
        run_id = hashlib.sha256(canonical_bytes((identity, trace_sha256))).hexdigest()
        caveats = [
            "Engineering decision replay only; no economic strategy evaluation has occurred.",
            "Forecast scale is uncalibrated and is not a probability or position size.",
            "Costs, fills, PnL, allocation, execution and statistical validation are absent.",
            "Data or phase-gate acceptance is not asserted by successful replay.",
            "No strategy fitting occurs; authorized input use is not proof of completed training.",
            "Horizon lengths count observed bars; no missing interval is forward-filled.",
            "Calendar, macro, cross-asset and tick microstructure features are not connected.",
            "All quality-flagged and missing-spread bars suppress forecasts and reset warmup.",
        ]
        for source, source_flags in sorted(flags.items()):
            if "TS_RECV_IMPUTED" in source_flags:
                caveats.append(
                    f"{source}: TS_RECV_IMPUTED observations were withheld from forecasts."
                )
        report = {
            "schema_version": 1,
            "evidence_class": "engineering-decision-replay-only",
            "run_id": run_id,
            "status": "COMPLETED",
            "identity": identity,
            "execution_enabled": False,
            "orders_created": 0,
            "costs_modelled": False,
            "pnl_reported": False,
            "economic_evaluation": "NOT_PERFORMED",
            "data_acceptance": "NOT_ASSERTED",
            "gate_approvals_claimed": [],
            "forecast_scaling": "UNCALIBRATED",
            "bars_processed": count,
            "decisions_by_status": dict(sorted(counts.items())),
            "instruments": {
                name: dict(sorted(stats.items())) for name, stats in instrument_counts.items()
            },
            "source_quality_flags": {
                name: dict(sorted(stats.items())) for name, stats in sorted(flags.items())
            },
            "first_available_at": first_available,
            "last_available_at": last_available,
            "latest_decisions": latest,
            "trace": {"path": "decisions.jsonl", "sha256": trace_sha256, "records": count},
            "caveats": caveats,
        }
        report_bytes = canonical_bytes(report)
        report_hash = hashlib.sha256(report_bytes).hexdigest()
        _durable_write(staging / "report.json", report_bytes)
        hashes = {"decisions.jsonl": trace_sha256, "report.json": report_hash}
        manifest_bytes = canonical_bytes({"schema_version": 1, "run_id": run_id, "files": hashes})
        _durable_write(staging / "manifest.json", manifest_bytes)
        hashes["manifest.json"] = hashlib.sha256(manifest_bytes).hexdigest()
        final = output_root / run_id
        if final.exists():
            _verify_existing(final, hashes)
        else:
            try:
                os.rename(staging, final)
            except FileExistsError:
                _verify_existing(final, hashes)
        # latest.json is explicitly mutable discovery metadata, never gate evidence.
        pointer = canonical_bytes(
            {
                "schema_version": 1,
                "run_id": run_id,
                "report": f"{run_id}/report.json",
                "sha256": report_hash,
                "evidence_class": "engineering-decision-replay-only",
            }
        )
        with tempfile.NamedTemporaryFile(
            dir=output_root, prefix=".latest-", delete=False
        ) as stream:
            pointer_path = Path(stream.name)
            try:
                stream.write(pointer)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                stream.close()
                pointer_path.unlink(missing_ok=True)
                raise
        try:
            os.replace(pointer_path, output_root / "latest.json")
        finally:
            pointer_path.unlink(missing_ok=True)
    return PublishedReplay(run_id, final, report_hash, trace_sha256)
