from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tradebot.data.acquisition_probe import (
    SOURCE_VIABILITY_PURPOSE,
    AcquisitionPlan,
    ChunkEvidence,
    ChunkRequest,
    SourceTick,
    analyse_chunk,
    canonical_tick_bytes,
    canonical_tick_lines,
    compare_repeat_fetches,
    dataset_sha256,
    fx_session_bounds,
    is_fx_session_date,
    parse_plan,
    semantic_tick_sha256,
    summarise_dataset,
)

NEW_YORK = ZoneInfo("America/New_York")


def test_checked_in_fbs_plan_is_valid_json_with_the_frozen_scope() -> None:
    repository = Path(__file__).resolve().parents[3]
    path = repository / "configs" / "probes" / "fbs_tick_continuity_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    plan = parse_plan(payload)

    assert [symbol.logical for symbol in plan.symbols] == ["EURUSD", "GBPUSD"]
    assert [len(window.iter_session_dates()) for window in plan.windows] == [
        10,
        10,
        25,
        15,
        30,
        5,
    ]
    assert len(plan.chunks) == 190


def _payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "probe_id": "fbs-fx-viability-v1",
        "source": "fbs_mt5_demo",
        "symbols": {"GBPUSD": "GBPUSD", "EURUSD": "EURUSD"},
        "repeat_fetches": 2,
        "chunk_sessions": 1,
        "purpose": SOURCE_VIABILITY_PURPOSE,
        "windows": [
            {
                "id": "spring-dst-2025",
                "purpose": "dst-transition-reference",
                "start_session_date": "2025-03-06",
                "end_session_date_exclusive": "2025-03-12",
            }
        ],
    }


def _single_session_plan(*, two_sessions: bool = False) -> AcquisitionPlan:
    payload = _payload()
    payload["symbols"] = {"GBPUSD": "GBPUSD"}
    payload["windows"] = [
        {
            "id": "sample",
            "purpose": "bounded-test",
            "start_session_date": "2025-03-10",
            "end_session_date_exclusive": "2025-03-12" if two_sessions else "2025-03-11",
        }
    ]
    return parse_plan(payload)


def _millis(moment: datetime) -> int:
    return int((moment - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000)


def _tick(
    request: ChunkRequest,
    offset_milliseconds: int,
    *,
    bid: str = "1.25000",
    ask: str = "1.25010",
    last: str = "0",
    volume: int = 0,
    flags: int = 6,
    volume_real: str = "0",
    time_adjustment: int = 0,
) -> SourceTick:
    stamp = _millis(request.start) + offset_milliseconds
    return SourceTick(
        time=stamp // 1000 + time_adjustment,
        time_msc=stamp,
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=Decimal(last),
        volume=volume,
        flags=flags,
        volume_real=Decimal(volume_real),
    )


def test_strict_plan_parser_produces_stable_symbol_window_session_order() -> None:
    plan = parse_plan(_payload())

    assert [symbol.logical for symbol in plan.symbols] == ["EURUSD", "GBPUSD"]
    assert [chunk.chunk_id for chunk in plan.chunks] == [
        "EURUSD/spring-dst-2025/2025-03-06",
        "EURUSD/spring-dst-2025/2025-03-09",
        "EURUSD/spring-dst-2025/2025-03-10",
        "EURUSD/spring-dst-2025/2025-03-11",
        "GBPUSD/spring-dst-2025/2025-03-06",
        "GBPUSD/spring-dst-2025/2025-03-09",
        "GBPUSD/spring-dst-2025/2025-03-10",
        "GBPUSD/spring-dst-2025/2025-03-11",
    ]

    reordered = _payload()
    reordered["symbols"] = {"EURUSD": "EURUSD", "GBPUSD": "GBPUSD"}
    assert parse_plan(reordered).plan_hash == plan.plan_hash


def test_session_chunks_use_each_1700_new_york_boundary_across_dst() -> None:
    chunks = parse_plan(_payload()).chunks[:4]

    assert chunks[0].start == datetime(2025, 3, 6, 22, tzinfo=UTC)
    assert chunks[1].start == datetime(2025, 3, 9, 21, tzinfo=UTC)
    assert {chunk.session_date.weekday() for chunk in chunks}.isdisjoint({4, 5})
    for chunk in chunks:
        assert chunk.start.astimezone(NEW_YORK).hour == 17
        assert chunk.end.astimezone(NEW_YORK).hour == 17
        assert chunk.start < chunk.end


def test_friday_and_saturday_are_closure_dates_not_synthetic_chunks() -> None:
    assert not is_fx_session_date(date(2025, 3, 7))
    assert not is_fx_session_date(date(2025, 3, 8))
    assert is_fx_session_date(date(2025, 3, 9))
    with pytest.raises(ValueError, match="not a Sunday-through-Thursday session"):
        fx_session_bounds(date(2025, 3, 8))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda value: value.update({"unknown": True}), "unknown=\\['unknown'\\]"),
        (lambda value: value.pop("probe_id"), "missing=\\['probe_id'\\]"),
        (lambda value: value.update({"schema_version": True}), "schema_version must be int"),
        (lambda value: value.update({"repeat_fetches": 1}), "at least 2"),
        (lambda value: value.update({"chunk_sessions": 2}), "exactly 1"),
        (lambda value: value.update({"purpose": "gate_pass"}), "purpose must be"),
    ],
)
def test_plan_root_fails_closed_on_malformed_values(change: Any, message: str) -> None:
    payload = _payload()
    change(payload)

    with pytest.raises((TypeError, ValueError), match=message):
        parse_plan(payload)


