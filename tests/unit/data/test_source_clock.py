from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from tradebot.data.source_clock import (
    SourceClockExclusion,
    SourceClockInterval,
    SourceClockPolicy,
)


def _utc(label: str) -> datetime:
    return datetime.fromisoformat(label.replace("Z", "+00:00"))


def _payload() -> dict[str, object]:
    # Synthetic fixture only: the excluded transition is deliberately not calibrated.
    return {
        "schema_version": 1,
        "policy_id": "synthetic-october-v1",
        "source": "FBS",
        "instrument": "EURUSD",
        "status": "EXPERIMENTAL_ONLY",
        "label_start": "2024-10-01T00:00:00Z",
        "label_end": "2024-11-01T00:00:00Z",
        "intervals": [
            {
                "label_start": "2024-10-01T00:00:00Z",
                "label_end": "2024-10-27T01:00:00Z",
                "offset_seconds_to_utc": -10800,
            },
            {
                "label_start": "2024-10-27T04:00:00Z",
                "label_end": "2024-11-01T00:00:00Z",
                "offset_seconds_to_utc": -7200,
            },
        ],
        "excluded_intervals": [
            {
                "label_start": "2024-10-27T01:00:00Z",
                "label_end": "2024-10-27T04:00:00Z",
                "reason": "Transition mapping is not established",
            }
        ],
        "evidence_sha256": {"synthetic-anchor-evidence.json": "a" * 64},
    }


def _rules(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], payload["intervals"])


def _policy() -> SourceClockPolicy:
    return SourceClockPolicy.from_dict(_payload())


@pytest.mark.parametrize("day,label_hour", [(4, 15), (10, 15), (30, 14), (31, 14)])
def test_explicit_seasonal_fbs_anchor_mapping_preserves_raw_label(
    day: int, label_hour: int
) -> None:
    policy = _policy()
    label = datetime(2024, 10, day, label_hour, 30, 0, 123456, tzinfo=UTC)
    original = label.isoformat()
    mapped = policy.apply(label, source="FBS", instrument="EURUSD")
    assert mapped == datetime(2024, 10, day, 12, 30, 0, 123456, tzinfo=UTC)
    assert mapped.tzinfo is UTC
    assert label.isoformat() == original


@pytest.mark.parametrize("day,label_hour", [(4, 8), (10, 8), (30, 7), (31, 7)])
def test_histdata_mapping_is_separate_not_a_relative_minus_two_hour_fix(
    day: int,
    label_hour: int,
) -> None:
    payload = _payload()
    payload["source"] = "HistData"
    _rules(payload)[0]["offset_seconds_to_utc"] = 14400
    _rules(payload)[1]["offset_seconds_to_utc"] = 18000
    policy = SourceClockPolicy.from_dict(payload)
    assert policy.apply(
        datetime(2024, 10, day, label_hour, 30, tzinfo=UTC), source="HistData", instrument="EURUSD"
    ) == datetime(2024, 10, day, 12, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "label", ["2024-09-30T23:59:59Z", "2024-11-01T00:00:00Z", "2024-11-02T00:00:00Z"]
)
def test_scope_is_half_open(label: str) -> None:
    with pytest.raises(ValueError, match="outside"):
        _policy().apply(_utc(label), source="FBS", instrument="EURUSD")


@pytest.mark.parametrize(
    "label", ["2024-10-27T01:00:00Z", "2024-10-27T02:30:00Z", "2024-10-27T03:59:59.999999Z"]
)
def test_ambiguous_transition_hole_fails_closed(label: str) -> None:
    with pytest.raises(ValueError, match="no unique clock rule"):
        _policy().apply(_utc(label), source="FBS", instrument="EURUSD")


def test_implicit_holes_also_fail_closed() -> None:
    payload = _payload()
    del payload["excluded_intervals"]
    _rules(payload)[0]["label_start"] = "2024-10-02T00:00:00Z"
    policy = SourceClockPolicy.from_dict(payload)
    for label in ("2024-10-01T12:00:00Z", "2024-10-27T02:30:00Z"):
        with pytest.raises(ValueError, match="no unique clock rule"):
            policy.apply(_utc(label), source="FBS", instrument="EURUSD")


