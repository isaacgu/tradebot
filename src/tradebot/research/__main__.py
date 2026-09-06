"""Run synthetic engineering fixtures only; this CLI never authorizes strategy training."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath

from tradebot.data.storage import sha256_path
from tradebot.demo import resolve_git_sha
from tradebot.research.demo import synthetic_setup
from tradebot.research.engine import ReplayConfig
from tradebot.research.feed import SnapshotBarFeed, load_snapshot_spec
from tradebot.research.report import SPEC_SHA256, ReplayProvenance, publish_replay

_TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


def main() -> None:
    """Publish engineering replay artifacts; no broker, tuning or financial simulation."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--synthetic", action="store_true", help="Run the fixed software fixture")
    mode.add_argument("--snapshot", type=Path, help="Explicit immutable Synthetic fixture manifest")
    parser.add_argument(
        "--root", type=Path, help="Snapshot manifest paths resolve under this directory"
    )
    parser.add_argument("--output-root", type=Path, default=Path("build/research/decision-replay"))
    parser.add_argument("--spec", type=Path, default=Path("docs/SPEC.md"))
    args = parser.parse_args()
    if sha256_path(args.spec) != SPEC_SHA256:
        parser.error("SPEC identity differs from the frozen v1.0 contract")
    git_sha = resolve_git_sha()
    if args.synthetic:
        if args.root is not None:
            parser.error("--root applies only to --snapshot")
        records, config, provenance = synthetic_setup(git_sha)
        published = publish_replay(records, config, provenance, output_root=args.output_root)
    else:
        if args.root is None:
            parser.error("--snapshot requires --root")
        spec = load_snapshot_spec(args.snapshot)
        if spec.venue != "Synthetic":
            parser.error(
                "real-data strategy replay is denied here; use a separately approved "
                "purpose-scoped consumer, not the synthetic engineering CLI"
            )
        if spec.timeframe not in _TIMEFRAMES:
            parser.error(f"unsupported timeframe: {spec.timeframe}")
        instruments = tuple(
            sorted({f"{spec.venue}/{PurePosixPath(item.path).parts[4]}" for item in spec.files})
        )
        config = ReplayConfig(instruments, _TIMEFRAMES[spec.timeframe])
        provenance = ReplayProvenance(
            spec.dataset_id, "immutable_clean_snapshot", spec.files, git_sha
        )
        feed = SnapshotBarFeed(args.root, spec)
        published = publish_replay(feed.records(), config, provenance, output_root=args.output_root)
    print(f"Engineering replay completed: {published.directory / 'report.json'}")
    print(f"Report SHA-256: {published.report_sha256}")
    print("Financial evaluation: NOT_PERFORMED; execution_enabled=false; pnl_reported=false")


if __name__ == "__main__":
    main()