def test_plan_rejects_unknown_window_keys_overlaps_and_weekend_only_windows() -> None:
    unknown = _payload()
    unknown["windows"][0]["timezone"] = "UTC"
    with pytest.raises(ValueError, match="unknown=\\['timezone'\\]"):
        parse_plan(unknown)

    overlap = _payload()
    overlap["windows"].append(
        {
            "id": "overlap",
            "purpose": "test",
            "start_session_date": "2025-03-10",
            "end_session_date_exclusive": "2025-03-14",
        }
    )
    with pytest.raises(ValueError, match="overlap"):
        parse_plan(overlap)

    weekend = _payload()
    weekend["windows"] = [
        {
            "id": "closed",
            "purpose": "test",
            "start_session_date": "2025-03-07",
            "end_session_date_exclusive": "2025-03-09",
        }
    ]
    with pytest.raises(ValueError, match="contains no"):
        parse_plan(weekend)


def test_chunk_request_rejects_an_endpoint_not_derived_from_its_session_date() -> None:
    request = _single_session_plan().chunks[0]

    with pytest.raises(ValueError, match="17:00 New York"):
        replace(request, end=request.end + timedelta(hours=1))


def test_chunk_membership_is_strictly_half_open() -> None:
    request = _single_session_plan().chunks[0]
    last_in_range = _tick(request, _millis(request.end) - _millis(request.start) - 1)

    assert analyse_chunk(request, [_tick(request, 0), last_in_range]).metrics.tick_count == 2
    with pytest.raises(ValueError, match="half-open"):
        analyse_chunk(request, [_tick(request, -1)])
    with pytest.raises(ValueError, match="half-open"):
        analyse_chunk(
            request,
            [
                SourceTick(
                    time=_millis(request.end) // 1000,
                    time_msc=_millis(request.end),
                    bid=Decimal("1"),
                    ask=Decimal("2"),
                    last=Decimal("0"),
                    volume=0,
                    flags=6,
                    volume_real=Decimal("0"),
                )
            ],
        )


