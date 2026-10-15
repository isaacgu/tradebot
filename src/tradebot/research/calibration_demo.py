"""Fit and freeze the fixed invented fixture; this CLI never opens market data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from tradebot.data.storage import FileDigest, dataset_id, sha256_path
from tradebot.research.calibration_run import (
    calibration_implementation_identity,
    fit_replay,
    validate_frozen_replay,
)
from tradebot.research.demo import synthetic_records
from tradebot.research.engine import ReplayConfig
from tradebot.research.feed import ReplayBar
from tradebot.research.fitting import publish_calibration
from tradebot.research.registry import Registry
from tradebot.research.report import SPEC_SHA256, ReplayProvenance, canonical_bytes
from tradebot.research.splits import ChronologicalSplit, TimeWindow


@dataclass(frozen=True, slots=True)
class PublishedCalibrationDemo:
    """Content-addressed completed report and the measured registry attempt identity."""

    report_path: Path
    report_sha256: str
    experiment_id: str
    attempt_id: str


def _setup(
    instrument: str, git_sha: str
) -> tuple[tuple[ReplayBar, ...], ReplayConfig, ReplayProvenance, ChronologicalSplit]:
    records = tuple(row for row in synthetic_records() if row.bar.instrument == instrument)
    manifest = (
        FileDigest("invented-fixture.json", hashlib.sha256(canonical_bytes(records)).hexdigest()),
    )
    provenance = ReplayProvenance(dataset_id(manifest), "synthetic", manifest, git_sha)
    start = records[0].bar.ts_open
    split = ChronologicalSplit(
        TimeWindow(start, start + timedelta(minutes=75)),
        TimeWindow(start + timedelta(minutes=80), start + timedelta(minutes=150)),
        TimeWindow(start + timedelta(minutes=155), start + timedelta(minutes=160)),
        timedelta(minutes=5),
        timedelta(minutes=1),
    )
    return records, ReplayConfig((instrument,), 60), provenance, split


def run_synthetic_calibration(
    *, output_root: Path, attempt_id: str, git_sha: str = "UNCOMMITTED"
) -> PublishedCalibrationDemo:
    """Record an attempt, fit training, persist, load frozen fit, then validate.

    Fixture generation/hashing is not blind research; lockbox values are known
    invented data but never delivered to the strategy. All failed attempts remain
    in the local registry. No economic trial or gate approval is asserted.
    """
    inputs = tuple(_setup(name, git_sha) for name in ("Synthetic/EURUSD", "Synthetic/GBPUSD"))
    before = calibration_implementation_identity()
    runner_hash = sha256_path(Path(__file__))
    declaration = json.loads(
        canonical_bytes(
            {
                "schema_version": 1,
                "evidence_class": "ENGINEERING_ONLY",
                "name": "fixed-synthetic-forecast-calibration-v1",
                "spec_sha256": SPEC_SHA256,
                "implementation": before,
                "runner_sha256": runner_hash,
                "configurations": [config for _, config, _, _ in inputs],
                "provenance": [provenance for _, _, provenance, _ in inputs],
                "split_windows": [
                    {
                        "training": split.training,
                        "validation": split.validation,
                        "lockbox": split.lockbox,
                        "embargo_seconds": 300,
                        "label_horizon_seconds": 60,
                    }
                    for _, _, _, split in inputs
                ],
                "estimator": "10 / training mean absolute raw strength, then cap +/-20",
                "state_policy": "FRESH_STRATEGY_PER_PARTITION_FROZEN_TRAINING_SCALE",
                "financial_variants_tried": 0,
                "engineering_variants_declared": 1,
                "lockbox_policy": "DENY_STRATEGY_AND_FITTING_ACCESS",
            }
        )
    )
    registry = Registry(output_root / "registry.sqlite")
    experiment_id = registry.register(declaration)
    registry.start_attempt(
        experiment_id, attempt_id, metadata={"runner": "synthetic-calibration-v1"}
    )
    try:
        results: list[dict[str, object]] = []
        artifact_hashes: dict[str, str] = {}
        for records, config, provenance, split in inputs:
            fitted = fit_replay(records, config, provenance, split)
            path = publish_calibration(fitted.calibration, output_root=output_root / "models")
            # Actual frozen bytes must exist before any validation calculation.
            validation = validate_frozen_replay(
                records,
                config,
                provenance,
                split,
                calibration_path=path,
                calibration_sha256=fitted.calibration.sha256,
            )
            artifact_hashes[config.instruments[0].split("/")[1]] = fitted.calibration.sha256
            results.append(
                {
                    "instrument": config.instruments[0],
                    "provenance": provenance,
                    "input_bars": len(records),
                    "calibration_path": path.relative_to(output_root).as_posix(),
                    "calibration_sha256": fitted.calibration.sha256,
                    "fit": {
                        "calibration": json.loads(fitted.calibration.as_bytes()),
                        "decisions_by_status": fitted.decisions_by_status,
                        "source_quality_flags": fitted.source_quality_flags,
                        "release_sha256": fitted.release_sha256,
                        "registry_sha256": fitted.registry_sha256,
                    },
                    "validation": validation,
                }
            )
        if (
            before != calibration_implementation_identity()
            or sha256_path(Path(__file__)) != runner_hash
        ):
            raise RuntimeError("implementation changed during synthetic calibration")
        report = {
            "schema_version": 1,
            "evidence_class": "ENGINEERING_ONLY",
            "status": "COMPLETED",
            "experiment_id": experiment_id,
            "declaration": declaration,
            "results": results,
            "models_fitted": len(results),
            "model_source": "INVENTED_SYNTHETIC_FIXTURE_ONLY",
            "market_data_models_fitted": 0,
            "economic_evaluation": "NOT_PERFORMED",
            "training_release": "NOT_ASSERTED",
            "execution_enabled": False,
            "costs_modelled": False,
            "pnl_reported": False,
            "gate_approvals_claimed": [],
            "caveats": [
                "Synthetic calibration tests mechanics, not profitability or sufficient data.",
                "Small invented samples; measured train/validation counts are in results.",
                "Scale targets pre-cap mean absolute 10; post-cap mean is measured separately.",
                "All quality flags still suppress forecasts, including TS_RECV_IMPUTED.",
                "Fresh validation warmup; lockbox observations never reach strategy or fitter.",
                "Invented bytes are known and hashed; no untouched market lockbox is claimed.",
                "No sizing, broker financing, returns or statistical validation is performed.",
            ],
        }
        payload = canonical_bytes(report)
        report_hash = hashlib.sha256(payload).hexdigest()
        reports = output_root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        target = reports / report_hash
        with tempfile.TemporaryDirectory(dir=reports, prefix=".pending-") as temporary:
            pending = Path(temporary)
            with (pending / "report.json").open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                pending.rename(target)
            except OSError:
                existing = target / "report.json"
                if (
                    target.is_symlink()
                    or existing.is_symlink()
                    or not existing.is_file()
                    or sha256_path(existing) != report_hash
                ):
                    raise FileExistsError("immutable synthetic report differs") from None
        artifact_hashes["report"] = report_hash
        registry.finish_attempt(
            experiment_id, attempt_id, status="COMPLETED", artifacts=artifact_hashes
        )
        return PublishedCalibrationDemo(
            target / "report.json", report_hash, experiment_id, attempt_id
        )
    except Exception as exc:
        registry.finish_attempt(
            experiment_id, attempt_id, status="FAILED", error=f"{type(exc).__name__}: {exc}"
        )
        raise


def main(argv: list[str] | None = None) -> int:
    """Run only the fixed engineering fixture; no market-data or gate-bypass flags exist."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--git-sha", default="UNCOMMITTED")
    args = parser.parse_args(argv)
    result = run_synthetic_calibration(
        output_root=args.output_root, attempt_id=args.attempt_id, git_sha=args.git_sha
    )
    print(
        canonical_bytes(
            {
                "evidence_class": "ENGINEERING_ONLY",
                "report": str(result.report_path),
                "report_sha256": result.report_sha256,
                "market_data_models_fitted": 0,
                "execution_enabled": False,
            }
        ).decode(),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
