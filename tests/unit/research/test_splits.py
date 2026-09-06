"""ENGINEERING_ONLY chronology contracts; no market labels or performance metrics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from tradebot.research.demo import synthetic_records, synthetic_setup
from tradebot.research.engine import iter_decisions
from tradebot.research.feed import ReplayBar
from tradebot.research.splits import ChronologicalSplit, TimeWindow, select_partition

START = datetime(2024, 1, 8, 10, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def _at(minutes: int) -> datetime:
    return START + minutes * MINUTE


def _split() -> ChronologicalSplit:
    return ChronologicalSplit(
        TimeWindow(_at(0), _at(70)),
        TimeWindow(_at(72), _at(142)),
        TimeWindow(_at(144), _at(160)),
        embargo=2 * MINUTE,
        label_horizon=MINUTE,
    )


@pytest.mark.parametrize("invalid", [None, 1, "2024-01-08T10:00:00Z", True])
@pytest.mark.parametrize("field", ["start", "end"])
def test_window_requires_datetime_fields(invalid: object, field: str) -> None:
    values = {"start": START, "end": _at(70)}
    values[field] = cast(datetime, invalid)
    with pytest.raises(TypeError, match="datetime"):
        TimeWindow(**values)


@pytest.mark.parametrize(
    "invalid",
    [datetime(2024, 1, 8, 10), START.astimezone(timezone(timedelta(hours=2)))],
)
@pytest.mark.parametrize("field", ["start", "end"])
def test_window_rejects_naive_and_non_utc(invalid: datetime, field: str) -> None:
    values = {"start": START, "end": _at(70)}
    values[field] = invalid
    with pytest.raises(ValueError, match="UTC"):
        TimeWindow(**values)


@pytest.mark.parametrize("end", [START, START - MINUTE])
def test_window_must_be_nonempty_and_forward(end: datetime) -> None:
    with pytest.raises(ValueError, match="earlier"):
        TimeWindow(START, end)


def test_window_is_immutable_and_normalizes_zero_offset_to_utc() -> None:
    zero_offset = timezone(timedelta(0), name="Explicit zero offset")
    window = TimeWindow(START.astimezone(zero_offset), _at(70).astimezone(zero_offset))
    assert window.start.tzinfo is UTC
    assert window.end.tzinfo is UTC
    with pytest.raises(FrozenInstanceError):
        window.start = _at(1)  # type: ignore[misc]
    split = _split()
    with pytest.raises(FrozenInstanceError):
        split.embargo = MINUTE  # type: ignore[misc]


@pytest.mark.parametrize("field", ["training", "validation", "lockbox"])
def test_split_requires_typed_windows(field: str) -> None:
    with pytest.raises(TypeError, match="TimeWindow"):
        replace(_split(), **{field: object()})  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["embargo", "label_horizon"])
@pytest.mark.parametrize("invalid", [None, True, 60, 60.0, "60"])
def test_split_requires_timedeltas(field: str, invalid: object) -> None:
    with pytest.raises(TypeError, match="timedelta"):
        replace(_split(), **{field: invalid})  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["embargo", "label_horizon"])
def test_split_rejects_negative_durations(field: str) -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        replace(_split(), **{field: -MINUTE})  # type: ignore[arg-type]


def test_embargo_cannot_be_shorter_than_label_horizon() -> None:
    with pytest.raises(ValueError, match="label_horizon"):
        replace(_split(), label_horizon=3 * MINUTE)


@pytest.mark.parametrize(
    ("field", "window"),
    [
        ("validation", TimeWindow(_at(69), _at(142))),
        ("validation", TimeWindow(_at(70), _at(142))),
        ("validation", TimeWindow(_at(71), _at(142))),
        ("lockbox", TimeWindow(_at(141), _at(160))),
        ("lockbox", TimeWindow(_at(142), _at(160))),
        ("lockbox", TimeWindow(_at(143), _at(160))),
        ("training", TimeWindow(_at(150), _at(155))),
    ],
)
def test_split_requires_chronology_and_full_embargo(field: str, window: TimeWindow) -> None:
    with pytest.raises(ValueError, match="embargo"):
        replace(_split(), **{field: window})  # type: ignore[arg-type]


def test_zero_horizon_and_embargo_allow_contiguous_windows() -> None:
    split = ChronologicalSplit(
        TimeWindow(_at(0), _at(1)),
        TimeWindow(_at(1), _at(2)),
        TimeWindow(_at(2), _at(3)),
        embargo=timedelta(0),
        label_horizon=timedelta(0),
    )
    assert split.classify(_at(1), _at(1), _at(1)) == "validation"


@pytest.mark.parametrize(
    ("observation", "decision", "label", "expected"),
    [
        (0, 0, 0, "training"),
        (0, 1, 2, "training"),
        (68, 69, 70, "training"),
        (69, 70, 70, "purged"),
        (69, 69, 71, "purged"),
        (0, 72, 73, "purged"),
        (70, 70, 71, "outside"),
        (71, 72, 73, "outside"),
        (72, 73, 74, "validation"),
        (140, 141, 142, "validation"),
        (141, 142, 142, "purged"),
        (142, 142, 143, "outside"),
        (144, 145, 146, "lockbox"),
        (158, 159, 160, "lockbox"),
        (159, 160, 160, "purged"),
        (160, 160, 161, "outside"),
        (-1, 0, 1, "outside"),
    ],
)
def test_classification_half_open_windows_and_purge_boundaries(
    observation: int, decision: int, label: int, expected: str
) -> None:
    assert _split().classify(_at(observation), _at(decision), _at(label)) == expected


@pytest.mark.parametrize("argument", [0, 1, 2])
@pytest.mark.parametrize(
    ("invalid", "error"),
    [
        (None, TypeError),
        ("2024-01-08T10:00:00Z", TypeError),
        (datetime(2024, 1, 8, 10), ValueError),
        (START.astimezone(timezone(timedelta(hours=2))), ValueError),
    ],
)
def test_classification_validates_all_timestamp_arguments(
    argument: int, invalid: object, error: type[Exception]
) -> None:
    values = [START, _at(1), _at(2)]
    values[argument] = cast(datetime, invalid)
    with pytest.raises(error):
        _split().classify(*values)


@pytest.mark.parametrize(("decision", "label"), [(-1, 1), (1, 0)])
def test_classification_rejects_impossible_time_order(decision: int, label: int) -> None:
    with pytest.raises(ValueError, match="precede"):
        _split().classify(START, _at(decision), _at(label))


@pytest.mark.parametrize("partition", ["lockbox", "outside", "purged", "", "TRAINING"])
def test_selection_denies_lockbox_or_unknown_partition_without_touching_input(
    partition: str,
) -> None:
    class UnreadableInput:
        def __iter__(self) -> Iterator[ReplayBar]:
            pytest.fail("denied partition must not obtain an input iterator")

    with pytest.raises(ValueError, match=r"training.*validation"):
        select_partition(UnreadableInput(), _split(), partition)


def test_selection_validates_split_and_partition_types_before_touching_input() -> None:
    with pytest.raises(TypeError, match="ChronologicalSplit"):
        select_partition((), cast(ChronologicalSplit, None), "training")
    with pytest.raises(TypeError, match="partition"):
        select_partition((), _split(), cast(str, []))


def test_selection_validates_record_type_including_nonselected_input() -> None:
    with pytest.raises(TypeError, match="ReplayBar"):
        tuple(select_partition(cast(list[ReplayBar], [object()]), _split(), "training"))


def test_selection_uses_receipt_availability_not_historical_event_time() -> None:
    row = synthetic_records()[0]
    late = replace(row, bar=replace(row.bar, ts_recv=_at(72)))
    assert tuple(select_partition((late,), _split(), "training")) == ()
    assert tuple(select_partition((late,), _split(), "validation")) == ()


def test_selection_purges_label_crossing_boundary_by_one_microsecond() -> None:
    row = synthetic_records()[136]
    exact = replace(row, bar=replace(row.bar, ts_recv=_at(69)))
    late = replace(row, bar=replace(row.bar, ts_recv=_at(69) + timedelta(microseconds=1)))
    assert tuple(select_partition((exact,), _split(), "training")) == (exact,)
    assert tuple(select_partition((late,), _split(), "training")) == ()


def test_selection_consumes_unselected_tail_and_propagates_eof_validation_failure() -> None:
    row = synthetic_records()[0]

    def feed() -> Iterator[ReplayBar]:
        yield row
        yield synthetic_records()[-1]
        raise RuntimeError("snapshot failed EOF verification")

    selected = select_partition(feed(), _split(), "training")
    assert next(selected) == row
    with pytest.raises(RuntimeError, match="EOF"):
        next(selected)


def test_selection_is_lazy_and_preserves_source_order_without_sorting() -> None:
    read = 0
    rows = synthetic_records()[:2]

    def feed() -> Iterator[ReplayBar]:
        nonlocal read
        for row in reversed(rows):
            read += 1
            yield row

    selected = select_partition(feed(), _split(), "training")
    assert read == 0
    assert next(selected) == rows[1]
    assert read == 1
    assert tuple(selected) == (rows[0],)
    assert read == 2


def test_label_horizon_datetime_overflow_fails_closed() -> None:
    row = synthetic_records()[0]
    end = datetime.max.replace(tzinfo=UTC)
    late = replace(row, bar=replace(row.bar, ts_recv=end))
    with pytest.raises(ValueError, match="overflows"):
        tuple(select_partition((late,), _split(), "training"))


def test_fixed_synthetic_partition_counts_are_repeatable_and_disjoint() -> None:
    rows = synthetic_records()
    split = _split()
    partitions = {
        name: tuple(select_partition(rows, split, name)) for name in ("training", "validation")
    }
    assert {name: len(selected) for name, selected in partitions.items()} == {
        "training": 136,
        "validation": 136,
    }
    assert {row.key for row in partitions["training"]}.isdisjoint(
        row.key for row in partitions["validation"]
    )
    assert partitions["training"] == tuple(select_partition(rows, split, "training"))
    counts = Counter(
        split.classify(
            row.bar.ts_open,
            max(row.bar.ts_event, row.bar.ts_recv, row.bar.available_at),
            max(row.bar.ts_event, row.bar.ts_recv, row.bar.available_at) + split.label_horizon,
        )
        for row in rows
    )
    assert counts == {"training": 136, "validation": 136, "lockbox": 28, "purged": 12, "outside": 8}


def test_partition_replays_start_fresh_and_never_receive_lockbox_rows() -> None:
    rows, config, _ = synthetic_setup("UNCOMMITTED")
    split = _split()
    training = tuple(iter_decisions(select_partition(rows, split, "training"), config))
    validation = tuple(iter_decisions(select_partition(rows, split, "validation"), config))
    assert Counter(row.status for row in training) == {"warmup": 128, "forecast": 8}
    assert Counter(row.status for row in validation) == {"warmup": 134, "suppressed": 2}
    assert validation[0].reason == "insufficient_history"
    assert all(row.bar_open < split.lockbox.start for row in (*training, *validation))