def test_quality_metrics_describe_bad_quotes_without_dropping_them() -> None:
    request = _single_session_plan().chunks[0]
    first = _tick(request, 1000, flags=6)
    ticks = [
        first,
        first,
        _tick(request, 2000, bid="1", ask="1", flags=2),
        _tick(request, 1500, bid="1.2", ask="1.1", flags=4),
        _tick(request, 3000, bid="0", ask="1.1", flags=0),
        _tick(request, 4000, bid="1.1", ask="0", flags=0, time_adjustment=1),
    ]

    evidence = analyse_chunk(request, ticks, bid_flag_mask=2, ask_flag_mask=4)
    metrics = evidence.metrics

    assert metrics.tick_count == 6
    assert metrics.both_sides_positive == 4
    assert metrics.positive_spread_quotes == 2
    assert metrics.locked_quotes == 1
    assert metrics.crossed_quotes == 1
    assert metrics.bid_nonpositive == metrics.ask_nonpositive == 1
    assert metrics.timestamp_regressions == 1
    assert metrics.same_millisecond_transitions == 1
    assert metrics.exact_adjacent_duplicates == 1
    assert metrics.time_field_mismatches == 1
    assert metrics.bid_update_flagged == 3
    assert metrics.ask_update_flagged == 3
    assert metrics.both_update_flagged == 2
    assert metrics.neither_update_flagged == 2
    assert metrics.flag_counts == ((0, 2), (2, 1), (4, 1), (6, 2))
    assert evidence.semantic_sha256 == semantic_tick_sha256(ticks)


def test_active_minutes_and_exact_spread_distribution_are_json_friendly() -> None:
    request = _single_session_plan().chunks[0]
    ticks = [
        _tick(request, 1, ask="1.25010"),
        _tick(request, 60_001, ask="1.25020"),
        _tick(request, 60_002, ask="1.25030"),
        _tick(request, 120_001, ask="1.25040"),
        _tick(request, 180_001, ask="1.25050"),
    ]

    metrics = analyse_chunk(request, ticks).metrics

    assert metrics.active_minutes == 4
    assert metrics.positive_spread_min == "0.0001"
    assert metrics.positive_spread_p50 == "0.0003"
    assert metrics.positive_spread_p95 == "0.0005"
    assert metrics.positive_spread_p99 == "0.0005"
    assert metrics.positive_spread_max == "0.0005"
    assert metrics.positive_spread_counts == (
        ("0.0001", 1),
        ("0.0002", 1),
        ("0.0003", 1),
        ("0.0004", 1),
        ("0.0005", 1),
    )
    assert json.loads(json.dumps(asdict(metrics)))["positive_spread_p50"] == "0.0003"


def test_canonical_hash_is_decimal_semantic_but_order_and_duplicates_matter() -> None:
    request = _single_session_plan().chunks[0]
    tick = _tick(request, 1, bid="1.2300", ask="1.24000")
    equivalent = replace(tick, bid=Decimal("1.23"), ask=Decimal("1.24"))
    other = _tick(request, 2)

    assert canonical_tick_bytes([tick]) == canonical_tick_bytes([equivalent])
    assert b"".join(canonical_tick_lines([tick])) == canonical_tick_bytes([tick])
    assert semantic_tick_sha256([tick]) == semantic_tick_sha256([equivalent])
    assert semantic_tick_sha256([tick, tick]) != semantic_tick_sha256([tick])
    assert semantic_tick_sha256([tick, other]) != semantic_tick_sha256([other, tick])


def test_nonfinite_source_values_are_structural_errors_but_bad_finite_values_survive() -> None:
    request = _single_session_plan().chunks[0]
    bad = _tick(request, 1, bid="-1", ask="-2", volume=-3, volume_real="-4")

    metrics = analyse_chunk(request, [bad]).metrics
    assert metrics.tick_count == 1
    assert metrics.bid_nonpositive == metrics.ask_nonpositive == 1
    assert metrics.negative_volume == metrics.negative_volume_real == 1

    with pytest.raises(ValueError, match="finite"):
        replace(bad, bid=Decimal("NaN"))