def test_adjacent_rules_have_unambiguous_half_open_boundary() -> None:
    payload = _payload()
    payload["excluded_intervals"] = []
    _rules(payload)[1]["label_start"] = "2024-10-27T01:00:00Z"
    policy = SourceClockPolicy.from_dict(payload)
    boundary = _utc("2024-10-27T01:00:00Z")
    assert policy.apply(boundary, source="FBS", instrument="EURUSD") == boundary - timedelta(
        hours=2
    )
    before = boundary - timedelta(microseconds=1)
    assert policy.apply(before, source="FBS", instrument="EURUSD") == before - timedelta(hours=3)


def test_converted_utc_ranges_may_be_exactly_adjacent() -> None:
    payload = _payload()
    _rules(payload)[1]["offset_seconds_to_utc"] = -21600
    policy = SourceClockPolicy.from_dict(payload)
    first, second = policy.intervals
    first_exclusive_end = first.label_end + timedelta(seconds=first.offset_seconds_to_utc)
    assert (
        policy.apply(second.label_start, source="FBS", instrument="EURUSD") == first_exclusive_end
    )
    before = first.label_end - timedelta(microseconds=1)
    assert policy.apply(before, source="FBS", instrument="EURUSD") < first_exclusive_end


@pytest.mark.parametrize("second_offset", [-21601, -40000])
def test_nonoverlapping_labels_cannot_map_to_overlapping_utc_ranges(second_offset: int) -> None:
    payload = _payload()
    _rules(payload)[1]["offset_seconds_to_utc"] = second_offset
    with pytest.raises(ValueError, match="Converted UTC intervals"):
        SourceClockPolicy.from_dict(payload)


def test_adjacent_labels_cannot_regress_the_converted_clock() -> None:
    payload = _payload()
    payload["excluded_intervals"] = []
    _rules(payload)[1]["label_start"] = _rules(payload)[0]["label_end"]
    _rules(payload)[1]["offset_seconds_to_utc"] = -14400
    with pytest.raises(ValueError, match="Converted UTC intervals"):
        SourceClockPolicy.from_dict(payload)


@pytest.mark.parametrize(
    "source,instrument",
    [("fbs", "EURUSD"), ("HistData", "EURUSD"), ("FBS", "GBPUSD"), ("FBS", "eurusd")],
)
def test_exact_source_and_instrument_scope(source: str, instrument: str) -> None:
    with pytest.raises(ValueError, match="scope does not match"):
        _policy().apply(_utc("2024-10-04T15:30:00Z"), source=source, instrument=instrument)


@pytest.mark.parametrize(
    "label",
    [
        datetime(2024, 10, 4, 15, 30),
        datetime(2024, 10, 4, 15, 30, tzinfo=timezone(timedelta(hours=2))),
        datetime(2024, 10, 30, 15, 30, tzinfo=ZoneInfo("Europe/London")),
        datetime(2024, 10, 4, 15, 30, tzinfo=UTC, fold=1),
    ],
)
def test_naive_non_utc_geographic_and_fold_labels_are_refused(label: datetime) -> None:
    with pytest.raises(ValueError, match=r"UTC carrier|fold"):
        _policy().apply(label, source="FBS", instrument="EURUSD")


def test_fixed_zero_offset_is_normalized_to_utc() -> None:
    label = datetime(2024, 10, 4, 15, 30, tzinfo=timezone(timedelta(0), "fixed-zero"))
    assert _policy().apply(label, source="FBS", instrument="EURUSD").tzinfo is UTC


def test_canonical_roundtrip_and_full_policy_identity() -> None:
    policy = _policy()
    roundtrip = SourceClockPolicy.from_json(policy.to_json())
    assert roundtrip == policy
    assert roundtrip.identity == hashlib.sha256(policy.to_json().encode()).hexdigest()
    assert roundtrip.to_dict() == policy.to_dict()
    assert SourceClockPolicy.from_json(policy.to_json().encode()).identity == policy.identity
    assert SourceClockPolicy.from_json(bytearray(policy.to_json(), "utf-8")) == policy
    equivalent = _payload()
    equivalent["label_start"] = "2024-10-01T00:00:00.000000+00:00"
    equivalent["evidence_sha256"] = {"synthetic-anchor-evidence.json": "A" * 64}
    assert SourceClockPolicy.from_json(json.dumps(equivalent, indent=4)).identity == policy.identity


