"""Freeze a real-data sample, import once, and rebuild twice for Gate-1 evidence.

This is offline and read-only with respect to MT5 and the acquisition work directory.
It never calls the terminal or rewrites completed checkpoints. Unknown calendars and
human reference checks are deliberately not upgraded to PASS by reproducible bytes.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import cast

from tradebot.core.config import _UniqueKeyLoader
from tradebot.data.acquisition_probe import parse_plan
from tradebot.data.corpus import ProbeArtifact, discover_probe_artifacts, import_raw_artifact
from tradebot.data.quality import QualityThresholds
from tradebot.data.storage import dataset_id, file_manifest, sha256_path

_ROOT = Path(__file__).resolve().parents[1]


def load_thresholds(path: Path) -> QualityThresholds:
    """Reject ambiguous/extra configuration rather than silently changing policy."""
    loader = _UniqueKeyLoader(path.read_text(encoding="utf-8"))
    try:
        payload = loader.get_single_data()
    finally:
        loader.dispose()
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "status", "thresholds"}:
        raise ValueError("quality config must contain schema_version, status and thresholds")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("unsupported quality schema")
    if payload["status"] != "prospective":
        raise ValueError("quality configuration must declare prospective policy")
    values = payload["thresholds"]
    expected = {
        "spread_multiplier",
        "price_sigma",
        "price_reversion_ticks",
        "gap_seconds",
        "fast_market_median_seconds",
        "rolling_horizon_seconds",
        "minimum_history",
    }
    if not isinstance(values, dict) or set(values) != expected:
        raise ValueError("quality thresholds must contain exactly the documented fields")
    for key in expected - {"spread_multiplier", "price_sigma"}:
        if type(values[key]) is not int or values[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("spread_multiplier", "price_sigma"):
        if not isinstance(values[key], str):
            raise ValueError(f"{key} must be a quoted decimal")
    return QualityThresholds(
        spread_multiplier=Decimal(values["spread_multiplier"]),
        price_sigma=Decimal(values["price_sigma"]),
        price_reversion_ticks=values["price_reversion_ticks"],
        gap_threshold=timedelta(seconds=values["gap_seconds"]),
        fast_market_median=timedelta(seconds=values["fast_market_median_seconds"]),
        rolling_horizon=timedelta(seconds=values["rolling_horizon_seconds"]),
        minimum_history=values["minimum_history"],
    )


def select_sample(
    artifacts: Sequence[ProbeArtifact], *, instrument: str, days: int, seed: int
) -> tuple[ProbeArtifact, ...]:
    """Sample distinct nonempty session dates, not duplicate dates across pairs."""
    if days < 1:
        raise ValueError("days must be positive")
    eligible = sorted(
        (a for a in artifacts if a.request.logical_symbol == instrument and a.expected_rows > 0),
        key=lambda a: a.request.chunk_id,
    )
    by_day: dict[date, ProbeArtifact] = {}
    for artifact in eligible:
        day = artifact.request.session_date
        if day in by_day:
            raise ValueError(f"overlapping completed windows for {instrument}/{day}")
        by_day[day] = artifact
    if len(by_day) < days:
        raise ValueError(
            f"need {days} distinct nonempty {instrument} days; only {len(by_day)} saved"
        )
    selected = random.Random(seed).sample(sorted(by_day), days)  # noqa: S311 -- sampling, not secrets
    return tuple(by_day[day] for day in sorted(selected))


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
    raise TypeError(f"unsupported evidence value {type(value).__name__}")


def _write_new(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, default=_json_default, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _code_hashes() -> dict[str, str]:
    # Hash the full corpus dependency subtree. Read-only monitoring modules do not
    # participate in raw/clean derivation and may evolve while this offline job runs.
    paths = [
        _ROOT / "scripts/build_gate1_corpus.py",
        _ROOT / "pyproject.toml",
        _ROOT / "uv.lock",
        _ROOT / "docs/SPEC.md",
        _ROOT / "src/tradebot/__init__.py",
        *sorted((_ROOT / "src/tradebot/core").rglob("*.py")),
        *sorted((_ROOT / "src/tradebot/data").rglob("*.py")),
    ]
    return {path.relative_to(_ROOT).as_posix(): sha256_path(path) for path in paths}


def _git_identity() -> dict[str, str]:
    result: dict[str, str] = {}
    for name, args in (("head", ["rev-parse", "HEAD"]), ("status", ["status", "--porcelain"])):
        completed = subprocess.run(  # noqa: S603 -- fixed read-only git arguments
            ["git", "--no-optional-locks", *args],  # noqa: S607 -- fixed read-only local Git
            cwd=_ROOT,
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
        result[name] = completed.stdout.strip()
    return result


def run(args: argparse.Namespace) -> dict[str, object]:
    """Return a measured evidence report; leave approval and unmeasured checks pending."""
    from tradebot.data.corpus import rebuild_from_raw

    started = time.monotonic()
    started_at = datetime.now(UTC)
    # Fail before heavy work if local Git provenance cannot be read. Actual pipeline
    # files are independently hashed before and after the two immutable rebuilds.
    initial_git_identity = _git_identity()
    thresholds = load_thresholds(args.quality_config)
    code_before = _code_hashes()
    plan_payload = json.loads(args.plan.read_text(encoding="utf-8"))
    if not isinstance(plan_payload, dict):
        raise ValueError("plan must be an object")
    plan = parse_plan(cast(Mapping[str, object], plan_payload))
    print("Validating saved checkpoints and source checksums...", flush=True)
    artifacts = discover_probe_artifacts(plan, work_root=args.work_dir)
    sample = select_sample(artifacts, instrument=args.instrument, days=args.days, seed=args.seed)
    selection = {
        "plan_hash": plan.plan_hash,
        "seed": args.seed,
        "instrument": args.instrument,
        "days": args.days,
        "timeframes": list(args.timeframes),
        "initial_git": initial_git_identity,
        "code_hashes": code_before,
        "quality_config_sha256": sha256_path(args.quality_config),
        "chunks": [
            {
                "chunk_id": a.request.chunk_id,
                "rows": a.expected_rows,
                "checkpoint_sha256": sha256_path(a.checkpoint_path),
                "source_sha256": a.compressed_sha256,
                "semantic_sha256": a.semantic_sha256,
            }
            for a in sample
        ],
    }
    _write_new(args.output_dir / "selection.json", selection)
    imported = []
    raw_root = args.output_dir / "snapshot"
    for index, artifact in enumerate(sample, 1):
        print(f"Import {index}/{len(sample)}: {artifact.request.chunk_id}", flush=True)
        imported.append(
            import_raw_artifact(artifact, data_root=raw_root, batch_size=args.batch_size)
        )
    raw_paths = tuple(path for item in imported for path in item.files)
    raw_before = file_manifest(raw_paths, relative_to=raw_root)
    results = []
    roots = (args.output_dir / "first", args.output_dir / "second")
    for index, output_root in enumerate(roots, 1):
        print(
            f"Rebuild {index}/2 from the same {len(raw_paths)} immutable raw files...", flush=True
        )
        results.append(
            rebuild_from_raw(
                tuple(imported),
                data_root=output_root,
                venue=args.venue,
                timeframes=tuple(args.timeframes),
                thresholds=thresholds,
                batch_size=args.batch_size,
            )
        )
    raw_after = file_manifest(raw_paths, relative_to=raw_root)
    first, second = results
    # Compare both identities and complete file manifests, including bytes on disk.
    first_manifest = file_manifest(
        [*first.clean_tick_files, *first.clean_bar_files], relative_to=roots[0]
    )
    second_manifest = file_manifest(
        [*second.clean_tick_files, *second.clean_bar_files], relative_to=roots[1]
    )
    unchanged_code = code_before == _code_hashes()
    identical = first_manifest == second_manifest and bool(first.clean_bar_files)
    raw_unchanged = raw_before == raw_after
    report: dict[str, object] = {
        "schema_version": 1,
        "started_at": started_at,
        "finished_at": datetime.now(UTC),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "scope": "Gate-1 30-day reproducibility" if args.days >= 30 else "engineering smoke only",
        "gate_approved": False,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pyarrow": version("pyarrow"),
            "tzdata": version("tzdata"),
        },
        "git": initial_git_identity,
        "git_snapshot_timing": "before_source_discovery",
        "selection": selection,
        "selected_primary_ticks": sum(a.expected_rows for a in sample),
        "corpus_id": first.corpus_id,
        "dataset_id": dataset_id(first_manifest),
        "raw_manifest": [asdict(item) for item in raw_before],
        "clean_manifest": [asdict(item) for item in first_manifest],
        "raw_files_unchanged": raw_unchanged,
        "implementation_unchanged": unchanged_code,
        "independent_rebuilds_byte_identical": identical,
        "reproducibility_status": "PASSED"
        if (identical and raw_unchanged and unchanged_code and args.days >= 30)
        else "NOT_SATISFIED",
        "quality": [asdict(summary) for summary in first.quality],
        "bar_rows_by_timeframe": dict(first.bar_rows_by_timeframe),
        "liquid_hours_flagged_bar_criterion": "INDETERMINATE: no approved dated liquidity calendar",
        "five_hand_verified_reference_checks": "PENDING",
        "committed_sha_ci": "PENDING",
        "independent_human_review": "PENDING",
        "principal_approval": "PENDING",
    }
    _write_new(args.output_dir / "report.json", report)
    report_hash = sha256_path(args.output_dir / "report.json")
    _write_new(args.output_dir / "report.sha256.json", {"report.json": report_hash})
    print(
        json.dumps(
            {
                "report": str(args.output_dir / "report.json"),
                "sha256": report_hash,
                "status": report["reproducibility_status"],
            }
        ),
        flush=True,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan", type=Path, default=Path("configs/probes/fbs_tick_continuity_v1.json")
    )
    parser.add_argument("--work-dir", type=Path, default=Path("build/fbs-tick-continuity-v1"))
    parser.add_argument("--quality-config", type=Path, default=Path("configs/data_quality.yaml"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--instrument", default="EURUSD")
    parser.add_argument("--venue", default="FBS")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--batch-size", type=int, default=65536)
    parser.add_argument("--timeframes", nargs="+", choices=("1m", "1d"), default=["1m"])
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("output-dir must not already exist; evidence runs are append-only")
    if args.days < 1 or args.batch_size < 1:
        parser.error("days and batch-size must be positive")
    if "1m" not in args.timeframes or len(args.timeframes) != len(set(args.timeframes)):
        parser.error("timeframes must include 1m and must not contain duplicates")
    try:
        run(args)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Corpus evidence failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
