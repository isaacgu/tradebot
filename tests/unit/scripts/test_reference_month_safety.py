"""Independent temporal, denominator, privacy and snapshot-safety regressions."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

_NY = ZoneInfo("America/New_York")
_INSTRUMENT = "FBS-Demo/EURUSD"
_PLAN = "a" * 64
_CAPTURED = datetime(2026, 9, 5, 9, tzinfo=UTC)


@pytest.fixture
def diagnostic() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "diagnose_reference_month.py"
    spec = importlib.util.spec_from_file_location("reference_month_independent_safety", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _stamp(day: date) -> datetime:
    return datetime.combine(day, time(17), tzinfo=_NY).astimezone(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _candidate() -> dict[str, Any]:
    rows = []
    for offset in range(31):
        day = date(2024, 10, 1) + timedelta(days=offset)
        start, end = _stamp(day), _stamp(day + timedelta(days=1))
        status = "CLOSED" if day.weekday() in {4, 5} else "FULL"
        intervals = [] if status == "CLOSED" else [[_iso(start), _iso(end)]]
        if day == date(2024, 10, 27):
            status = "PARTIAL"
            intervals = [[_iso(start + timedelta(hours=1)), _iso(end)]]
        rows.append(
            {
                "instrument": _INSTRUMENT,
                "session_date": day.isoformat(),
                "canonical_session": [_iso(start), _iso(end)],
                "advertised_status_hypothesis": status,
                "advertised_availability_intervals": intervals,
                "basis": {"generic_hours": "independent-test-source"},
                "review_disposition": "UNAPPROVED_SYNTHETIC_TEST",
            }
        )
    return {
        "schema_version": "tradebot-fbs-liquidity-calendar-candidate-v1",
        "reference_month": "2024-10",
        "instrument_scope": [_INSTRUMENT],
        "approval": {
            "approved": False,
            "approved_entries": 0,
            "loadable_by_expected_liquidity_calendar": False,
            "status": "PROVISIONAL_NOT_LOADABLE",
        },
        "knowledge_policy": {"available_at_utc": "2026-09-04T20:46:16.340049Z"},
        "source_claims": [{"source": "SYNTHETIC_TEST_NOT_VENUE_EVIDENCE"}],
        "review_rows": rows,
    }


def _source(root: Path, day: date, *, empty: bool = False) -> dict[str, Any]:
    """Create an independent minimal checkpoint, not via the production writer."""
    start, end = _stamp(day), _stamp(day + timedelta(days=1))
    milliseconds = int(start.timestamp()) * 1000
    content = b"tradebot.source-ticks.semantic.v1\n"
    if not empty:
        content += f"{milliseconds // 1000}\t{milliseconds}\t1.1\t1.2\t0\t0\t6\t0\n".encode()
    compressed = gzip.compress(content, mtime=0)
    prefix = Path(_PLAN) / "EURUSD/autumn_dst_reference_2024"
    raw_relative = prefix / f"{day.isoformat()}.source-ticks.tsv.gz"
    checkpoint_relative = prefix / f"{day.isoformat()}.source-ticks.tsv.checkpoint.json"
    raw_path = root / raw_relative
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(compressed)
    count = 0 if empty else 1
    metrics = {
        "tick_count": count,
        "active_minutes": count,
        "ask_nonpositive": 0,
        "bid_nonpositive": 0,
        "crossed_quotes": 0,
        "exact_adjacent_duplicates": 0,
        "locked_quotes": 0,
        "negative_volume": 0,
        "negative_volume_real": 0,
        "time_field_mismatches": 0,
        "timestamp_regressions": 0,
        "flag_counts": [] if empty else [[6, 1]],
    }
    checkpoint: dict[str, Any] = {
        "schema_version": 1,
        "probe_version": "fbs-tick-continuity-v1",
        "plan_hash": _PLAN,
        "run_id": "b" * 32,
        "environment_sha256": "e" * 64,
        "source": "FBS-Demo",
        "environment": {"login": "PRIVATE_TEST_SENTINEL", "password": "PRIVATE_TEST_SECRET"},
        "chunk": {
            "logical_symbol": "EURUSD",
            "broker_symbol": "EURUSD",
            "window_id": "autumn_dst_reference_2024",
            "chunk_id": f"EURUSD/autumn_dst_reference_2024/{day.isoformat()}",
            "session_date": day.isoformat(),
            "start_utc": _iso(start),
            "end_utc": _iso(end),
            "metrics": metrics,
        },
        "raw": {
            "path": raw_relative.as_posix(),
            "compressed_sha256": _sha(compressed),
            "compressed_bytes": len(compressed),
            "semantic_sha256": _sha(content),
            "uncompressed_bytes": len(content),
        },
    }
    unsigned = json.dumps(checkpoint, sort_keys=True, separators=(",", ":")).encode()
    checkpoint["integrity"] = {"algorithm": "sha256", "payload_sha256": _sha(unsigned)}
    encoded = json.dumps(checkpoint, sort_keys=True).encode()
    (root / checkpoint_relative).write_bytes(encoded)
    return {
        "run_id": "b" * 32,
        "checkpoint_path": checkpoint_relative.as_posix(),
        "checkpoint_sha256": _sha(encoded),
        "checkpoint_payload_sha256": _sha(unsigned),
        "raw_path": raw_relative.as_posix(),
        "raw_compressed_sha256": _sha(compressed),
        "raw_semantic_sha256": _sha(content),
        "tick_count": count,
    }


@pytest.fixture
def sparse_inputs(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "probe"
    candidate = _candidate()
    observed = _source(root, date(2024, 10, 6))
    _source(root, date(2024, 9, 30), empty=True)
    for row in candidate["review_rows"]:
        if row["session_date"] == "2024-10-06":
            row["observation"] = observed
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")
    return path, root


def test_close_date_october_includes_september_open_and_excludes_november_close(
    diagnostic: ModuleType,
) -> None:
    close_start, close_end = diagnostic._canonical_bounds_for_close_date(date(2024, 10, 1))
    assert close_start * 60 == int(datetime(2024, 9, 30, 21, tzinfo=UTC).timestamp())
    assert close_end * 60 == int(datetime(2024, 10, 1, 21, tzinfo=UTC).timestamp())
    assert diagnostic._canonical_bounds_for_open_date(date(2024, 9, 30)) == (
        close_start,
        close_end,
    )
    _, november_close = diagnostic._canonical_bounds_for_open_date(date(2024, 10, 31))
    assert diagnostic._close_date(november_close) == date(2024, 11, 1)


@pytest.mark.parametrize(
    "opening,expected_minutes", [(date(2024, 3, 9), 1380), (date(2024, 11, 2), 1500)]
)
def test_ny_boundary_uses_each_dates_actual_offset(
    diagnostic: ModuleType, opening: date, expected_minutes: int
) -> None:
    start, end = diagnostic._canonical_bounds_for_open_date(opening)
    assert end - start == expected_minutes
    assert diagnostic._canonical_bounds_for_close_date(opening + timedelta(days=1)) == (
        start,
        end,
    )


def test_zero_tick_minutes_remain_denominator_and_missing_windows_remain_unknown(
    diagnostic: ModuleType,
) -> None:
    result = diagnostic._minute_metrics(set(range(10)), set(range(2, 8)), {3, 6})
    assert result["expected_advertised_minutes"] == 10
    assert result["evaluable_expected_minutes"] == 6
    assert result["observed_active_minutes_within_expected"] == 2
    assert result["unobserved_evaluable_minutes"] == 4
    assert result["unknown_expected_minutes_due_to_unverified_window"] == 4
    assert result["active_share_of_evaluable_advertised_minutes"] == pytest.approx(1 / 3)
    assert result["longest_unknown_expected_run"]["minutes"] == 2


@pytest.mark.parametrize("expected,acquired", [(set(), set()), ({1, 2}, set())])
def test_empty_or_unverified_denominator_is_not_zero_or_perfect_coverage(
    diagnostic: ModuleType, expected: set[int], acquired: set[int]
) -> None:
    result = diagnostic._minute_metrics(expected, acquired, set())
    assert result["active_share_of_evaluable_advertised_minutes"] is None
    assert result["unknown_expected_minutes_due_to_unverified_window"] == len(expected)


def test_fixed_offset_sensitivities_leave_original_epoch_sets_unchanged(
    diagnostic: ModuleType,
) -> None:
    original = {28798920, 28798921}
    frozen = original.copy()
    assert [case[1] for case in diagnostic.OFFSET_SCENARIOS] == [0, -120, -180]
    assert diagnostic._shift(original, -120) == {28798800, 28798801}
    assert original == frozen
    assert diagnostic._shift(original, 0) is not original


@pytest.mark.parametrize("case", ["full_empty", "full_short", "partial_outside", "overlap"])
def test_hypothesis_cannot_silently_change_its_canonical_denominator(
    diagnostic: ModuleType, case: str
) -> None:
    candidate = _candidate()
    row = candidate["review_rows"][0]
    start, end = row["canonical_session"]
    if case == "full_empty":
        row["advertised_availability_intervals"] = []
    elif case == "full_short":
        row["advertised_availability_intervals"] = [["2024-10-01T22:00:00Z", end]]
    elif case == "partial_outside":
        row["advertised_status_hypothesis"] = "PARTIAL"
        row["advertised_availability_intervals"] = [["2024-10-01T20:59:00Z", end]]
    else:
        row["advertised_status_hypothesis"] = "PARTIAL"
        row["advertised_availability_intervals"] = [[start, end], [start, end]]
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._validate_candidate(candidate)


def test_sparse_report_keeps_full_month_scope_unknown_and_no_gate_approval(
    diagnostic: ModuleType, sparse_inputs: tuple[Path, Path]
) -> None:
    candidate_path, probe_root = sparse_inputs
    input_before = {path: path.read_bytes() for path in probe_root.rglob("*") if path.is_file()}
    result = diagnostic.build_diagnostic(
        candidate_path=candidate_path,
        probe_root=probe_root,
        thirty_day_report=None,
        generated_at=_CAPTURED,
    )
    instrument = result["instruments"][0]
    assert instrument["advertised_hypothesis_minutes"] == 33060
    assert len(instrument["hypothesis_rows"]) == 31
    assert instrument["hypothesis_rows"][0]["canonical_close_date"] == "2024-10-01"
    assert instrument["source_checkpoint_flag_breakdown"]["checkpoint_windows"] == 2
    canonical = instrument["offset_scenarios"][0]["aggregate"]
    assert canonical["expected_advertised_minutes"] == 33060
    assert canonical["evaluable_expected_minutes"] == 2880
    assert canonical["unobserved_evaluable_minutes"] == 2879
    assert canonical["unknown_expected_minutes_due_to_unverified_window"] == 30180
    assert result["scope"]["candidate_available_at_utc_preserved"] == (
        "2026-09-04T20:46:16.340049Z"
    )
    assert result["acceptance_status"] == "INDETERMINATE"
    assert result["gate_approved"] is False
    assert result["scope"]["canonical_timestamps_mutated"] is False
    assert result["provisional_controls"]["calendar_approved"] is False
    assert "PRIVATE_TEST" not in json.dumps(result)
    assert all(path.read_bytes() == content for path, content in input_before.items())


@pytest.mark.parametrize("target", ["checkpoint", "raw", "candidate"])
def test_mid_scan_input_replacement_aborts_before_report_publication(
    diagnostic: ModuleType,
    sparse_inputs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    candidate_path, probe_root = sparse_inputs
    original_scan = diagnostic._scan_raw
    altered = False

    def scan_then_replace(frozen: Any) -> Any:
        nonlocal altered
        result = original_scan(frozen)
        if not altered:
            path = {
                "checkpoint": frozen.checkpoint_path,
                "raw": frozen.raw_path,
                "candidate": candidate_path,
            }[target]
            path.write_bytes(path.read_bytes() + b" ")
            altered = True
        return result

    monkeypatch.setattr(diagnostic, "_scan_raw", scan_then_replace)
    with pytest.raises(diagnostic.DiagnosticError, match="changed"):
        diagnostic.build_diagnostic(
            candidate_path=candidate_path,
            probe_root=probe_root,
            thirty_day_report=None,
            generated_at=_CAPTURED,
        )
    assert not (candidate_path.parent / "report.json").exists()


def test_missing_close_month_boundary_cannot_be_silently_dropped(
    diagnostic: ModuleType, sparse_inputs: tuple[Path, Path]
) -> None:
    candidate_path, probe_root = sparse_inputs
    boundary = next(probe_root.rglob("2024-09-30.source-ticks.tsv.checkpoint.json"))
    boundary.unlink()
    with pytest.raises(diagnostic.DiagnosticError, match="missing"):
        diagnostic.build_diagnostic(
            candidate_path=candidate_path,
            probe_root=probe_root,
            thirty_day_report=None,
            generated_at=_CAPTURED,
        )


def test_report_refuses_to_replace_existing_evidence(
    diagnostic: ModuleType, tmp_path: Path
) -> None:
    original = b"independent previously published evidence"
    (tmp_path / "report.json").write_bytes(original)
    with pytest.raises(diagnostic.DiagnosticError, match=r"overwrite|reuse"):
        diagnostic.write_report({"gate_approved": False}, tmp_path)
    assert (tmp_path / "report.json").read_bytes() == original


def _frozen_one(diagnostic: ModuleType, root: Path) -> Any:
    observation = _source(root, date(2024, 10, 6))
    return diagnostic._freeze_checkpoint(
        probe_root=root,
        checkpoint_relative_path=observation["checkpoint_path"],
        instrument=_INSTRUMENT,
        expected_observation=observation,
        provenance="SYNTHETIC_INDEPENDENT_TEST",
    )


@pytest.mark.parametrize(
    "field", ["raw_semantic_sha256", "tick_count", "active_minutes", "uncompressed_bytes"]
)
def test_raw_scan_reconciles_every_declared_content_identity(
    diagnostic: ModuleType, tmp_path: Path, field: str
) -> None:
    frozen = _frozen_one(diagnostic, tmp_path)
    if field == "raw_semantic_sha256":
        frozen = replace(frozen, raw_semantic_sha256="0" * 64)
    elif field == "tick_count":
        frozen = replace(frozen, tick_count=2)
    elif field == "active_minutes":
        frozen.checkpoint["chunk"]["metrics"][field] = 2
    else:
        frozen.checkpoint["raw"][field] += 1
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._scan_raw(frozen)


def test_checkpoint_payload_cannot_change_without_its_identity(
    diagnostic: ModuleType, tmp_path: Path
) -> None:
    observation = _source(tmp_path, date(2024, 10, 6))
    path = tmp_path / observation["checkpoint_path"]
    payload = json.loads(path.read_bytes())
    payload["chunk"]["metrics"]["tick_count"] += 1
    with pytest.raises(diagnostic.DiagnosticError, match="checksum"):
        diagnostic._checkpoint_payload(json.dumps(payload).encode(), "synthetic checkpoint")


def test_evidence_reference_cannot_escape_or_traverse_an_inside_root_symlink(
    diagnostic: ModuleType, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    with pytest.raises(diagnostic.DiagnosticError, match="escape"):
        diagnostic._safe_reference(root, "../outside.json", "test")
    target = root / "target.json"
    target.write_bytes(b"{}")
    link = root / "alias.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Host does not permit unprivileged symlink fixtures")
    with pytest.raises(diagnostic.DiagnosticError, match="symlink"):
        diagnostic._safe_reference(root, "alias.json", "test")


def test_json_size_limit_bounds_the_read_itself(
    diagnostic: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "oversized.json"
    path.write_bytes(b"{}")
    read_sizes: list[int] = []

    class ObservedStream(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            read_sizes.append(-1 if size is None else size)
            return super().read(size)

    def bounded_open(*args: Any, **kwargs: Any) -> ObservedStream:
        return ObservedStream(b" " * (diagnostic.MAX_JSON_BYTES + 100))

    monkeypatch.setattr(Path, "open", bounded_open)
    with pytest.raises(diagnostic.DiagnosticError, match="limit"):
        diagnostic._read_frozen_json(path, "synthetic oversized evidence")
    assert read_sizes and all(0 <= size <= diagnostic.MAX_JSON_BYTES + 1 for size in read_sizes)


def test_concurrent_report_publication_cannot_clobber_first_writer(
    diagnostic: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write = diagnostic._write_exclusive
    prior = b"first concurrent writer owns this evidence"

    def publish_after_competitor(path: Path, content: bytes) -> None:
        if path.name == "report.json":
            path.write_bytes(prior)
        original_write(path, content)

    output = tmp_path / "new-output"
    monkeypatch.setattr(diagnostic, "_write_exclusive", publish_after_competitor)
    with pytest.raises((diagnostic.DiagnosticError, FileExistsError)):
        diagnostic.write_report({"gate_approved": False}, output)
    assert (output / "report.json").read_bytes() == prior


@pytest.mark.parametrize(
    "identity",
    ["plan_hash", "window_id", "run_id", "probe_version", "schema_version", "environment_sha256"],
)
def test_supplement_cannot_claim_another_self_checksummed_input_lineage(
    diagnostic: ModuleType, sparse_inputs: tuple[Path, Path], identity: str
) -> None:
    candidate_path, probe_root = sparse_inputs
    boundary = next(probe_root.rglob("2024-09-30.source-ticks.tsv.checkpoint.json"))
    payload = json.loads(boundary.read_bytes())
    if identity == "window_id":
        payload["chunk"][identity] = "different-reference-window"
    elif identity == "schema_version":
        payload[identity] = 2
    elif identity == "plan_hash":
        payload[identity] = "c" * 64
    elif identity == "run_id":
        payload[identity] = "d" * 32
    elif identity == "environment_sha256":
        payload[identity] = "f" * 64
    else:
        payload[identity] = "different-probe-implementation"
    unsigned = {key: value for key, value in payload.items() if key != "integrity"}
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    payload["integrity"]["payload_sha256"] = _sha(encoded)
    boundary.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.build_diagnostic(
            candidate_path=candidate_path,
            probe_root=probe_root,
            thirty_day_report=None,
            generated_at=_CAPTURED,
        )