def test_identity_changes_with_offset_evidence_and_exclusion_reason() -> None:
    policy = _policy()
    changed_offset = replace(
        policy,
        intervals=(replace(policy.intervals[0], offset_seconds_to_utc=-10799), policy.intervals[1]),
    )
    changed_evidence = replace(
        policy, evidence_sha256=(("synthetic-anchor-evidence.json", "b" * 64),)
    )
    changed_exclusion = replace(
        policy,
        excluded_intervals=(replace(policy.excluded_intervals[0], reason="Different evidence"),),
    )
    assert (
        len(
            {
                policy.identity,
                changed_offset.identity,
                changed_evidence.identity,
                changed_exclusion.identity,
            }
        )
        == 4
    )


def test_evidence_order_normalizes_but_every_binding_affects_identity() -> None:
    first = replace(_policy(), evidence_sha256=(("z", "b" * 64), ("a", "a" * 64)))
    second = replace(first, evidence_sha256=tuple(reversed(first.evidence_sha256)))
    assert first.identity == second.identity
    assert first.identity != _policy().identity


def test_policy_and_nested_values_are_immutable_and_input_copies_are_isolated() -> None:
    payload = _payload()
    policy = SourceClockPolicy.from_dict(payload)
    original = policy.to_json()
    _rules(payload)[0]["offset_seconds_to_utc"] = 0
    exported = policy.to_dict()
    exported["evidence_sha256"] = {}
    assert policy.to_json() == original
    for target, name, value in (
        (policy, "source", "changed"),
        (policy.intervals[0], "offset_seconds_to_utc", 0),
        (policy.excluded_intervals[0], "reason", "changed"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, name, value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("status", "APPROVED"),
        ("policy_id", ""),
        ("source", " FBS"),
        ("instrument", "EURUSD "),
        ("label_start", "2024-10-01"),
        ("label_start", "not-a-time"),
        ("label_start", "2024-10-01T00:00:00-00:00"),
        ("label_start", "2024-10-01T00:00:00.000000Z+00:00"),
        ("label_end", "2024-11-01T00:00:00+02:00"),
        ("label_end", "2024-10-01T00:00:00Z"),
        ("label_end", "2024-09-01T00:00:00Z"),
        ("intervals", []),
        ("intervals", {}),
        ("excluded_intervals", {}),
        ("evidence_sha256", {}),
        ("evidence_sha256", []),
        ("evidence_sha256", {"evidence": "x" * 64}),
        ("evidence_sha256", {"evidence": "a" * 63}),
        ("evidence_sha256", {"": "a" * 64}),
    ],
)
def test_invalid_policy_fields_fail_closed(field: str, value: object) -> None:
    payload = _payload()
    payload[field] = value
    with pytest.raises(ValueError):
        SourceClockPolicy.from_dict(payload)


@pytest.mark.parametrize(
    "offset", [True, False, 1.0, "3600", None, -86400, 86400, float("nan"), float("inf")]
)
def test_offset_requires_bounded_non_bool_integer(offset: object) -> None:
    payload = _payload()
    _rules(payload)[0]["offset_seconds_to_utc"] = offset
    with pytest.raises(ValueError, match="offset_seconds_to_utc"):
        SourceClockPolicy.from_dict(payload)


@pytest.mark.parametrize("offset", [-86399, 0, 86399])
def test_whole_second_offset_bounds_are_open(offset: int) -> None:
    policy = _policy()
    rule = replace(policy.intervals[0], offset_seconds_to_utc=offset)
    changed = replace(policy, intervals=(rule,), excluded_intervals=())
    assert changed.apply(rule.label_start, source="FBS", instrument="EURUSD") == (
        rule.label_start + timedelta(seconds=offset)
    )


