"""Predeclared chronological routing for ENGINEERING_ONLY research preparation.

This module validates boundaries, not research readiness, economic labels or a
complete purged-CV procedure. The selector never supplies lockbox observations
to a strategy; it is not a filesystem access-control or gate-approval mechanism.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from tradebot.core.timestamps import require_utc
from tradebot.research.feed import ReplayBar

type Classification = Literal["training", "validation", "lockbox", "purged", "outside"]


def _timestamp(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be datetime")
    return require_utc(value, field=field)


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """Nonempty half-open UTC interval: start is included and end is excluded."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", _timestamp(self.start, "start"))
        object.__setattr__(self, "end", _timestamp(self.end, "end"))
        if self.start >= self.end:
            raise ValueError("window start must be earlier than end")


@dataclass(frozen=True, slots=True)
class ChronologicalSplit:
    """One fixed train/validation/lockbox split, not walk-forward or CPCV.

    The embargo is a minimum unused gap after training and validation; the
    prospective label horizon cannot be longer than that gap. No label value is
    calculated here. Each partition must be replayed with fresh strategy state.
    """

    training: TimeWindow
    validation: TimeWindow
    lockbox: TimeWindow
    embargo: timedelta
    label_horizon: timedelta

    def __post_init__(self) -> None:
        for name in ("training", "validation", "lockbox"):
            if not isinstance(getattr(self, name), TimeWindow):
                raise TypeError(f"{name} must be TimeWindow")
        for name in ("embargo", "label_horizon"):
            duration = getattr(self, name)
            if not isinstance(duration, timedelta):
                raise TypeError(f"{name} must be timedelta")
            if duration < timedelta(0):
                raise ValueError(f"{name} must be nonnegative")
        if self.embargo < self.label_horizon:
            raise ValueError("embargo must be at least label_horizon")
        if self.validation.start - self.training.end < self.embargo:
            raise ValueError("training to validation must preserve the full embargo")
        if self.lockbox.start - self.validation.end < self.embargo:
            raise ValueError("validation to lockbox must preserve the full embargo")

    def classify(
        self, observation_start: datetime, decision_at: datetime, label_end: datetime
    ) -> Classification:
        """Route by observation start, then purge unavailable/cross-boundary data.

        The decision must lie inside the same half-open window as the observation
        start. A label may end exactly at the window end, but not after it. Late
        historical observations are purged, never reassigned to a later window.
        Gap/embargo observations are outside. Impossible timestamp ordering raises.
        The caller supplies the actual label endpoint; applying the declared
        fixed horizon is the selector's responsibility, not this classifier's.
        """
        observed = _timestamp(observation_start, "observation_start")
        decision = _timestamp(decision_at, "decision_at")
        labelled = _timestamp(label_end, "label_end")
        if decision < observed:
            raise ValueError("decision_at cannot precede observation_start")
        if labelled < decision:
            raise ValueError("label_end cannot precede decision_at")
        windows: tuple[tuple[Classification, TimeWindow], ...] = (
            ("training", self.training),
            ("validation", self.validation),
            ("lockbox", self.lockbox),
        )
        for name, window in windows:
            if window.start <= observed < window.end:
                if decision >= window.end or labelled > window.end:
                    return "purged"
                return name
        return "outside"


def select_partition(
    records: Iterable[ReplayBar], split: ChronologicalSplit, partition: str
) -> Iterator[ReplayBar]:
    """Select training or validation observations without evaluating label values.

    Reject lockbox/unknown partitions before obtaining an input iterator. The
    returned iterator is lazy, retains source order, and must be fully consumed:
    it scans even the unselected tail so immutable feeds can verify hashes at EOF.
    This necessarily inspects routing timestamps of unselected input; it does not
    establish that an underlying source has never been read. Existing replay
    remains responsible for source sequencing, duration and strategy validation.
    """
    if not isinstance(split, ChronologicalSplit):
        raise TypeError("split must be ChronologicalSplit")
    if not isinstance(partition, str):
        raise TypeError("partition must be str")
    if partition not in {"training", "validation"}:
        raise ValueError("only training and validation partitions may be replayed")

    def selected() -> Iterator[ReplayBar]:
        for record in records:
            if not isinstance(record, ReplayBar):
                raise TypeError("records must contain ReplayBar values")
            bar = record.bar
            decision = max(bar.ts_event, bar.ts_recv, bar.available_at)
            try:
                label_end = decision + split.label_horizon
            except OverflowError as exc:
                raise ValueError("label_horizon overflows the datetime range") from exc
            if split.classify(bar.ts_open, decision, label_end) == partition:
                yield record

    return selected()
