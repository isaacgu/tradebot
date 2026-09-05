"""Preregister and exercise research controls using only the fixed invented fixture.

This is not a backtest. The fixture is already known when its declaration is built.
No CLI option admits market data, changes parameters, or opens the lockbox.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import tempfile
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tradebot.data.storage import FileDigest, sha256_path
from tradebot.research.demo import synthetic_setup
from tradebot.research.engine import ReplayConfig, iter_decisions
from tradebot.research.feed import ReplayBar
from tradebot.research.registry import Registry
from tradebot.research.report import (
    SPEC_SHA256,
    canonical_bytes,
    implementation_identity,
    json_value,
)
from tradebot.research.splits import ChronologicalSplit, TimeWindow, select_partition


def _split() -> ChronologicalSplit:
    start = datetime(2024, 1, 8, 10, tzinfo=UTC)

    def window(first: int, last: int) -> TimeWindow:
        return TimeWindow(start + timedelta(minutes=first), start + timedelta(minutes=last))

    return ChronologicalSplit(
        training=window(0, 75),
        validation=window(80, 150),
        lockbox=window(155, 160),
        embargo=timedelta(minutes=5),
        label_horizon=timedelta(minutes=1),
    )


def _identity() -> tuple[FileDigest, ...]:
    identity = list(implementation_identity())
    for name in (
        "tradebot.research.registry",
        "tradebot.research.splits",
        "tradebot.research.experiment_demo",
    ):
        module = importlib.util.find_spec(name)
        if module is None or module.origin is None:
            raise RuntimeError(f"missing implementation source: {name}")
        identity.append(FileDigest(name, sha256_path(Path(module.origin))))
    return tuple(sorted(identity, key=lambda entry: entry.path))


def synthetic_declaration(git_sha: str = "UNCOMMITTED") -> dict[str, object]:
    """Describe one fixed engineering variant, not a preregistered economic hypothesis."""
    _, config, provenance = synthetic_setup(git_sha)
    split = _split()
    declaration: dict[str, object] = {
        "schema_version": 1,
        "evidence_class": "ENGINEERING_ONLY",
        "name": "fixed-synthetic-research-controls-v1",
        "hypothesis": "Declarations, attempt accounting and split controls are replayable.",
        "acceptance": "Repeat decision bytes; retain failures; exclude lockbox from strategy.",
        "economic_hypothesis": "NOT_PROPOSED",
        "financial_variants_tried": 0,
        "engineering_variants_declared": 1,
        "spec_sha256": SPEC_SHA256,
        "config": json_value(config),
        "config_sha256": hashlib.sha256(canonical_bytes(config)).hexdigest(),
        "provenance": json_value(provenance),
        "implementation": json_value(_identity()),
        "python": platform.python_version(),
        "platform": platform.system(),
        "split": {
            name: json_value(getattr(split, name)) for name in ("training", "validation", "lockbox")
        },
        "embargo_seconds": 300,
        "label_horizon_seconds": 60,
        "state_policy": "FRESH_STRATEGY_PER_PARTITION_NO_FITTING",
        "lockbox_policy": "DENY_EXECUTION",
        "fixture_access": "KNOWN_SYNTHETIC_FIXTURE_HASHED_IN_FULL_NOT_BLIND_MARKET_RESEARCH",
    }
    # Round-trip to JSON-native values before entering the generic registry.
    result = json.loads(canonical_bytes(declaration))
    if not isinstance(result, dict):
        raise TypeError("declaration must encode an object")
    return result


def _durable_write(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _publish(
    experiment_id: str,
    partition: str,
    config: ReplayConfig,
    git_sha: str,
    output_root: Path,
    declaration: dict[str, object],
) -> dict[str, str]:
    records, actual_config, provenance = synthetic_setup(git_sha)
    if actual_config != config or json_value(provenance) != declaration["provenance"]:
        raise ValueError("fixture identity differs from preregistration")
    split = _split()
    routing: Counter[str] = Counter()

    def tracked() -> Iterator[ReplayBar]:
        for record in records:
            bar = record.bar
            decision = max(bar.ts_event, bar.ts_recv, bar.available_at)
            classification = split.classify(bar.ts_open, decision, decision + split.label_horizon)
            routing[classification] += 1
            yield record

    selected = select_partition(tracked(), split, partition)
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    instrument_counts: Counter[str] = Counter()
    trace_hash = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=output_root) as temporary:
        pending = Path(temporary)
        with (pending / "decisions.jsonl").open("xb") as stream:
            for decision in iter_decisions(selected, config):
                payload = canonical_bytes(decision)
                stream.write(payload)
                trace_hash.update(payload)
                counts[decision.status] += 1
                instrument_counts[decision.instrument] += 1
            stream.flush()
            os.fsync(stream.fileno())
        if json_value(_identity()) != declaration["implementation"]:
            raise RuntimeError("implementation changed during the attempt")
        report = {
            "schema_version": 1,
            "evidence_class": "ENGINEERING_ONLY",
            "experiment_id": experiment_id,
            "partition": partition,
            "status": "COMPLETED",
            "declaration_sha256": experiment_id,
            "source_kind": "synthetic",
            "input_bars": sum(routing.values()),
            "routing_by_classification": dict(sorted(routing.items())),
            "bars_processed": sum(counts.values()),
            "decisions_by_status": dict(sorted(counts.items())),
            "bars_by_instrument": dict(sorted(instrument_counts.items())),
            "decisions_sha256": trace_hash.hexdigest(),
            "lockbox_decisions": 0,
            "economic_evaluation": "NOT_PERFORMED",
            "data_acceptance": "NOT_ASSERTED",
            "execution_enabled": False,
            "orders_created": 0,
            "costs_modelled": False,
            "pnl_reported": False,
            "gate_approvals_claimed": [],
            "caveats": [
                "Fixed invented fixture; no market data or economic strategy selection.",
                "All fixture bytes are generated and hashed; this is not an untouched lockbox.",
                "Lockbox bars are not delivered to the feature/strategy pipeline.",
                "Each partition warms up independently; no parameters are fitted.",
                "Label endpoints exercise purging only; no future labels or returns are built.",
                "Registry is local evidence, not external timestamping or tamper-proof storage.",
                "Costs, fills, statistical validation and phase-gate approval remain absent.",
            ],
        }
        report_bytes = canonical_bytes(report)
        report_hash = hashlib.sha256(report_bytes).hexdigest()
        _durable_write(pending / "report.json", report_bytes)
        target = output_root / report_hash
        expected = {"report.json": report_hash, "decisions.jsonl": trace_hash.hexdigest()}
        try:
            pending.rename(target)
        except OSError:
            if not target.is_dir() or target.is_symlink():
                raise
            for name, digest in expected.items():
                path = target / name
                if path.is_symlink() or not path.is_file() or sha256_path(path) != digest:
                    message = f"immutable experiment artifact differs: {name}"
                    raise FileExistsError(message) from None
        return expected


def run_synthetic_attempt(
    registry: Registry,
    experiment_id: str,
    attempt_id: str,
    *,
    partition: str,
    output_root: Path,
    git_sha: str = "UNCOMMITTED",
) -> dict[str, str]:
    """Persist STARTED before execution; persist failures and never unlock the lockbox.

    Abrupt termination can leave STARTED visible as incomplete, never COMPLETED.
    Callers must choose a fresh attempt ID for a retry; failures are not overwritten.
    """
    registry.start_attempt(
        experiment_id,
        attempt_id,
        metadata={"partition": partition, "runner": "fixed-synthetic-v1"},
    )
    try:
        if partition not in {"training", "validation"}:
            raise ValueError("only training and validation execution is allowed; lockbox is denied")
        declaration = synthetic_declaration(git_sha)
        stored = registry.audit(experiment_id)["declaration"]
        if stored != declaration:
            raise ValueError("current implementation/configuration differs from preregistration")
        config = ReplayConfig(("Synthetic/EURUSD", "Synthetic/GBPUSD"), 60)
        artifacts = _publish(experiment_id, partition, config, git_sha, output_root, declaration)
        registry.finish_attempt(experiment_id, attempt_id, status="COMPLETED", artifacts=artifacts)
        return artifacts
    except Exception as exc:
        registry.finish_attempt(
            experiment_id, attempt_id, status="FAILED", error=f"{type(exc).__name__}: {exc}"
        )
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--attempt-prefix", required=True)
    parser.add_argument("--git-sha", default="UNCOMMITTED")
    args = parser.parse_args(argv)
    registry = Registry(args.output_root / "registry.sqlite")
    experiment_id = registry.register(synthetic_declaration(args.git_sha))
    artifacts: dict[str, object] = {}
    for partition in ("training", "validation"):
        artifacts[partition] = run_synthetic_attempt(
            registry,
            experiment_id,
            f"{args.attempt_prefix}.{partition}",
            partition=partition,
            output_root=args.output_root / "artifacts",
            git_sha=args.git_sha,
        )
    print(
        canonical_bytes(
            {
                "evidence_class": "ENGINEERING_ONLY",
                "audit": registry.audit(experiment_id),
                "artifacts": artifacts,
            }
        ).decode(),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