def test_repeat_fetch_comparison_reports_exact_divergence_location() -> None:
    request = _single_session_plan().chunks[0]
    first = [_tick(request, offset) for offset in (1, 2, 3)]
    semantically_equal = [replace(tick, bid=Decimal("1.250000")) for tick in first]

    equal = compare_repeat_fetches(request, first, semantically_equal)
    assert equal.identical is True
    assert equal.first_sha256 == equal.second_sha256
    assert equal.common_prefix_rows == 3
    assert equal.common_suffix_rows == 0
    assert equal.first_difference_index is None

    changed = [first[0], replace(first[1], ask=Decimal("1.25020")), first[2]]
    comparison = compare_repeat_fetches(request, first, changed)
    assert comparison.identical is False
    assert comparison.common_prefix_rows == comparison.common_suffix_rows == 1
    assert comparison.first_difference_index == 1


def test_empty_completed_chunk_is_evidence_not_an_automatic_defect() -> None:
    plan = _single_session_plan()
    evidence = analyse_chunk(plan.chunks[0], [])

    summary = summarise_dataset(plan, [evidence])

    assert summary.complete is True
    assert summary.total_ticks == 0
    assert summary.active_minutes == 0
    assert summary.empty_chunk_ids == (plan.chunks[0].chunk_id,)
    assert summary.dataset_sha256 is not None
    assert summary.positive_spread_min is None
    assert summary.positive_spread_counts == ()
    assert not hasattr(summary, "status"), "the pure probe must not invent a quality verdict"


def test_partial_dataset_has_no_dataset_hash_and_names_missing_work() -> None:
    plan = _single_session_plan(two_sessions=True)
    evidence = analyse_chunk(plan.chunks[0], [_tick(plan.chunks[0], 1)])

    summary = summarise_dataset(plan, [evidence])

    assert summary.complete is False
    assert summary.dataset_sha256 is None
    assert summary.missing_chunk_ids == (plan.chunks[1].chunk_id,)
    with pytest.raises(ValueError, match="incomplete"):
        dataset_sha256(plan, [evidence])


def test_complete_dataset_hash_uses_plan_order_not_caller_order() -> None:
    plan = _single_session_plan(two_sessions=True)
    first = analyse_chunk(plan.chunks[0], [_tick(plan.chunks[0], 1)])
    second = analyse_chunk(plan.chunks[1], [_tick(plan.chunks[1], 2, ask="1.25020")])

    forward = summarise_dataset(plan, [first, second])
    reversed_input = summarise_dataset(plan, [second, first])

    assert forward.complete is True
    assert forward.active_minutes == 2
    assert forward.positive_spread_min == "0.0001"
    assert forward.positive_spread_p50 == "0.0001"
    assert forward.positive_spread_p95 == "0.0002"
    assert forward.positive_spread_max == "0.0002"
    assert forward.positive_spread_counts == (("0.0001", 1), ("0.0002", 1))
    assert forward.dataset_sha256 == reversed_input.dataset_sha256
    assert forward.dataset_sha256 == dataset_sha256(plan, [second, first])
    changed = analyse_chunk(plan.chunks[1], [_tick(plan.chunks[1], 2, ask="1.25030")])
    assert dataset_sha256(plan, [first, changed]) != forward.dataset_sha256


def test_cross_chunk_summary_includes_measured_boundary_gap() -> None:
    plan = _single_session_plan(two_sessions=True)
    first = analyse_chunk(plan.chunks[0], [_tick(plan.chunks[0], 1000)])
    second = analyse_chunk(plan.chunks[1], [_tick(plan.chunks[1], 2000)])

    summary = summarise_dataset(plan, [first, second])
    assert second.metrics.earliest_time_msc is not None
    assert first.metrics.latest_time_msc is not None
    expected_gap = second.metrics.earliest_time_msc - first.metrics.latest_time_msc

    assert summary.total_ticks == 2
    assert summary.max_intrasession_intertick_gap_milliseconds is None
    assert summary.max_cross_session_gap_milliseconds == expected_gap
    assert summary.max_observed_intertick_gap_milliseconds == expected_gap


