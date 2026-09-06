"""Explicit experimental source-label interpretation, never a default clock fix.

Carrier timestamps encode source labels in UTC, not an assertion of absolute UTC.
Rules and exclusions use half-open label intervals. Unknown or ambiguous periods
must remain uncovered; this module never infers a timezone, transition or fold.
"""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Self, cast

_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty, unpadded string")
    return value


def _carrier(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if not isinstance(value.tzinfo, timezone) or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must use a fixed UTC carrier timezone")
    if value.fold:
        raise ValueError(f"{name} must not select a fold; ambiguous labels must be excluded")
    return value.astimezone(UTC)


def _stamp(value: object, name: str) -> datetime:
    text = _text(value, name)
    if not text.endswith(("Z", "+00:00")):
        raise ValueError(f"{name} must declare UTC with Z or +00:00")
    try:
        result = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO datetime with an explicit UTC offset") from error
    return _carrier(result, name)


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object with string keys")
    return cast(Mapping[str, object], value)


def _keys(value: Mapping[str, object], required: set[str], optional: set[str]) -> None:
    if required - value.keys() or value.keys() - required - optional:
        raise ValueError("Policy object has missing or unknown fields")


def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return cast(list[object], value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceClockInterval:
    """One explicit [label_start, label_end) rule with a whole-second offset."""

    label_start: datetime
    label_end: datetime
    offset_seconds_to_utc: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "label_start", _carrier(self.label_start, "label_start"))
        object.__setattr__(self, "label_end", _carrier(self.label_end, "label_end"))
        if self.label_start >= self.label_end:
            raise ValueError("Clock intervals must be non-empty and increasing")
        if type(self.offset_seconds_to_utc) is not int or not (
            -86400 < self.offset_seconds_to_utc < 86400
        ):
            raise ValueError("offset_seconds_to_utc must be an integer strictly within 24 hours")


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceClockExclusion:
    """An explicitly unexplained/ambiguous label interval; never a conversion rule."""

    label_start: datetime
    label_end: datetime
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "label_start", _carrier(self.label_start, "label_start"))
        object.__setattr__(self, "label_end", _carrier(self.label_end, "label_end"))
        if self.label_start >= self.label_end:
            raise ValueError("Excluded intervals must be non-empty and increasing")
        _text(self.reason, "reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceClockPolicy:
    """Immutable, evidence-bound interpretation for one exact source/instrument.

    Schema version 1 has no installed mappings. Exclusions cannot overlap rules;
    implicit holes also fail closed. Fixed UTC ``datetime.timezone`` carriers
    are required even when a geographic timezone happens to have zero offset.
    A policy's status is analytical provenance, never approval for production.
    """

    schema_version: int
    policy_id: str
    source: str
    instrument: str
    status: str
    label_start: datetime
    label_end: datetime
    intervals: tuple[SourceClockInterval, ...]
    evidence_sha256: tuple[tuple[str, str], ...]
    excluded_intervals: tuple[SourceClockExclusion, ...] = ()
    _starts: tuple[datetime, ...] = field(init=False, repr=False, compare=False)
    _identity: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Only source-clock schema_version 1 is supported")
        for name in ("policy_id", "source", "instrument"):
            _text(getattr(self, name), name)
        if self.status != "EXPERIMENTAL_ONLY":
            raise ValueError("Source clock policies must remain EXPERIMENTAL_ONLY")
        object.__setattr__(self, "label_start", _carrier(self.label_start, "label_start"))
        object.__setattr__(self, "label_end", _carrier(self.label_end, "label_end"))
        if self.label_start >= self.label_end:
            raise ValueError("Policy label scope must be non-empty and increasing")
        if not isinstance(self.intervals, tuple) or not self.intervals:
            raise ValueError("intervals must be a non-empty immutable tuple")
        if not isinstance(self.excluded_intervals, tuple):
            raise ValueError("excluded_intervals must be an immutable tuple")
        previous_end = self.label_start
        previous_rule: SourceClockInterval | None = None
        for rule in self.intervals:
            if not isinstance(rule, SourceClockInterval):
                raise ValueError("intervals must contain SourceClockInterval values")
            if rule.label_start < previous_end or rule.label_end > self.label_end:
                raise ValueError("Clock rules must be ordered, nonoverlapping and inside scope")
            if previous_rule is not None:
                # Compare relative durations so validation cannot overflow at datetime limits.
                mapped_gap = (
                    rule.label_start
                    - previous_rule.label_end
                    + timedelta(
                        seconds=(rule.offset_seconds_to_utc - previous_rule.offset_seconds_to_utc)
                    )
                )
                if mapped_gap < timedelta(0):
                    raise ValueError("Converted UTC intervals must not overlap or regress")
            previous_end = rule.label_end
            previous_rule = rule
        previous_end = self.label_start
        for exclusion in self.excluded_intervals:
            if not isinstance(exclusion, SourceClockExclusion):
                raise ValueError("excluded_intervals must contain SourceClockExclusion values")
            if exclusion.label_start < previous_end or exclusion.label_end > self.label_end:
                raise ValueError("Exclusions must be ordered, nonoverlapping and inside scope")
            if any(
                rule.label_start < exclusion.label_end and exclusion.label_start < rule.label_end
                for rule in self.intervals
            ):
                raise ValueError("Excluded intervals must be holes, not overlap clock rules")
            previous_end = exclusion.label_end
        if not isinstance(self.evidence_sha256, tuple) or not self.evidence_sha256:
            raise ValueError("evidence_sha256 must contain immutable evidence bindings")
        evidence: dict[str, str] = {}
        for pair in self.evidence_sha256:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("Each evidence binding must be an immutable name/hash pair")
            name, digest = pair
            _text(name, "evidence binding")
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise ValueError("Evidence hashes must contain exactly 64 hexadecimal digits")
            if name in evidence:
                raise ValueError("Evidence bindings must have unique names")
            evidence[name] = digest.lower()
        object.__setattr__(self, "evidence_sha256", tuple(sorted(evidence.items())))
        object.__setattr__(self, "_starts", tuple(rule.label_start for rule in self.intervals))
        object.__setattr__(
            self, "_identity", hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> Self:
        """Parse strict fields; timestamps must explicitly end in Z or +00:00."""
        value: object = json.loads(payload, object_pairs_hook=_unique_object)
        return cls.from_dict(_object(value, "policy"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        """Copy and validate a schema-v1 policy; retain no mutable input references."""
        _keys(
            payload,
            {
                "schema_version",
                "policy_id",
                "source",
                "instrument",
                "status",
                "label_start",
                "label_end",
                "intervals",
                "evidence_sha256",
            },
            {"excluded_intervals"},
        )
        version = payload["schema_version"]
        if type(version) is not int:
            raise ValueError("schema_version must be an integer")
        intervals: list[SourceClockInterval] = []
        for item in _array(payload["intervals"], "intervals"):
            rule = _object(item, "interval")
            _keys(rule, {"label_start", "label_end", "offset_seconds_to_utc"}, set())
            offset = rule["offset_seconds_to_utc"]
            if type(offset) is not int:
                raise ValueError("offset_seconds_to_utc must be an integer")
            intervals.append(
                SourceClockInterval(
                    label_start=_stamp(rule["label_start"], "label_start"),
                    label_end=_stamp(rule["label_end"], "label_end"),
                    offset_seconds_to_utc=offset,
                )
            )
        exclusions: list[SourceClockExclusion] = []
        for item in _array(payload.get("excluded_intervals", []), "excluded_intervals"):
            exclusion = _object(item, "exclusion")
            _keys(exclusion, {"label_start", "label_end", "reason"}, set())
            exclusions.append(
                SourceClockExclusion(
                    label_start=_stamp(exclusion["label_start"], "label_start"),
                    label_end=_stamp(exclusion["label_end"], "label_end"),
                    reason=_text(exclusion["reason"], "reason"),
                )
            )
        evidence = _object(payload["evidence_sha256"], "evidence_sha256")
        return cls(
            schema_version=version,
            policy_id=_text(payload["policy_id"], "policy_id"),
            source=_text(payload["source"], "source"),
            instrument=_text(payload["instrument"], "instrument"),
            status=_text(payload["status"], "status"),
            label_start=_stamp(payload["label_start"], "label_start"),
            label_end=_stamp(payload["label_end"], "label_end"),
            intervals=tuple(intervals),
            evidence_sha256=tuple(
                (name, _text(digest, "evidence hash")) for name, digest in evidence.items()
            ),
            excluded_intervals=tuple(exclusions),
        )

    @property
    def identity(self) -> str:
        """SHA-256 of normalized full policy JSON, including every evidence binding."""
        return self._identity

    def to_dict(self) -> dict[str, object]:
        """Return a fresh JSON-compatible representation, never internal references."""
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "source": self.source,
            "instrument": self.instrument,
            "status": self.status,
            "label_start": self.label_start.isoformat(timespec="microseconds"),
            "label_end": self.label_end.isoformat(timespec="microseconds"),
            "intervals": [
                {
                    "label_start": rule.label_start.isoformat(timespec="microseconds"),
                    "label_end": rule.label_end.isoformat(timespec="microseconds"),
                    "offset_seconds_to_utc": rule.offset_seconds_to_utc,
                }
                for rule in self.intervals
            ],
            "evidence_sha256": dict(self.evidence_sha256),
            "excluded_intervals": [
                {
                    "label_start": item.label_start.isoformat(timespec="microseconds"),
                    "label_end": item.label_end.isoformat(timespec="microseconds"),
                    "reason": item.reason,
                }
                for item in self.excluded_intervals
            ],
        }

    def to_json(self) -> str:
        """Canonical UTF-8-ready JSON with stable key ordering and timestamp precision."""
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def apply(self, label: datetime, *, source: str, instrument: str) -> datetime:
        """Interpret one label, failing closed outside a unique explicitly allowed rule."""
        if source != self.source or instrument != self.instrument:
            raise ValueError("Source clock policy scope does not match source/instrument")
        carrier = _carrier(label, "label")
        if not self.label_start <= carrier < self.label_end:
            raise ValueError("Source label is outside the policy scope")
        index = bisect_right(self._starts, carrier) - 1
        if index < 0 or carrier >= self.intervals[index].label_end:
            raise ValueError("Source label has no unique clock rule (uncovered or excluded)")
        try:
            return carrier + timedelta(seconds=self.intervals[index].offset_seconds_to_utc)
        except OverflowError as error:
            raise ValueError("Interpreted timestamp is outside the datetime range") from error
