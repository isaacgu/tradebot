"""Reference-month liquid-hours quality acceptance (SPEC 4.6).

The evaluator compares immutable one-minute clean bars with a point-in-time
``ExpectedLiquidityCalendar``.  It deliberately keeps three questions separate:

* which minute bins an approved calendar expected to be liquid;
* which observed bars carried each causal or retrospective flag; and
* whether the hash-bound calendar and counted-flag policy have the required human
  decision records.

No boolean in a calendar or policy file is treated as approval.  Missing calendar
days, an empty denominator, an incomplete retrospective view, unresolved flag
treatments, or absent decision bindings make the result ``INDETERMINATE``.
"""

from __future__ import annotations

import calendar as month_calendar
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from tradebot.core.time_rules import NEW_YORK, fx_session_bounds
from tradebot.core.timestamps import require_utc
from tradebot.data.calendar import ExpectedLiquidityCalendar, LiquidityStatus
from tradebot.data.storage import (
    CLEAN_BAR_SCHEMA,
    CLEAN_TICK_SCHEMA,
    parquet_metadata,
    sha256_path,
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")
_MINUTE = timedelta(minutes=1)


class ReferenceAcceptanceError(ValueError):
    """The supplied evidence violates the evaluator contract."""


class AcceptanceStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INDETERMINATE = "INDETERMINATE"


class FlagSource(StrEnum):
    CAUSAL_BAR = "CAUSAL_BAR"
    RETROSPECTIVE_TICK = "RETROSPECTIVE_TICK"


class FlagClass(StrEnum):
    CAUSAL_DEFECT = "CAUSAL_DEFECT"
    CALENDAR_UNKNOWN = "CALENDAR_UNKNOWN"
    WARMUP = "WARMUP"
    PROVENANCE = "PROVENANCE"
    RETROSPECTIVE_QA = "RETROSPECTIVE_QA"


class FlagTreatment(StrEnum):
    COUNT_AS_FLAGGED = "COUNT_AS_FLAGGED"
    EXCLUDE_FROM_NUMERATOR = "EXCLUDE_FROM_NUMERATOR"
    INDETERMINATE_IF_PRESENT = "INDETERMINATE_IF_PRESENT"
    UNRESOLVED = "UNRESOLVED"


class MissingBarTreatment(StrEnum):
    COUNT_AS_FLAGGED = "COUNT_AS_FLAGGED"
    INDETERMINATE = "INDETERMINATE"
    UNRESOLVED = "UNRESOLVED"


def _nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceAcceptanceError(f"{field} must be a non-empty string")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ReferenceAcceptanceError(f"{field} must be a lowercase SHA-256")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReferenceAcceptanceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReferenceAcceptanceError(f"non-finite JSON constant is forbidden: {value}")


def _json(payload: bytes, *, label: str) -> object:
    try:
        return json.loads(
            payload,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ReferenceAcceptanceError(f"{label} is not valid JSON") from exc


def _month_days(reference_month: str) -> tuple[date, ...]:
    if not isinstance(reference_month, str) or _MONTH.fullmatch(reference_month) is None:
        raise ReferenceAcceptanceError("reference_month must be canonical YYYY-MM")
    year, month = (int(part) for part in reference_month.split("-"))
    return tuple(
        date(year, month, day) for day in range(1, month_calendar.monthrange(year, month)[1] + 1)
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceScope:
    """Exact venue/symbol/source and canonical session-close month under review."""

    venue: str
    source: str
    instrument: str
    calendar_instrument: str
    reference_month: str

    def __post_init__(self) -> None:
        for field in ("venue", "source", "instrument", "calendar_instrument"):
            _nonempty(getattr(self, field), field)
        _month_days(self.reference_month)


@dataclass(frozen=True, slots=True, kw_only=True)
class MinuteBar:
    """Minimal immutable clean-bar view used by the acceptance calculation."""

    venue: str
    source: str
    instrument: str
    ts_open: datetime
    ts_close: datetime
    quality_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("venue", "source", "instrument"):
            _nonempty(getattr(self, field), field)
        start = require_utc(self.ts_open, field="ts_open")
        end = require_utc(self.ts_close, field="ts_close")
        if start.second or start.microsecond or end != start + _MINUTE:
            raise ReferenceAcceptanceError("each observation must be one aligned UTC minute")
        if not isinstance(self.quality_flags, tuple):
            raise ReferenceAcceptanceError("quality_flags must be an immutable tuple")
        if len(set(self.quality_flags)) != len(self.quality_flags):
            raise ReferenceAcceptanceError("quality_flags must not contain duplicates")
        for flag in self.quality_flags:
            _nonempty(flag, "quality flag")


@dataclass(frozen=True, slots=True, kw_only=True)
class FlagRule:
    name: str
    source: FlagSource
    classification: FlagClass
    treatment: FlagTreatment
    rationale: str

    def __post_init__(self) -> None:
        _nonempty(self.name, "flag rule name")
        _nonempty(self.rationale, "flag rule rationale")
        if not isinstance(self.source, FlagSource):
            raise ReferenceAcceptanceError("flag rule source is invalid")
        if not isinstance(self.classification, FlagClass):
            raise ReferenceAcceptanceError("flag rule classification is invalid")
        if not isinstance(self.treatment, FlagTreatment):
            raise ReferenceAcceptanceError("flag rule treatment is invalid")
        if self.source == FlagSource.RETROSPECTIVE_TICK and (
            self.classification != FlagClass.RETROSPECTIVE_QA
        ):
            raise ReferenceAcceptanceError(
                "retrospective flags must be classified RETROSPECTIVE_QA"
            )
        if self.classification == FlagClass.RETROSPECTIVE_QA and (
            self.source != FlagSource.RETROSPECTIVE_TICK
        ):
            raise ReferenceAcceptanceError("RETROSPECTIVE_QA rules must use retrospective evidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceMonthPolicy:
    """Hash-identified counted-flag policy; status text alone is never approval."""

    policy_id: str
    status: str
    scope: ReferenceScope
    missing_expected_bar_treatment: MissingBarTreatment
    rules: tuple[FlagRule, ...]
    sha256: str
    comparison: str = "STRICTLY_LESS_THAN"
    threshold: str = "0.001"
    denominator: str = "EXPECTED_LIQUID_MINUTE_BINS"
    interval_membership: str = "BAR_INTERVAL_FULLY_CONTAINED"

    def __post_init__(self) -> None:
        _nonempty(self.policy_id, "policy_id")
        if self.status not in ("DRAFT_UNAPPROVED", "APPROVED"):
            raise ReferenceAcceptanceError("policy status must be DRAFT_UNAPPROVED or APPROVED")
        _digest(self.sha256, "policy sha256")
        if self.comparison != "STRICTLY_LESS_THAN" or self.threshold != "0.001":
            raise ReferenceAcceptanceError("SPEC 4.6 requires a strict threshold of 0.001")
        if self.denominator != "EXPECTED_LIQUID_MINUTE_BINS":
            raise ReferenceAcceptanceError("unsupported reference-month denominator")
        if self.interval_membership != "BAR_INTERVAL_FULLY_CONTAINED":
            raise ReferenceAcceptanceError("unsupported interval-membership rule")
        if not isinstance(self.missing_expected_bar_treatment, MissingBarTreatment):
            raise ReferenceAcceptanceError("missing-bar treatment is invalid")
        if not isinstance(self.rules, tuple) or not self.rules:
            raise ReferenceAcceptanceError("policy must explicitly list flag rules")
        keys = [(rule.source, rule.name) for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ReferenceAcceptanceError("policy contains duplicate flag rules")
        if not any(rule.classification == FlagClass.PROVENANCE for rule in self.rules):
            raise ReferenceAcceptanceError("policy must explicitly classify provenance flags")
        if not any(rule.source == FlagSource.RETROSPECTIVE_TICK for rule in self.rules):
            raise ReferenceAcceptanceError("policy must explicitly classify retrospective flags")
        if self.status == "APPROVED" and (
            self.missing_expected_bar_treatment == MissingBarTreatment.UNRESOLVED
            or any(rule.treatment == FlagTreatment.UNRESOLVED for rule in self.rules)
        ):
            raise ReferenceAcceptanceError(
                "an approved policy cannot contain unresolved treatments"
            )

    @property
    def rule_map(self) -> Mapping[tuple[FlagSource, str], FlagRule]:
        return {(rule.source, rule.name): rule for rule in self.rules}


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalDecision:
    """One hash-bound human decision artifact, not an authenticated identity claim."""

    role: str
    person: str
    decision: str
    decided_at_utc: datetime
    artifact_path: str
    artifact_sha256: str

    def __post_init__(self) -> None:
        if self.role not in ("INDEPENDENT_REVIEWER", "PRINCIPAL"):
            raise ReferenceAcceptanceError("approval role is invalid")
        _nonempty(self.person, "approval person")
        if self.decision != "APPROVED":
            raise ReferenceAcceptanceError("decision artifacts must explicitly say APPROVED")
        require_utc(self.decided_at_utc, field="decided_at_utc")
        _nonempty(self.artifact_path, "approval artifact path")
        _digest(self.artifact_sha256, "approval artifact sha256")


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalBinding:
    """Links distinct reviewer and Principal artifacts to exact calendar/policy bytes."""

    scope: ReferenceScope
    calendar_sha256: str
    policy_sha256: str
    independent_review: ApprovalDecision
    principal_approval: ApprovalDecision

    def __post_init__(self) -> None:
        _digest(self.calendar_sha256, "approval calendar_sha256")
        _digest(self.policy_sha256, "approval policy_sha256")
        if self.independent_review.role != "INDEPENDENT_REVIEWER":
            raise ReferenceAcceptanceError("independent-review role binding is invalid")
        if self.principal_approval.role != "PRINCIPAL":
            raise ReferenceAcceptanceError("principal role binding is invalid")
        if self.independent_review.person.casefold() == self.principal_approval.person.casefold():
            raise ReferenceAcceptanceError("reviewer and Principal must be different humans")
        if self.principal_approval.decided_at_utc < self.independent_review.decided_at_utc:
            raise ReferenceAcceptanceError("Principal decision cannot predate independent review")

    def mismatch_reasons(
        self,
        *,
        scope: ReferenceScope,
        calendar_sha256: str | None,
        policy_sha256: str,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.scope != scope:
            reasons.append("approval binding scope does not match the evaluation")
        if calendar_sha256 is None or self.calendar_sha256 != calendar_sha256:
            reasons.append("approval binding does not match the calendar bytes")
        if self.policy_sha256 != policy_sha256:
            reasons.append("approval binding does not match the policy bytes")
        return tuple(reasons)


@dataclass(frozen=True, slots=True, kw_only=True)
class FileEvidence:
    path: str
    sha256: str
    rows: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LoadedBars:
    bars: tuple[MinuteBar, ...]
    files: tuple[FileEvidence, ...]
    corpus_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class LoadedRetrospectiveFlags:
    causal_flags_by_minute: Mapping[datetime, tuple[str, ...]]
    flags_by_minute: Mapping[datetime, tuple[str, ...]]
    covered_minutes: frozenset[datetime]
    files: tuple[FileEvidence, ...]
    corpus_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProducerInventoryEvidence:
    """Identity of a producer report whose sidecar and full clean inventory were verified."""

    report_sha256: str
    sidecar_sha256: str
    corpus_id: str

    def __post_init__(self) -> None:
        _digest(self.report_sha256, "producer report sha256")
        _digest(self.sidecar_sha256, "producer sidecar sha256")
        _digest(self.corpus_id, "producer corpus_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceMonthResult:
    status: AcceptanceStatus
    reasons: tuple[str, ...]
    scope: ReferenceScope
    known_at_utc: datetime
    policy_id: str
    policy_status: str
    policy_sha256: str
    calendar_sha256: str | None
    producer_inventory_sha256: str | None
    approval_binding_verified: bool
    human_identities_authenticated: bool
    calendar_days_in_month: int
    calendar_days_resolved: int
    calendar_days_missing: tuple[str, ...]
    expected_liquid_minutes: int
    actual_bars_in_close_month: int
    observed_expected_minutes: int
    missing_expected_minutes: int
    unexpected_actual_minutes: int
    tick_minutes_covered: int
    actual_bar_minutes_without_tick_coverage: int
    causal_tick_flagged_minutes_without_bar: int
    retrospective_flagged_minutes_without_bar: int
    observed_counted_flag_union: int
    missing_counted_as_flagged: int
    counted_flagged_union: int
    flagged_fraction: str | None
    strict_less_than_0_1_percent: bool | None
    flag_observations: tuple[Mapping[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["status"] = self.status.value
        result["known_at_utc"] = self.known_at_utc.isoformat()
        result["scope"] = asdict(self.scope)
        result["gate_approved"] = False
        result["criterion"] = {
            "comparison": "STRICTLY_LESS_THAN",
            "threshold_fraction": "0.001",
            "threshold_percent": "0.1",
            "denominator": "EXPECTED_LIQUID_MINUTE_BINS",
            "overlap_treatment": "UNION_EACH_MINUTE_COUNTED_ONCE",
        }
        return result


def _canonical_close_date(instant: datetime) -> date:
    bounds = fx_session_bounds(require_utc(instant))
    if bounds is None:
        raise ReferenceAcceptanceError(
            f"bar/tick minute {instant.isoformat()} is outside the canonical FX session"
        )
    return bounds[1].astimezone(NEW_YORK).date()


def _calendar_minutes(
    *,
    calendar: ExpectedLiquidityCalendar | None,
    scope: ReferenceScope,
    known_at: datetime,
) -> tuple[set[datetime], tuple[str, ...], int]:
    days = _month_days(scope.reference_month)
    if calendar is None:
        return set(), tuple(day.isoformat() for day in days), 0
    expected: set[datetime] = set()
    missing: list[str] = []
    resolved = 0
    for day in days:
        entry = calendar.lookup(scope.calendar_instrument, day, known_at=known_at)
        if entry is None:
            missing.append(day.isoformat())
            continue
        resolved += 1
        if entry.status == LiquidityStatus.CLOSED:
            continue
        for start, end in entry.expected_intervals:
            start = require_utc(start, field="expected interval start")
            end = require_utc(end, field="expected interval end")
            if start.second or start.microsecond or end.second or end.microsecond:
                raise ReferenceAcceptanceError(
                    "expected liquidity intervals must be minute-aligned"
                )
            bounds = fx_session_bounds(start)
            if bounds is None or end > bounds[1]:
                raise ReferenceAcceptanceError(
                    "expected interval is outside one canonical FX session"
                )
            if bounds[1].astimezone(NEW_YORK).date() != day:
                raise ReferenceAcceptanceError(
                    "expected interval session-close date disagrees with its calendar key"
                )
            cursor = start
            while cursor < end:
                if cursor in expected:
                    raise ReferenceAcceptanceError("calendar minute intervals overlap")
                expected.add(cursor)
                cursor += _MINUTE
    return expected, tuple(missing), resolved


def evaluate_reference_month(
    *,
    scope: ReferenceScope,
    bars: Iterable[MinuteBar],
    calendar: ExpectedLiquidityCalendar | None,
    calendar_sha256: str | None,
    policy: ReferenceMonthPolicy,
    known_at: datetime,
    approval: ApprovalBinding | None,
    producer_inventory: ProducerInventoryEvidence | None,
    causal_tick_flags_by_minute: Mapping[datetime, Sequence[str]] | None,
    retrospective_flags_by_minute: Mapping[datetime, Sequence[str]] | None,
    tick_covered_minutes: Iterable[datetime] | None,
) -> ReferenceMonthResult:
    """Evaluate one exact venue/symbol/month without manufacturing missing evidence."""

    known_at = require_utc(known_at, field="known_at")
    if policy.scope != scope:
        raise ReferenceAcceptanceError("policy scope does not match the requested evaluation")
    if calendar_sha256 is not None:
        _digest(calendar_sha256, "calendar sha256")

    expected, missing_days, resolved_days = _calendar_minutes(
        calendar=calendar, scope=scope, known_at=known_at
    )
    actual: dict[datetime, MinuteBar] = {}
    for bar in bars:
        if (bar.venue, bar.source, bar.instrument) != (
            scope.venue,
            scope.source,
            scope.instrument,
        ):
            raise ReferenceAcceptanceError("bar venue/source/instrument is outside the exact scope")
        if _canonical_close_date(bar.ts_open).strftime("%Y-%m") != scope.reference_month:
            continue
        bounds = fx_session_bounds(bar.ts_open)
        if bounds is None or bar.ts_close > bounds[1]:
            raise ReferenceAcceptanceError("minute bar crosses a canonical FX-session boundary")
        if bar.ts_open in actual:
            raise ReferenceAcceptanceError("duplicate actual minute bar in reference-month scope")
        actual[bar.ts_open] = bar

    def normalize_flags(
        values: Mapping[datetime, Sequence[str]] | None, *, label: str
    ) -> dict[datetime, tuple[str, ...]] | None:
        if values is None:
            return None
        normalized_map: dict[datetime, tuple[str, ...]] = {}
        for minute, flags in values.items():
            minute = require_utc(minute, field="retrospective minute")
            if minute.second or minute.microsecond:
                raise ReferenceAcceptanceError(f"{label} evidence keys must be aligned minutes")
            normalized = tuple(flags)
            if len(set(normalized)) != len(normalized):
                raise ReferenceAcceptanceError(f"{label} flag evidence contains duplicates")
            for flag in normalized:
                _nonempty(flag, f"{label} flag")
            bounds = fx_session_bounds(minute)
            if (
                bounds is not None
                and bounds[1].astimezone(NEW_YORK).strftime("%Y-%m") == scope.reference_month
            ):
                normalized_map[minute] = normalized
        return normalized_map

    causal_ticks = normalize_flags(causal_tick_flags_by_minute, label="causal tick")
    retrospective = normalize_flags(retrospective_flags_by_minute, label="retrospective")
    coverage: set[datetime] | None = None
    if tick_covered_minutes is not None:
        coverage = set()
        for minute in tick_covered_minutes:
            minute = require_utc(minute, field="tick coverage minute")
            if minute.second or minute.microsecond:
                raise ReferenceAcceptanceError(
                    "tick coverage evidence keys must be aligned minutes"
                )
            bounds = fx_session_bounds(minute)
            if (
                bounds is not None
                and bounds[1].astimezone(NEW_YORK).strftime("%Y-%m") == scope.reference_month
            ):
                coverage.add(minute)

    rules = policy.rule_map
    observed_by_rule: dict[tuple[FlagSource, str], set[datetime]] = defaultdict(set)
    unknown_by_rule: dict[tuple[FlagSource, str], set[datetime]] = defaultdict(set)
    counted_observed: set[datetime] = set()
    unresolved_observed: set[datetime] = set()
    evidence_minutes = set(actual)
    if causal_ticks is not None:
        evidence_minutes.update(causal_ticks)
    if retrospective is not None:
        evidence_minutes.update(retrospective)
    for minute in evidence_minutes:
        evidence_bar = actual.get(minute)
        for source, flags in (
            (
                FlagSource.CAUSAL_BAR,
                tuple(
                    sorted(
                        (set() if evidence_bar is None else set(evidence_bar.quality_flags))
                        | (set() if causal_ticks is None else set(causal_ticks.get(minute, ())))
                    )
                ),
            ),
            (
                FlagSource.RETROSPECTIVE_TICK,
                () if retrospective is None else retrospective.get(minute, ()),
            ),
        ):
            for flag in flags:
                key = (source, flag)
                observed_by_rule[key].add(minute)
                rule = rules.get(key)
                if minute not in expected:
                    continue
                if rule is None:
                    unknown_by_rule[key].add(minute)
                    unresolved_observed.add(minute)
                elif rule.treatment == FlagTreatment.COUNT_AS_FLAGGED:
                    counted_observed.add(minute)
                elif rule.treatment in (
                    FlagTreatment.INDETERMINATE_IF_PRESENT,
                    FlagTreatment.UNRESOLVED,
                ):
                    unresolved_observed.add(minute)

    causal_orphans = (
        set()
        if causal_ticks is None
        else {minute for minute, flags in causal_ticks.items() if flags} - set(actual)
    )
    retrospective_orphans = (
        set()
        if retrospective is None
        else {minute for minute, flags in retrospective.items() if flags} - set(actual)
    )
    observed_expected = set(actual) & expected
    missing_expected = expected - set(actual)
    unexpected_actual = set(actual) - expected
    counted_observed &= expected
    missing_counted: set[datetime] = set()
    if policy.missing_expected_bar_treatment == MissingBarTreatment.COUNT_AS_FLAGGED:
        missing_counted = missing_expected
    counted_union = counted_observed | missing_counted

    reasons: list[str] = []
    days = _month_days(scope.reference_month)
    if calendar is None:
        reasons.append("no expected-liquidity calendar was supplied")
    if missing_days:
        reasons.append(
            f"expected-liquidity calendar is unknown for {len(missing_days)} month day(s)"
        )
    if not expected:
        reasons.append("approved expected-liquid minute denominator is empty")
    if policy.status != "APPROVED":
        reasons.append("counted-flag policy is draft/unapproved")
    if policy.missing_expected_bar_treatment == MissingBarTreatment.UNRESOLVED:
        reasons.append("missing expected-bar treatment is unresolved")
    if any(rule.treatment == FlagTreatment.UNRESOLVED for rule in policy.rules):
        reasons.append("one or more flag-class treatments are unresolved")
    if producer_inventory is None:
        reasons.append("a sidecar-pinned complete producer clean-file inventory was not verified")
    if causal_ticks is None:
        reasons.append(
            "causal tick flag evidence was not supplied; excluded-quote minutes cannot be certified"
        )
    if retrospective is None:
        reasons.append("retrospective flag evidence was not supplied")
    if coverage is None:
        uncovered_actual = set(actual)
        reasons.append("tick-minute coverage evidence was not supplied")
    else:
        uncovered_actual = set(actual) - coverage
        if uncovered_actual:
            reasons.append(
                f"tick evidence does not cover {len(uncovered_actual)} observed bar minute(s)"
            )
    if unknown_by_rule:
        reasons.append("observed flags are absent from the counted-flag policy")
    if unresolved_observed:
        reasons.append("observed flag treatment requires an indeterminate result")
    if missing_expected and policy.missing_expected_bar_treatment in (
        MissingBarTreatment.INDETERMINATE,
        MissingBarTreatment.UNRESOLVED,
    ):
        reasons.append("one or more expected liquid minute bars are missing")
    approval_verified = False
    if approval is None:
        reasons.append("hash-bound independent-review and Principal approval are absent")
    else:
        mismatches = approval.mismatch_reasons(
            scope=scope,
            calendar_sha256=calendar_sha256,
            policy_sha256=policy.sha256,
        )
        reasons.extend(mismatches)
        approval_verified = not mismatches

    denominator = len(expected)
    numerator = len(counted_union)
    fraction = None if denominator == 0 else str(Decimal(numerator) / Decimal(denominator))
    strict = None if denominator == 0 else numerator * 1000 < denominator
    if reasons:
        status = AcceptanceStatus.INDETERMINATE
    elif strict is False:
        status = AcceptanceStatus.FAILED
        reasons.append("counted flagged-bar fraction is not strictly below 0.1%")
    else:
        status = AcceptanceStatus.PASSED
        reasons.append("counted flagged-bar fraction is strictly below 0.1%")

    observations: list[Mapping[str, object]] = []
    all_keys = sorted(set(rules) | set(observed_by_rule), key=lambda item: (item[0].value, item[1]))
    for key in all_keys:
        rule = rules.get(key)
        observations.append(
            {
                "source": key[0].value,
                "flag": key[1],
                "classification": None if rule is None else rule.classification.value,
                "treatment": None if rule is None else rule.treatment.value,
                "observed_bars": len(observed_by_rule.get(key, set())),
                "in_policy": rule is not None,
            }
        )

    return ReferenceMonthResult(
        status=status,
        reasons=tuple(dict.fromkeys(reasons)),
        scope=scope,
        known_at_utc=known_at,
        policy_id=policy.policy_id,
        policy_status=policy.status,
        policy_sha256=policy.sha256,
        calendar_sha256=calendar_sha256,
        producer_inventory_sha256=(
            None if producer_inventory is None else producer_inventory.report_sha256
        ),
        approval_binding_verified=approval_verified,
        human_identities_authenticated=False,
        calendar_days_in_month=len(days),
        calendar_days_resolved=resolved_days,
        calendar_days_missing=missing_days,
        expected_liquid_minutes=denominator,
        actual_bars_in_close_month=len(actual),
        observed_expected_minutes=len(observed_expected),
        missing_expected_minutes=len(missing_expected),
        unexpected_actual_minutes=len(unexpected_actual),
        tick_minutes_covered=0 if coverage is None else len(coverage),
        actual_bar_minutes_without_tick_coverage=len(uncovered_actual),
        causal_tick_flagged_minutes_without_bar=len(causal_orphans),
        retrospective_flagged_minutes_without_bar=len(retrospective_orphans),
        observed_counted_flag_union=len(counted_observed),
        missing_counted_as_flagged=len(missing_counted),
        counted_flagged_union=numerator,
        flagged_fraction=fraction,
        strict_less_than_0_1_percent=strict,
        flag_observations=tuple(observations),
    )


def _strict_object(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ReferenceAcceptanceError(f"{label} must contain exactly {sorted(keys)}")
    return cast(dict[str, Any], value)


def _scope_from(value: object, label: str = "scope") -> ReferenceScope:
    row = _strict_object(
        value,
        {"venue", "source", "instrument", "calendar_instrument", "reference_month"},
        label,
    )
    return ReferenceScope(**row)


def read_policy(path: Path) -> ReferenceMonthPolicy:
    """Read an exact-schema JSON policy and bind the object to its file bytes."""

    payload_bytes = path.read_bytes()
    payload = _json(payload_bytes, label="policy")
    row = _strict_object(
        payload,
        {"schema_version", "policy_id", "status", "scope", "criterion", "flag_rules"},
        "policy",
    )
    if type(row["schema_version"]) is not int or row["schema_version"] != 1:
        raise ReferenceAcceptanceError("unsupported policy schema")
    criterion = _strict_object(
        row["criterion"],
        {
            "comparison",
            "threshold",
            "denominator",
            "interval_membership",
            "missing_expected_bar_treatment",
        },
        "policy.criterion",
    )
    raw_rules = row["flag_rules"]
    if not isinstance(raw_rules, list):
        raise ReferenceAcceptanceError("policy.flag_rules must be a list")
    rules: list[FlagRule] = []
    for index, value in enumerate(raw_rules):
        item = _strict_object(
            value,
            {"name", "source", "classification", "treatment", "rationale"},
            f"policy.flag_rules[{index}]",
        )
        rules.append(
            FlagRule(
                name=item["name"],
                source=FlagSource(item["source"]),
                classification=FlagClass(item["classification"]),
                treatment=FlagTreatment(item["treatment"]),
                rationale=item["rationale"],
            )
        )
    return ReferenceMonthPolicy(
        policy_id=row["policy_id"],
        status=row["status"],
        scope=_scope_from(row["scope"], "policy.scope"),
        missing_expected_bar_treatment=MissingBarTreatment(
            criterion["missing_expected_bar_treatment"]
        ),
        rules=tuple(rules),
        sha256=hashlib.sha256(payload_bytes).hexdigest(),
        comparison=criterion["comparison"],
        threshold=criterion["threshold"],
        denominator=criterion["denominator"],
        interval_membership=criterion["interval_membership"],
    )


def _decision_from(value: object, role: str, repository: Path) -> ApprovalDecision:
    row = _strict_object(
        value,
        {"role", "person", "decision", "decided_at_utc", "artifact_path", "artifact_sha256"},
        role.lower(),
    )
    if row["role"] != role:
        raise ReferenceAcceptanceError(f"{role} decision has the wrong role")
    relative = Path(row["artifact_path"])
    if relative.is_absolute():
        raise ReferenceAcceptanceError("approval artifact path must be repository-relative")
    resolved = (repository / relative).resolve()
    if not resolved.is_relative_to(repository.resolve()) or resolved.is_symlink():
        raise ReferenceAcceptanceError("approval artifact escapes the repository")
    expected = _digest(row["artifact_sha256"], "approval artifact sha256")
    if sha256_path(resolved) != expected:
        raise ReferenceAcceptanceError("approval artifact checksum mismatch")
    return ApprovalDecision(
        role=row["role"],
        person=row["person"],
        decision=row["decision"],
        decided_at_utc=datetime.fromisoformat(row["decided_at_utc"]),
        artifact_path=relative.as_posix(),
        artifact_sha256=expected,
    )


def read_approval_binding(path: Path, *, repository: Path) -> ApprovalBinding:
    """Read hash-bound decision records; an ``approved: true`` shortcut is rejected."""

    payload = _json(path.read_bytes(), label="approval binding")
    row = _strict_object(
        payload,
        {
            "schema_version",
            "scope",
            "calendar_sha256",
            "policy_sha256",
            "independent_review",
            "principal_approval",
        },
        "approval binding",
    )
    if type(row["schema_version"]) is not int or row["schema_version"] != 1:
        raise ReferenceAcceptanceError("unsupported approval-binding schema")
    return ApprovalBinding(
        scope=_scope_from(row["scope"], "approval.scope"),
        calendar_sha256=row["calendar_sha256"],
        policy_sha256=row["policy_sha256"],
        independent_review=_decision_from(
            row["independent_review"], "INDEPENDENT_REVIEWER", repository
        ),
        principal_approval=_decision_from(row["principal_approval"], "PRINCIPAL", repository),
    )


def _check_parquet_metadata(path: Path, *, scope: ReferenceScope, kind: str) -> Mapping[str, str]:
    metadata = parquet_metadata(path)
    expected = {
        "tradebot.schema": "clean-bar-v2" if kind == "bar" else "clean-tick-v2",
        "tradebot.kind": "clean-bar" if kind == "bar" else "clean-tick",
        "tradebot.venue": scope.venue,
        "tradebot.instrument": scope.instrument,
        "tradebot.source": scope.source,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ReferenceAcceptanceError(f"{path} metadata {key} does not match exact scope")
    if kind == "bar" and metadata.get("tradebot.timeframe") != "1m":
        raise ReferenceAcceptanceError(f"{path} is not a one-minute clean-bar file")
    corpus = metadata.get("tradebot.corpus_id")
    if corpus is None or _SHA256.fullmatch(corpus) is None:
        raise ReferenceAcceptanceError(f"{path} has no valid corpus identity")
    return metadata


def read_clean_bar_files(paths: Sequence[Path], *, scope: ReferenceScope) -> LoadedBars:
    """Read and hash exact-scope immutable one-minute Parquet evidence."""

    records: list[MinuteBar] = []
    evidence: list[FileEvidence] = []
    corpus_ids: set[str] = set()
    for path in sorted({item.resolve() for item in paths}, key=lambda item: item.as_posix()):
        before = sha256_path(path)
        metadata = _check_parquet_metadata(path, scope=scope, kind="bar")
        parquet = pq.ParquetFile(path)
        if not parquet.schema_arrow.equals(CLEAN_BAR_SCHEMA, check_metadata=False):
            raise ReferenceAcceptanceError(f"{path} does not have the exact clean-bar-v2 schema")
        rows = 0
        for batch in parquet.iter_batches(
            columns=("instrument", "ts_open", "ts_close", "source", "quality_flags"),
            batch_size=65_536,
        ):
            for row in batch.to_pylist():
                records.append(
                    MinuteBar(
                        venue=scope.venue,
                        source=cast(str, row["source"]),
                        instrument=cast(str, row["instrument"]),
                        ts_open=cast(datetime, row["ts_open"]),
                        ts_close=cast(datetime, row["ts_close"]),
                        quality_flags=tuple(cast(list[str], row["quality_flags"])),
                    )
                )
                rows += 1
        after = sha256_path(path)
        if before != after:
            raise ReferenceAcceptanceError(f"clean-bar evidence changed while reading: {path}")
        evidence.append(FileEvidence(path=path.as_posix(), sha256=before, rows=rows))
        corpus_ids.add(metadata["tradebot.corpus_id"])
    return LoadedBars(
        bars=tuple(records), files=tuple(evidence), corpus_ids=tuple(sorted(corpus_ids))
    )


def read_retrospective_tick_files(
    paths: Sequence[Path], *, scope: ReferenceScope
) -> LoadedRetrospectiveFlags:
    """Read future-informed tick annotations separately from causal bar flags."""

    causal_flags: dict[datetime, set[str]] = defaultdict(set)
    retrospective_flags: dict[datetime, set[str]] = defaultdict(set)
    covered: set[datetime] = set()
    evidence: list[FileEvidence] = []
    corpus_ids: set[str] = set()
    for path in sorted({item.resolve() for item in paths}, key=lambda item: item.as_posix()):
        before = sha256_path(path)
        metadata = _check_parquet_metadata(path, scope=scope, kind="tick")
        parquet = pq.ParquetFile(path)
        if not parquet.schema_arrow.equals(CLEAN_TICK_SCHEMA, check_metadata=False):
            raise ReferenceAcceptanceError(f"{path} does not have the exact clean-tick-v2 schema")
        rows = 0
        for batch in parquet.iter_batches(
            columns=(
                "instrument",
                "ts_event",
                "source",
                "quality_flags",
                "retrospective_flags",
            ),
            batch_size=65_536,
        ):
            for row in batch.to_pylist():
                if (row["instrument"], row["source"]) != (scope.instrument, scope.source):
                    raise ReferenceAcceptanceError("clean-tick row is outside the exact scope")
                moment = require_utc(cast(datetime, row["ts_event"]), field="tick ts_event")
                bounds = fx_session_bounds(moment)
                if (
                    bounds is not None
                    and bounds[1].astimezone(NEW_YORK).strftime("%Y-%m") == scope.reference_month
                ):
                    minute = moment.replace(second=0, microsecond=0)
                    causal = tuple(cast(list[str], row["quality_flags"]))
                    retrospective = tuple(cast(list[str], row["retrospective_flags"]))
                    if len(set(causal)) != len(causal) or len(set(retrospective)) != len(
                        retrospective
                    ):
                        raise ReferenceAcceptanceError("clean-tick flag lists contain duplicates")
                    for flag in causal:
                        _nonempty(flag, "causal tick flag")
                    for flag in retrospective:
                        _nonempty(flag, "retrospective tick flag")
                    covered.add(minute)
                    causal_flags[minute].update(causal)
                    retrospective_flags[minute].update(retrospective)
                rows += 1
        after = sha256_path(path)
        if before != after:
            raise ReferenceAcceptanceError(f"clean-tick evidence changed while reading: {path}")
        evidence.append(FileEvidence(path=path.as_posix(), sha256=before, rows=rows))
        corpus_ids.add(metadata["tradebot.corpus_id"])
    return LoadedRetrospectiveFlags(
        causal_flags_by_minute={
            minute: tuple(sorted(values)) for minute, values in causal_flags.items()
        },
        flags_by_minute={
            minute: tuple(sorted(values)) for minute, values in retrospective_flags.items()
        },
        covered_minutes=frozenset(covered),
        files=tuple(evidence),
        corpus_ids=tuple(sorted(corpus_ids)),
    )


def _clean_relative_path(path: str) -> str:
    parts = Path(path).parts
    try:
        index = parts.index("clean")
    except ValueError as exc:
        raise ReferenceAcceptanceError("loaded evidence path is not inside a clean/ tree") from exc
    return Path(*parts[index:]).as_posix()


def verify_producer_inventory(
    *,
    report_path: Path,
    sidecar_path: Path,
    expected_report_sha256: str,
    scope: ReferenceScope,
    bars: LoadedBars,
    ticks: LoadedRetrospectiveFlags,
) -> ProducerInventoryEvidence:
    """Verify full exact-scope clean inputs against the sidecar-pinned producer report."""

    report_bytes = report_path.read_bytes()
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    if report_sha256 != _digest(expected_report_sha256, "expected producer report sha256"):
        raise ReferenceAcceptanceError(
            "producer report does not match the independent expected hash"
        )
    sidecar_bytes = sidecar_path.read_bytes()
    sidecar = _json(sidecar_bytes, label="producer sidecar")
    report = _json(report_bytes, label="producer report")
    if sidecar != {report_path.name: report_sha256}:
        raise ReferenceAcceptanceError("producer report sidecar does not pin the report bytes")
    if (
        not isinstance(report, dict)
        or type(report.get("schema_version")) is not int
        or report.get("schema_version") != 1
    ):
        raise ReferenceAcceptanceError("unsupported producer report schema")
    corpus_id = _digest(report.get("corpus_id"), "producer corpus_id")
    if (
        report.get("reproducibility_status") != "PASSED"
        or report.get("independent_rebuilds_byte_identical") is not True
        or report.get("raw_files_unchanged") is not True
        or report.get("implementation_unchanged") is not True
    ):
        raise ReferenceAcceptanceError("producer report does not certify an immutable rebuild")
    if bars.corpus_ids != (corpus_id,) or ticks.corpus_ids != (corpus_id,):
        raise ReferenceAcceptanceError("loaded clean inputs do not match the producer corpus")

    raw_manifest = report.get("clean_manifest")
    if not isinstance(raw_manifest, list):
        raise ReferenceAcceptanceError("producer report has no clean_manifest")
    manifest: dict[str, str] = {}
    for value in raw_manifest:
        row = _strict_object(value, {"path", "sha256"}, "producer clean_manifest row")
        path = _nonempty(row["path"], "producer clean path")
        digest = _digest(row["sha256"], "producer clean sha256")
        if path in manifest:
            raise ReferenceAcceptanceError("producer clean_manifest contains duplicate paths")
        manifest[path] = digest

    bar_prefix = f"clean/bars/{scope.venue}/1m/{scope.instrument}/"
    tick_prefix = f"clean/ticks/{scope.venue}/{scope.instrument}/"
    selected_manifest = {
        path: digest
        for path, digest in manifest.items()
        if path.startswith(bar_prefix) or path.startswith(tick_prefix)
    }
    loaded_rows = (*bars.files, *ticks.files)
    loaded_manifest = {_clean_relative_path(row.path): row.sha256 for row in loaded_rows}
    if len(loaded_manifest) != len(loaded_rows):
        raise ReferenceAcceptanceError("loaded clean inputs contain duplicate inventory paths")
    if not selected_manifest or loaded_manifest != selected_manifest:
        raise ReferenceAcceptanceError(
            "loaded clean inputs are not the producer report's complete exact-scope inventory"
        )
    return ProducerInventoryEvidence(
        report_sha256=report_sha256,
        sidecar_sha256=hashlib.sha256(sidecar_bytes).hexdigest(),
        corpus_id=corpus_id,
    )