def test_cross_chunk_transitions_never_join_different_symbols() -> None:
    payload = _payload()
    payload["windows"] = [
        {
            "id": "sample",
            "purpose": "bounded-test",
            "start_session_date": "2025-03-10",
            "end_session_date_exclusive": "2025-03-11",
        }
    ]
    plan = parse_plan(payload)
    eur, gbp = plan.chunks
    # Plan order finishes EUR before restarting the same interval for GBP. Joining
    # those rows would manufacture a regression at the symbol boundary.
    evidence = [
        analyse_chunk(eur, [_tick(eur, 2000)]),
        analyse_chunk(gbp, [_tick(gbp, 1000)]),
    ]

    summary = summarise_dataset(plan, evidence)

    assert summary.timestamp_regressions == 0
    assert summary.max_cross_session_gap_milliseconds is None
    assert summary.max_observed_intertick_gap_milliseconds is None


def test_cross_chunk_transitions_never_join_discontinuous_windows() -> None:
    payload = _payload()
    payload["symbols"] = {"GBPUSD": "GBPUSD"}
    payload["windows"] = [
        {
            "id": "covid",
            "purpose": "stress",
            "start_session_date": "2020-03-16",
            "end_session_date_exclusive": "2020-03-17",
        },
        {
            "id": "gilt",
            "purpose": "stress",
            "start_session_date": "2022-09-26",
            "end_session_date_exclusive": "2022-09-27",
        },
    ]
    plan = parse_plan(payload)
    covid, gilt = plan.chunks
    evidence = [
        analyse_chunk(covid, [_tick(covid, 1)]),
        analyse_chunk(gilt, [_tick(gilt, 1)]),
    ]

    summary = summarise_dataset(plan, evidence)

    assert summary.timestamp_regressions == 0
    assert summary.max_cross_session_gap_milliseconds is None
    assert summary.max_observed_intertick_gap_milliseconds is None


def test_dataset_summary_rejects_duplicate_unexpected_or_inconsistently_scored_chunks() -> None:
    plan = _single_session_plan()
    request = plan.chunks[0]
    evidence = analyse_chunk(request, [_tick(request, 1)])

    with pytest.raises(ValueError, match="duplicate evidence"):
        summarise_dataset(plan, [evidence, evidence])

    unexpected_request = replace(
        request,
        logical_symbol="EURUSD",
        broker_symbol="EURUSD",
    )
    unexpected = analyse_chunk(unexpected_request, [_tick(unexpected_request, 1)])
    with pytest.raises(ValueError, match="unexpected chunk"):
        summarise_dataset(plan, [unexpected])

    two = _single_session_plan(two_sessions=True)
    one_masks = analyse_chunk(two.chunks[0], [], bid_flag_mask=2, ask_flag_mask=4)
    no_masks = analyse_chunk(two.chunks[1], [])
    with pytest.raises(ValueError, match="same bid/ask flag masks"):
        summarise_dataset(two, [one_masks, no_masks])


def test_plan_hash_changes_when_a_window_or_repeatability_contract_changes() -> None:
    original = parse_plan(_payload())
    changed_window = deepcopy(_payload())
    changed_window["windows"][0]["end_session_date_exclusive"] = "2025-03-13"
    changed_repeat = _payload()
    changed_repeat["repeat_fetches"] = 3

    assert parse_plan(changed_window).plan_hash != original.plan_hash
    assert parse_plan(changed_repeat).plan_hash != original.plan_hash


def test_source_tick_requires_exact_integer_and_decimal_boundary_types() -> None:
    plan = _single_session_plan()
    tick = _tick(plan.chunks[0], 1)

    with pytest.raises(TypeError, match="time must be int"):
        replace(tick, time=True)
    with pytest.raises(TypeError, match="bid must be Decimal"):
        replace(tick, bid=1.25)  # type: ignore[arg-type]


def test_chunk_evidence_request_must_exactly_match_the_plan_not_only_its_id() -> None:
    plan = _single_session_plan()
    evidence = analyse_chunk(plan.chunks[0], [])
    forged_request = replace(plan.chunks[0], index_in_window=99)
    forged = ChunkEvidence(
        request=forged_request,
        semantic_sha256=evidence.semantic_sha256,
        metrics=evidence.metrics,
    )

    with pytest.raises(ValueError, match="does not match plan"):
        summarise_dataset(plan, [forged])
