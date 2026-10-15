"""Evaluate the SPEC 4.6 reference-month quality criterion without approving it.

This offline command reads immutable clean one-minute bars, optional retrospective
tick annotations, an ``ExpectedLiquidityCalendar`` snapshot, and a separately
hash-bound counted-flag policy/approval record.  It never reads MT5, changes source
data, edits a calendar, or turns a successful calculation into Gate approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from tradebot.core.timestamps import require_utc
from tradebot.data.calendar import ExpectedLiquidityCalendar
from tradebot.data.reference_acceptance import (
    AcceptanceStatus,
    FileEvidence,
    ReferenceAcceptanceError,
    ReferenceScope,
    evaluate_reference_month,
    read_approval_binding,
    read_clean_bar_files,
    read_clean_tick_files,
    read_policy,
    verify_producer_inventory,
)
from tradebot.data.storage import sha256_path

_ROOT = Path(__file__).resolve().parents[1]


def _utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReferenceAcceptanceError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    return require_utc(parsed, field=field)


def _inside_repository(path: Path, *, label: str, must_exist: bool = True) -> Path:
    resolved = (path if path.is_absolute() else _ROOT / path).resolve()
    if not resolved.is_relative_to(_ROOT.resolve()):
        raise ReferenceAcceptanceError(f"{label} must stay inside the repository")
    if must_exist and not resolved.exists():
        raise ReferenceAcceptanceError(f"{label} does not exist: {path}")
    return resolved


def _parquet_files(root: Path, *, label: str) -> tuple[Path, ...]:
    resolved = _inside_repository(root, label=label)
    if resolved.is_symlink():
        raise ReferenceAcceptanceError(f"{label} cannot be a symlink")
    paths = (resolved,) if resolved.is_file() else tuple(sorted(resolved.rglob("*.parquet")))
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(resolved):
            raise ReferenceAcceptanceError(f"{label} contains an unsafe file reference")
    return paths


def _file_evidence(rows: Sequence[FileEvidence]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        path = Path(row.path).resolve()
        result.append(
            {
                "path": path.relative_to(_ROOT.resolve()).as_posix(),
                "sha256": row.sha256,
                "rows": row.rows,
            }
        )
    return result


def build_report(
    *,
    scope: ReferenceScope,
    bar_root: Path,
    tick_root: Path | None,
    policy_path: Path,
    calendar_path: Path | None,
    approval_binding_path: Path | None,
    producer_report_path: Path | None,
    producer_sidecar_path: Path | None,
    expected_producer_report_sha256: str | None,
    known_at: datetime,
    generated_at: datetime,
) -> dict[str, object]:
    """Build one inspectable readiness report from frozen inputs."""

    known_at = require_utc(known_at, field="known_at")
    generated_at = require_utc(generated_at, field="generated_at")
    if known_at > generated_at:
        raise ReferenceAcceptanceError("known_at cannot postdate generated_at")
    if generated_at > datetime.now(UTC):
        raise ReferenceAcceptanceError("generated_at cannot be in the future")
    policy_file = _inside_repository(policy_path, label="policy")
    policy = read_policy(policy_file)
    if policy.scope != scope:
        raise ReferenceAcceptanceError("command scope does not match the exact policy scope")

    calendar: ExpectedLiquidityCalendar | None = None
    calendar_sha256: str | None = None
    calendar_file: Path | None = None
    if calendar_path is not None:
        calendar_file = _inside_repository(calendar_path, label="calendar")
        calendar_sha256 = sha256_path(calendar_file)
        calendar = ExpectedLiquidityCalendar.read(calendar_file)

    approval = None
    approval_file: Path | None = None
    if approval_binding_path is not None:
        approval_file = _inside_repository(approval_binding_path, label="approval binding")
        approval = read_approval_binding(approval_file, repository=_ROOT)

    bar_paths = _parquet_files(bar_root, label="bar root")
    loaded_bars = read_clean_bar_files(bar_paths, scope=scope)
    loaded_ticks = None
    tick_paths: tuple[Path, ...] = ()
    if tick_root is not None:
        tick_paths = _parquet_files(tick_root, label="tick root")
        if tick_paths:
            loaded_ticks = read_clean_tick_files(tick_paths, scope=scope)

    if len(loaded_bars.corpus_ids) > 1:
        raise ReferenceAcceptanceError("bar files mix multiple corpus identities")
    if loaded_ticks is not None and len(loaded_ticks.corpus_ids) > 1:
        raise ReferenceAcceptanceError("tick files mix multiple corpus identities")
    if (
        loaded_ticks is not None
        and loaded_bars.corpus_ids
        and loaded_ticks.corpus_ids != loaded_bars.corpus_ids
    ):
        raise ReferenceAcceptanceError("bar and retrospective tick evidence use different corpora")

    producer_inventory = None
    producer_inventory_errors: list[str] = []
    producer_report_file: Path | None = None
    producer_sidecar_file: Path | None = None
    if (
        producer_report_path is None
        or producer_sidecar_path is None
        or expected_producer_report_sha256 is None
    ):
        producer_inventory_errors.append(
            "producer report, sidecar, and independently supplied expected report hash are required"
        )
    elif loaded_ticks is None:
        producer_inventory_errors.append(
            "producer inventory cannot be verified without exact-scope clean tick files"
        )
    else:
        producer_report_file = _inside_repository(producer_report_path, label="producer report")
        producer_sidecar_file = _inside_repository(
            producer_sidecar_path, label="producer report sidecar"
        )
        try:
            producer_inventory = verify_producer_inventory(
                report_path=producer_report_file,
                sidecar_path=producer_sidecar_file,
                expected_report_sha256=expected_producer_report_sha256,
                scope=scope,
                bars=loaded_bars,
                ticks=loaded_ticks,
            )
        except ReferenceAcceptanceError as exc:
            producer_inventory_errors.append(str(exc))

    result = evaluate_reference_month(
        scope=scope,
        bars=loaded_bars.bars,
        calendar=calendar,
        calendar_sha256=calendar_sha256,
        policy=policy,
        known_at=known_at,
        approval=approval,
        producer_inventory=producer_inventory,
        causal_tick_flags_by_minute=(
            None if loaded_ticks is None else loaded_ticks.causal_flags_by_minute
        ),
        retrospective_flags_by_minute=(
            None if loaded_ticks is None else loaded_ticks.flags_by_minute
        ),
        tick_covered_minutes=(None if loaded_ticks is None else loaded_ticks.covered_minutes),
        outside_canonical_session_causal_flags_by_utc_minute=(
            None
            if loaded_ticks is None
            else loaded_ticks.outside_canonical_session_causal_flags_by_utc_minute
        ),
        outside_canonical_session_retrospective_flags_by_utc_minute=(
            None
            if loaded_ticks is None
            else loaded_ticks.outside_canonical_session_retrospective_flags_by_utc_minute
        ),
        outside_canonical_session_covered_utc_minutes=(
            None
            if loaded_ticks is None
            else loaded_ticks.outside_canonical_session_covered_utc_minutes
        ),
        outside_canonical_session_tick_rows_in_utc_month=(
            0
            if loaded_ticks is None
            else loaded_ticks.outside_canonical_session_tick_rows_in_utc_month
        ),
    )
    code_paths = (
        "src/tradebot/data/reference_acceptance.py",
        "scripts/evaluate_reference_month.py",
        "docs/SPEC.md",
    )
    inputs: dict[str, object] = {
        "policy": {
            "path": policy_file.relative_to(_ROOT).as_posix(),
            "sha256": policy.sha256,
            "status": policy.status,
        },
        "calendar": None
        if calendar_file is None
        else {
            "path": calendar_file.relative_to(_ROOT).as_posix(),
            "sha256": calendar_sha256,
        },
        "approval_binding": None
        if approval_file is None
        else {
            "path": approval_file.relative_to(_ROOT).as_posix(),
            "sha256": sha256_path(approval_file),
        },
        "producer_inventory": {
            "independent_expected_report_sha256": expected_producer_report_sha256,
            "report": None
            if producer_report_file is None
            else {
                "path": producer_report_file.relative_to(_ROOT).as_posix(),
                "sha256": sha256_path(producer_report_file),
            },
            "sidecar": None
            if producer_sidecar_file is None
            else {
                "path": producer_sidecar_file.relative_to(_ROOT).as_posix(),
                "sha256": sha256_path(producer_sidecar_file),
            },
            "verified": producer_inventory is not None,
            "validation_errors": producer_inventory_errors,
        },
        "clean_bar_files": _file_evidence(loaded_bars.files),
        "clean_bar_corpus_ids": list(loaded_bars.corpus_ids),
        "clean_tick_files": [] if loaded_ticks is None else _file_evidence(loaded_ticks.files),
        "clean_tick_corpus_ids": [] if loaded_ticks is None else list(loaded_ticks.corpus_ids),
        "retrospective_tick_files": []
        if loaded_ticks is None
        else _file_evidence(loaded_ticks.files),
        "retrospective_tick_corpus_ids": []
        if loaded_ticks is None
        else list(loaded_ticks.corpus_ids),
    }
    return {
        "schema_version": 1,
        "evidence_class": "REFERENCE_MONTH_ACCEPTANCE_EVALUATION",
        "generated_at_utc": generated_at.isoformat(),
        "status": result.status.value,
        "gate_approved": False,
        "training_enabled": False,
        "result": result.to_dict(),
        "inputs": inputs,
        "code_and_spec_sha256": {
            relative: hashlib.sha256((_ROOT / relative).read_bytes()).hexdigest()
            for relative in code_paths
        },
        "limitations": [
            "A PASSED calculation is evidence for review; this command cannot approve Gate 1.",
            "Human identities and signatures are not authenticated by this local evaluator.",
            "Broker data-use permission does not approve historical timestamp, calendar, "
            "or flag policy evidence.",
            "The evaluator never mutates clean bars, ticks, calendars, policies, "
            "or acquisition artifacts.",
        ],
    }


def _write_exclusive(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError as exc:
        raise ReferenceAcceptanceError(f"refusing to overwrite existing evidence: {path}") from exc


def write_report(report: dict[str, object], output_dir: Path) -> tuple[Path, Path]:
    """Publish a report and checksum sidecar into a new build directory."""

    output = _inside_repository(output_dir, label="output directory", must_exist=False)
    if not output.is_relative_to((_ROOT / "build").resolve()):
        raise ReferenceAcceptanceError("output directory must be inside build/")
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ReferenceAcceptanceError("output directory must not already exist") from exc
    report_path = output / "report.json"
    sidecar_path = output / "report.sha256.json"
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_exclusive(report_path, encoded)
    sidecar = {
        "report.json": {
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    }
    _write_exclusive(
        sidecar_path,
        (json.dumps(sidecar, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return report_path, sidecar_path


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bar-root",
        type=Path,
        default=Path("build/gate1/30day-stable-b102ecdd/first/clean/bars/FBS/1m/EURUSD"),
    )
    parser.add_argument(
        "--tick-root",
        type=Path,
        default=Path("build/gate1/30day-stable-b102ecdd/first/clean/ticks/FBS/EURUSD"),
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("configs/calendars/reference_month_policy_draft.json"),
    )
    parser.add_argument("--calendar", type=Path)
    parser.add_argument("--approval-binding", type=Path)
    parser.add_argument(
        "--producer-report",
        type=Path,
        default=Path("build/gate1/30day-stable-b102ecdd/report.json"),
    )
    parser.add_argument(
        "--producer-sidecar",
        type=Path,
        default=Path("build/gate1/30day-stable-b102ecdd/report.sha256.json"),
    )
    parser.add_argument("--producer-report-sha256")
    parser.add_argument("--venue", default="FBS")
    parser.add_argument("--source", default="FBS-Demo")
    parser.add_argument("--instrument", default="EURUSD")
    parser.add_argument("--calendar-instrument", default="FBS-Demo/EURUSD")
    parser.add_argument("--reference-month", default="2024-10")
    parser.add_argument("--known-at", required=True)
    parser.add_argument("--generated-at")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        known_at = _utc(args.known_at, "known_at")
        generated_at = (
            datetime.now(UTC)
            if args.generated_at is None
            else _utc(args.generated_at, "generated_at")
        )
        scope = ReferenceScope(
            venue=args.venue,
            source=args.source,
            instrument=args.instrument,
            calendar_instrument=args.calendar_instrument,
            reference_month=args.reference_month,
        )
        report = build_report(
            scope=scope,
            bar_root=args.bar_root,
            tick_root=args.tick_root,
            policy_path=args.policy,
            calendar_path=args.calendar,
            approval_binding_path=args.approval_binding,
            producer_report_path=args.producer_report,
            producer_sidecar_path=args.producer_sidecar,
            expected_producer_report_sha256=args.producer_report_sha256,
            known_at=known_at,
            generated_at=generated_at,
        )
        report_path, sidecar_path = write_report(report, args.output_dir)
    except (OSError, ValueError, ReferenceAcceptanceError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        return 3
    print(
        json.dumps(
            {
                "status": report["status"],
                "gate_approved": False,
                "report": report_path.relative_to(_ROOT).as_posix(),
                "sidecar": sidecar_path.relative_to(_ROOT).as_posix(),
            }
        )
    )
    if report["status"] == AcceptanceStatus.PASSED.value:
        return 0
    return 1 if report["status"] == AcceptanceStatus.FAILED.value else 2


if __name__ == "__main__":
    raise SystemExit(main())
