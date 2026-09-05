"""Reference-month acceptance uses exact bins and fails closed on missing evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from tradebot.data.calendar import ExpectedLiquidityCalendar, LiquidityDay, LiquidityStatus
from tradebot.data.reference_acceptance import (
    AcceptanceStatus,
    ApprovalBinding,
    ApprovalDecision,
    FileEvidence,
    FlagClass,
    FlagRule,
    FlagSource,
    FlagTreatment,
    LoadedBars,
    LoadedRetrospectiveFlags,
    MinuteBar,
    MissingBarTreatment,
    ProducerInventoryEvidence,
    ReferenceMonthPolicy,
    ReferenceMonthResult,
    ReferenceScope,
    evaluate_reference_month,
    read_clean_bar_files,
    read_policy,
    read_retrospective_tick_files,
    verify_producer_inventory,
)
from tradebot.data.storage import CLEAN_TICK_SCHEMA

KNOWN_AT = datetime(2025, 1, 1, tzinfo=UTC)
CALENDAR_SHA = "a" * 64
POLICY_SHA = "b" * 64
INVENTORY = ProducerInventoryEvidence(
    report_sha256="e" * 64, sidecar_sha256="f" * 64, corpus_id="0" * 64
)


def _scope(month: str = "2024-01") -> ReferenceScope:
    return ReferenceScope(
        venue="FBS",
        source="FBS-Demo",
        instrument="EURUSD",
        calendar_instrument="FBS-Demo/EURUSD",
        reference_month=month,
    )


def _rules(
    *, retrospective: FlagTreatment = FlagTreatment.COUNT_AS_FLAGGED
) -> tuple[FlagRule, ...]:
    return (
        FlagRule(
            name="DEFECT",
            source=FlagSource.CAUSAL_BAR,
            classification=FlagClass.CAUSAL_DEFECT,
            treatment=FlagTreatment.COUNT_AS_FLAGGED,
            rationale="Synthetic causal flag used to verify exact arithmetic.",
        ),
        FlagRule(
            name="SECOND",
            source=FlagSource.CAUSAL_BAR,
            classification=FlagClass.CAUSAL_DEFECT,
            treatment=FlagTreatment.COUNT_AS_FLAGGED,
            rationale="Second synthetic causal flag used to verify union semantics.",
        ),
        FlagRule(
            name="TS_RECV_IMPUTED",
            source=FlagSource.CAUSAL_BAR,
            classification=FlagClass.PROVENANCE,
            treatment=FlagTreatment.EXCLUDE_FROM_NUMERATOR,
            rationale="The test policy explicitly separates provenance from defects.",
        ),
        FlagRule(
            name="PRICE_OUTLIER",
            source=FlagSource.RETROSPECTIVE_TICK,
            classification=FlagClass.RETROSPECTIVE_QA,
            treatment=retrospective,
            rationale="The test policy explicitly classifies future-informed QA.",
        ),
    )


def _policy(
    scope: ReferenceScope,
    *,
    status: str = "APPROVED",
    missing: MissingBarTreatment = MissingBarTreatment.COUNT_AS_FLAGGED,
) -> ReferenceMonthPolicy:
    return ReferenceMonthPolicy(
        policy_id="synthetic-test-policy",
        status=status,
        scope=scope,
        missing_expected_bar_treatment=missing,
        rules=_rules(),
        sha256=POLICY_SHA,
    )


def _approval(scope: ReferenceScope) -> ApprovalBinding:
    reviewed = datetime(2024, 12, 1, tzinfo=UTC)
    return ApprovalBinding(
        scope=scope,
        calendar_sha256=CALENDAR_SHA,
        policy_sha256=POLICY_SHA,
        independent_review=ApprovalDecision(
            role="INDEPENDENT_REVIEWER",
            person="Reviewer",
            decision="APPROVED",
            decided_at_utc=reviewed,
            artifact_path="review.md",
            artifact_sha256="c" * 64,
        ),
        principal_approval=ApprovalDecision(
            role="PRINCIPAL",
            person="Principal",
            decision="APPROVED",
            decided_at_utc=reviewed + timedelta(days=1),
            artifact_path="principal.md",
            artifact_sha256="d" * 64,
        ),
    )


def _day(
    scope: ReferenceScope,
    session_date: date,
    *,
    interval: tuple[datetime, datetime] | None = None,
    available_at: datetime = datetime(2023, 1, 1, tzinfo=UTC),
) -> LiquidityDay:
    return LiquidityDay(
        instrument=scope.calendar_instrument,
        session_date=session_date,
        status=LiquidityStatus.CLOSED if interval is None else LiquidityStatus.PARTIAL,
        source="synthetic-test-calendar",
        source_citation="synthetic://reference-acceptance-test",
        effective_at=datetime(2023, 1, 1, tzinfo=UTC),
        available_at=available_at,
        valid_until=datetime(2030, 1, 1, tzinfo=UTC),
        expected_intervals=() if interval is None else (interval,),
    )


def _calendar(
    scope: ReferenceScope,
    start: datetime,
    minutes: int,
    *,
    omit: date | None = None,
    future_day: date | None = None,
) -> ExpectedLiquidityCalendar:
    year, month = (int(value) for value in scope.reference_month.split("-"))
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    day = date(year, month, 1)
    entries: list[LiquidityDay] = []
    while day < next_month:
        if day != omit:
            interval = None
            # FX sessions in these tests close on the UTC date immediately after start.
            if day == start.date() + timedelta(days=1):
                interval = (start, start + timedelta(minutes=minutes))
            available = (
                datetime(2026, 1, 1, tzinfo=UTC)
                if day == future_day
                else datetime(2023, 1, 1, tzinfo=UTC)
            )
            entries.append(_day(scope, day, interval=interval, available_at=available))
        day += timedelta(days=1)
    return ExpectedLiquidityCalendar(entries)


def _bar(scope: ReferenceScope, minute: datetime, *flags: str) -> MinuteBar:
    return MinuteBar(
        venue=scope.venue,
        source=scope.source,
        instrument=scope.instrument,
        ts_open=minute,
        ts_close=minute + timedelta(minutes=1),
        quality_flags=flags,
    )


def _evaluate(
    scope: ReferenceScope,
    bars: list[MinuteBar],
    calendar: ExpectedLiquidityCalendar | None,
    *,
    policy: ReferenceMonthPolicy | None = None,
    approval: ApprovalBinding | None = None,
    retrospective: dict[datetime, tuple[str, ...]] | None = None,
) -> ReferenceMonthResult:
    covered = {bar.ts_open for bar in bars}
    return evaluate_reference_month(
        scope=scope,
        bars=bars,
        calendar=calendar,
        calendar_sha256=None if calendar is None else CALENDAR_SHA,
        policy=_policy(scope) if policy is None else policy,
        known_at=KNOWN_AT,
        approval=_approval(scope) if approval is None else approval,
        producer_inventory=INVENTORY,
        causal_tick_flags_by_minute={},
        retrospective_flags_by_minute={} if retrospective is None else retrospective,
        tick_covered_minutes=covered,
    )


@pytest.mark.parametrize(
    ("denominator", "expected_status", "strict"),
    [(1000, AcceptanceStatus.FAILED, False), (1001, AcceptanceStatus.PASSED, True)],
)
def test_strict_zero_point_one_percent_boundary(
    denominator: int, expected_status: AcceptanceStatus, strict: bool
) -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)
    bars = [_bar(scope, start + timedelta(minutes=index)) for index in range(denominator)]
    bars[0] = _bar(scope, start, "DEFECT")

    result = _evaluate(scope, bars, _calendar(scope, start, denominator))

    assert result.status == expected_status
    assert result.counted_flagged_union == 1
    assert result.strict_less_than_0_1_percent is strict


def test_overlapping_flags_are_counted_once() -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)
    bars = [_bar(scope, start + timedelta(minutes=index)) for index in range(1001)]
    bars[0] = _bar(scope, start, "DEFECT", "SECOND")

    result = _evaluate(
        scope,
        bars,
        _calendar(scope, start, 1001),
        retrospective={start: ("PRICE_OUTLIER",)},
    )

    assert result.status == AcceptanceStatus.PASSED
    assert result.observed_counted_flag_union == 1
    assert result.counted_flagged_union == 1
    assert {row["flag"]: row["observed_bars"] for row in result.flag_observations}[
        "PRICE_OUTLIER"
    ] == 1


def test_causal_flag_from_excluded_tick_minute_is_not_laundered_by_absent_bar() -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)

    result = evaluate_reference_month(
        scope=scope,
        bars=[],
        calendar=_calendar(scope, start, 1001),
        calendar_sha256=CALENDAR_SHA,
        policy=_policy(scope),
        known_at=KNOWN_AT,
        approval=_approval(scope),
        producer_inventory=INVENTORY,
        causal_tick_flags_by_minute={start: ("DEFECT",)},
        retrospective_flags_by_minute={},
        tick_covered_minutes={start},
    )

    assert result.observed_counted_flag_union == 1
    assert result.causal_tick_flagged_minutes_without_bar == 1
    # All other expected bars are also absent and union counting still counts each bin once.
    assert result.counted_flagged_union == 1001


def test_sparse_tick_coverage_is_indeterminate_even_when_flags_are_empty() -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)
    bars = [_bar(scope, start), _bar(scope, start + timedelta(minutes=1))]

    result = evaluate_reference_month(
        scope=scope,
        bars=bars,
        calendar=_calendar(scope, start, 2),
        calendar_sha256=CALENDAR_SHA,
        policy=_policy(scope),
        known_at=KNOWN_AT,
        approval=_approval(scope),
        producer_inventory=INVENTORY,
        causal_tick_flags_by_minute={},
        retrospective_flags_by_minute={},
        tick_covered_minutes={start},
    )

    assert result.status == AcceptanceStatus.INDETERMINATE
    assert result.actual_bar_minutes_without_tick_coverage == 1


def test_absent_producer_inventory_is_indeterminate() -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)

    result = evaluate_reference_month(
        scope=scope,
        bars=[_bar(scope, start)],
        calendar=_calendar(scope, start, 1),
        calendar_sha256=CALENDAR_SHA,
        policy=_policy(scope),
        known_at=KNOWN_AT,
        approval=_approval(scope),
        producer_inventory=None,
        causal_tick_flags_by_minute={},
        retrospective_flags_by_minute={},
        tick_covered_minutes={start},
    )

    assert result.status == AcceptanceStatus.INDETERMINATE
    assert "complete producer clean-file inventory" in " ".join(result.reasons)


def test_missing_expected_minute_counts_once_and_equality_fails() -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)
    bars = [_bar(scope, start + timedelta(minutes=index)) for index in range(999)]

    result = _evaluate(scope, bars, _calendar(scope, start, 1000))

    assert result.status == AcceptanceStatus.FAILED
    assert result.missing_expected_minutes == 1
    assert result.missing_counted_as_flagged == 1
    assert result.counted_flagged_union == 1


def test_bar_outside_narrow_liquidity_interval_is_reported_not_failed() -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)
    outside = start + timedelta(minutes=1)

    result = _evaluate(
        scope,
        [_bar(scope, start), _bar(scope, outside)],
        _calendar(scope, start, 1),
    )

    assert result.status == AcceptanceStatus.PASSED
    assert result.unexpected_actual_minutes == 1


def test_empty_expected_minute_map_is_indeterminate() -> None:
    scope = _scope()
    result = _evaluate(
        scope,
        [],
        ExpectedLiquidityCalendar(_day(scope, date(2024, 1, day)) for day in range(1, 32)),
    )

    assert result.status == AcceptanceStatus.INDETERMINATE
    assert result.expected_liquid_minutes == 0
    assert "approved expected-liquid minute denominator is empty" in result.reasons


def test_partial_month_calendar_is_indeterminate() -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)
    calendar = _calendar(scope, start, 1, omit=date(2024, 1, 31))

    result = _evaluate(scope, [_bar(scope, start)], calendar)

    assert result.status == AcceptanceStatus.INDETERMINATE
    assert result.calendar_days_missing == ("2024-01-31",)


def test_missing_calendar_is_indeterminate() -> None:
    scope = _scope()
    result = _evaluate(scope, [], None)

    assert result.status == AcceptanceStatus.INDETERMINATE
    assert "no expected-liquidity calendar was supplied" in result.reasons
    assert len(result.calendar_days_missing) == 31


def test_absent_approval_is_indeterminate() -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)
    result = evaluate_reference_month(
        scope=scope,
        bars=[_bar(scope, start)],
        calendar=_calendar(scope, start, 1),
        calendar_sha256=CALENDAR_SHA,
        policy=_policy(scope),
        known_at=KNOWN_AT,
        approval=None,
        producer_inventory=INVENTORY,
        causal_tick_flags_by_minute={},
        retrospective_flags_by_minute={},
        tick_covered_minutes={start},
    )

    assert result.status == AcceptanceStatus.INDETERMINATE
    assert not result.approval_binding_verified
    assert "hash-bound independent-review and Principal approval are absent" in result.reasons


def test_reference_month_uses_canonical_new_york_close_date_not_utc_month() -> None:
    scope = _scope("2024-10")
    september_utc = datetime(2024, 9, 30, 21, tzinfo=UTC)
    november_close_session = datetime(2024, 10, 31, 21, tzinfo=UTC)
    calendar = _calendar(scope, september_utc, 1)

    result = _evaluate(
        scope,
        [_bar(scope, september_utc), _bar(scope, november_close_session)],
        calendar,
    )

    assert result.status == AcceptanceStatus.PASSED
    assert result.actual_bars_in_close_month == 1
    assert result.observed_expected_minutes == 1


def test_calendar_entry_not_yet_known_is_indeterminate() -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)
    calendar = _calendar(scope, start, 1, future_day=date(2024, 1, 2))

    result = _evaluate(scope, [_bar(scope, start)], calendar)

    assert result.status == AcceptanceStatus.INDETERMINATE
    assert result.calendar_days_missing == ("2024-01-02",)


def test_draft_policy_never_becomes_approved_from_observed_counts() -> None:
    scope = _scope()
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)
    draft = replace(_policy(scope), status="DRAFT_UNAPPROVED")

    result = _evaluate(scope, [_bar(scope, start)], _calendar(scope, start, 1), policy=draft)

    assert result.status == AcceptanceStatus.INDETERMINATE
    assert not result.to_dict()["gate_approved"]
    assert "counted-flag policy is draft/unapproved" in result.reasons


def _clean_metadata(*, kind: str, schema: str) -> dict[bytes, bytes]:
    return {
        b"tradebot.schema": schema.encode(),
        b"tradebot.kind": kind.encode(),
        b"tradebot.venue": b"FBS",
        b"tradebot.instrument": b"EURUSD",
        b"tradebot.source": b"FBS-Demo",
        b"tradebot.corpus_id": ("0" * 64).encode(),
        b"tradebot.timeframe": b"1m",
    }


def test_loader_rejects_metadata_disguised_partial_bar_schema(tmp_path: Path) -> None:
    malformed = pa.schema(
        [
            pa.field("instrument", pa.string()),
            pa.field("ts_open", pa.timestamp("us", tz="UTC")),
            pa.field("ts_close", pa.timestamp("us", tz="UTC")),
            pa.field("source", pa.string()),
            pa.field("quality_flags", pa.list_(pa.string())),
        ],
        metadata=_clean_metadata(kind="clean-bar", schema="clean-bar-v2"),
    )
    path = tmp_path / "partial.parquet"
    pq.write_table(pa.Table.from_pylist([], schema=malformed), path)

    with pytest.raises(ValueError, match="exact clean-bar-v2 schema"):
        read_clean_bar_files([path], scope=_scope())


def test_policy_rejects_duplicate_keys_and_boolean_schema_version(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        read_policy(duplicate)

    source = (
        Path(__file__).resolve().parents[3] / "configs/calendars/reference_month_policy_draft.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["schema_version"] = True
    boolean = tmp_path / "boolean.json"
    boolean.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported policy schema"):
        read_policy(boolean)


def test_tick_loader_preserves_causal_retrospective_and_coverage(tmp_path: Path) -> None:
    start = datetime(2024, 1, 1, 22, tzinfo=UTC)
    table = pa.Table.from_pylist(
        [
            {
                "instrument": "EURUSD",
                "ts_event": start,
                "ts_recv": start,
                "available_at": start,
                "bid": Decimal("1.1"),
                "ask": Decimal("1.2"),
                "bid_size": None,
                "ask_size": None,
                "source": "FBS-Demo",
                "seq": 1,
                "source_flags": 0,
                "quality_flags": ["CROSSED_QUOTE"],
                "retrospective_flags": ["PRICE_OUTLIER"],
                "eligible_for_bars": False,
            }
        ],
        schema=CLEAN_TICK_SCHEMA,
    ).replace_schema_metadata(_clean_metadata(kind="clean-tick", schema="clean-tick-v2"))
    path = tmp_path / "tick.parquet"
    pq.write_table(table, path)

    loaded = read_retrospective_tick_files([path], scope=_scope())

    assert loaded.causal_flags_by_minute == {start: ("CROSSED_QUOTE",)}
    assert loaded.flags_by_minute == {start: ("PRICE_OUTLIER",)}
    assert loaded.covered_minutes == frozenset({start})
    assert loaded.files[0].rows == 1


def test_forged_sparse_inventory_and_matching_sidecar_fail_original_pin(tmp_path: Path) -> None:
    scope = _scope()
    original_tick_hash = "1" * 64
    sparse_tick_hash = "2" * 64
    bar_hash = "3" * 64
    original_report = {
        "schema_version": 1,
        "corpus_id": "0" * 64,
        "reproducibility_status": "PASSED",
        "independent_rebuilds_byte_identical": True,
        "raw_files_unchanged": True,
        "implementation_unchanged": True,
        "clean_manifest": [
            {
                "path": "clean/bars/FBS/1m/EURUSD/2024/01/part.parquet",
                "sha256": bar_hash,
            },
            {
                "path": "clean/ticks/FBS/EURUSD/2024/01/part.parquet",
                "sha256": original_tick_hash,
            },
        ],
    }
    original_bytes = (json.dumps(original_report, sort_keys=True) + "\n").encode()
    original_pin = hashlib.sha256(original_bytes).hexdigest()
    forged = json.loads(original_bytes)
    forged["clean_manifest"][1]["sha256"] = sparse_tick_hash
    forged_bytes = (json.dumps(forged, sort_keys=True) + "\n").encode()
    report_path = tmp_path / "report.json"
    report_path.write_bytes(forged_bytes)
    sidecar_path = tmp_path / "report.sha256.json"
    sidecar_path.write_text(
        json.dumps({"report.json": hashlib.sha256(forged_bytes).hexdigest()}), encoding="utf-8"
    )
    bars = LoadedBars(
        bars=(),
        files=(
            FileEvidence(
                path=str(tmp_path / "first/clean/bars/FBS/1m/EURUSD/2024/01/part.parquet"),
                sha256=bar_hash,
                rows=1,
            ),
        ),
        corpus_ids=("0" * 64,),
    )
    # The sparse replacement still covers the minute, but has omitted a flagged tick.
    ticks = LoadedRetrospectiveFlags(
        causal_flags_by_minute={},
        flags_by_minute={},
        covered_minutes=frozenset({datetime(2024, 1, 1, 22, tzinfo=UTC)}),
        files=(
            FileEvidence(
                path=str(tmp_path / "first/clean/ticks/FBS/EURUSD/2024/01/part.parquet"),
                sha256=sparse_tick_hash,
                rows=1,
            ),
        ),
        corpus_ids=("0" * 64,),
    )

    with pytest.raises(ValueError, match="independent expected hash"):
        verify_producer_inventory(
            report_path=report_path,
            sidecar_path=sidecar_path,
            expected_report_sha256=original_pin,
            scope=scope,
            bars=bars,
            ticks=ticks,
        )