@pytest.mark.parametrize(
    "index,field,value",
    [
        (0, "label_start", "2024-09-30T00:00:00Z"),
        (1, "label_end", "2024-11-02T00:00:00Z"),
        (0, "label_end", "2024-10-01T00:00:00Z"),
        (0, "label_end", "2024-09-30T00:00:00Z"),
        (1, "label_start", "2024-10-26T00:00:00Z"),
    ],
)
def test_empty_reversed_overlapping_and_out_of_scope_rules(
    index: int,
    field: str,
    value: str,
) -> None:
    payload = _payload()
    _rules(payload)[index][field] = value
    with pytest.raises(ValueError):
        SourceClockPolicy.from_dict(payload)


def test_unsorted_rules_are_not_silently_sorted() -> None:
    payload = _payload()
    payload["intervals"] = list(reversed(_rules(payload)))
    with pytest.raises(ValueError, match="ordered"):
        SourceClockPolicy.from_dict(payload)


@pytest.mark.parametrize(
    "exclusions",
    [
        [
            {
                "label_start": "2024-10-02T00:00:00Z",
                "label_end": "2024-10-03T00:00:00Z",
                "reason": "overlap",
            }
        ],
        [
            {
                "label_start": "2024-09-01T00:00:00Z",
                "label_end": "2024-09-02T00:00:00Z",
                "reason": "outside",
            }
        ],
        [
            {
                "label_start": "2024-10-27T02:00:00Z",
                "label_end": "2024-10-27T02:00:00Z",
                "reason": "empty",
            }
        ],
        [
            {
                "label_start": "2024-10-27T01:00:00Z",
                "label_end": "2024-10-27T03:00:00Z",
                "reason": "first",
            },
            {
                "label_start": "2024-10-27T02:00:00Z",
                "label_end": "2024-10-27T04:00:00Z",
                "reason": "overlap",
            },
        ],
    ],
)
def test_exclusions_must_be_nonoverlapping_holes_inside_scope(exclusions: object) -> None:
    payload = _payload()
    payload["excluded_intervals"] = exclusions
    with pytest.raises(ValueError):
        SourceClockPolicy.from_dict(payload)


@pytest.mark.parametrize("payload", ['{"schema_version":1,"schema_version":1}', "[]", "null", "{"])
def test_json_rejects_duplicates_non_objects_and_malformed_input(payload: str) -> None:
    with pytest.raises(ValueError):
        SourceClockPolicy.from_json(payload)


def test_unknown_missing_and_duplicate_nested_fields_are_rejected() -> None:
    payload = _payload()
    payload["approval"] = True
    with pytest.raises(ValueError, match="unknown"):
        SourceClockPolicy.from_dict(payload)
    del payload["approval"]
    del payload["policy_id"]
    with pytest.raises(ValueError, match="missing"):
        SourceClockPolicy.from_dict(payload)
    text = json.dumps(_payload()).replace(
        '"offset_seconds_to_utc": -10800',
        '"offset_seconds_to_utc": -10800, "offset_seconds_to_utc": 0',
    )
    with pytest.raises(ValueError, match="Duplicate"):
        SourceClockPolicy.from_json(text)


def test_direct_construction_cannot_admit_mutable_or_invalid_nested_values() -> None:
    policy = _policy()
    with pytest.raises(ValueError, match="immutable tuple"):
        replace(policy, intervals=cast(tuple[SourceClockInterval, ...], list(policy.intervals)))
    with pytest.raises(ValueError, match="immutable tuple"):
        replace(policy, excluded_intervals=cast(tuple[SourceClockExclusion, ...], []))
    with pytest.raises(ValueError, match="unique names"):
        replace(policy, evidence_sha256=policy.evidence_sha256 * 2)
    with pytest.raises(ValueError, match="integer"):
        replace(policy.intervals[0], offset_seconds_to_utc=True)


def test_datetime_overflow_fails_without_wrapping() -> None:
    start = datetime.min.replace(tzinfo=UTC)
    end = start + timedelta(days=1)
    policy = replace(
        _policy(),
        label_start=start,
        label_end=end,
        excluded_intervals=(),
        intervals=(
            SourceClockInterval(label_start=start, label_end=end, offset_seconds_to_utc=-1),
        ),
    )
    with pytest.raises(ValueError, match="datetime range"):
        policy.apply(start, source="FBS", instrument="EURUSD")
