"""Publish additive source-data quarantine evidence from an independently pinned report.

Offline only: no MetaTrader terminal, broker request, trading, or raw-data mutation.
Exit zero means evidence triage completed; all strategy/execution eligibility is false.
The output and checksum are copy-once; choose a new filename for another evidence set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from tradebot.data.admission import AdmissionEvidenceError, prepare_admission


def _check_publication_target(target: Path, protected_root: Path) -> None:
    """Check again after evidence scanning, including ancestors and checksum aliases."""
    if target.resolve() != target or target.is_symlink():
        raise AdmissionEvidenceError("Admission output must use a canonical path without links.")
    if target.is_relative_to(protected_root):
        raise AdmissionEvidenceError(
            "Admission output must be outside the preserved acquisition work directory."
        )
    if target.exists():
        raise AdmissionEvidenceError("Refusing to overwrite an existing admission artifact.")


def main(argv: list[str] | None = None) -> int:
    """Verify all inputs before publishing an immutable manifest and checksum."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--expected-candidate-sha256", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output: Path = args.output.absolute()
    sidecar = output.with_suffix(output.suffix + ".sha256")
    protected_root = args.work_dir.absolute()
    try:
        _check_publication_target(output, protected_root)
        _check_publication_target(sidecar, protected_root)
        report = prepare_admission(
            candidate=args.candidate,
            expected_candidate_sha256=args.expected_candidate_sha256,
            plan_path=args.plan,
            work_root=args.work_dir,
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"Admission evidence rejected: {exc}\n")
    encoded = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    digest = hashlib.sha256(encoded).hexdigest()
    try:
        # Evidence verification can take minutes. A path checked before that
        # scan must not authorize writes through a subsequently replaced parent.
        _check_publication_target(output, protected_root)
        _check_publication_target(sidecar, protected_root)
        output.parent.mkdir(parents=True, exist_ok=True)
        _check_publication_target(output, protected_root)
        _check_publication_target(sidecar, protected_root)
        with output.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _check_publication_target(sidecar, protected_root)
        with sidecar.open("x", encoding="ascii", newline="\n") as stream:
            stream.write(f"{digest}  {output.name}\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Admission publication rejected: {exc}\n")
    summary = report["summary"]
    print(
        f"Prepared {output}: {summary['quarantined_partitions']} quarantined, "
        f"{summary['qa_only_partitions']} QA_ONLY; strategy/execution eligibility false."
    )
    print(f"SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
