from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradebot.core.clock import SimClock
from tradebot.core.errors import IngestAlignmentError
from tradebot.core.types import QualityFlag
from tradebot.data.ingest import RawTick, TickIngester, overlap_length
from tradebot.data.normalize import TickObservation, normalize_tick

START = datetime(2025, 3, 17, 12, tzinfo=UTC)


def _observation(
    second: int,
    *,
    bid: str = "1.29000",
    recv_second: int | None = None,
) -> TickObservation:
    ts_event = START + timedelta(seconds=second)
    ts_recv = START + timedelta(seconds=second if recv_second is None else recv_second)
    return TickObservation(
        instrument="GBP_USD",
        ts_event=ts_event,
        ts_recv=ts_recv,
        bid=Decimal(bid),
        ask=Decimal(bid) + Decimal("0.00010"),
    )


def _ingester(**changes: object) -> TickIngester:
    fields: dict[str, object] = {
        "source": "dukascopy",
        "instrument": "GBP_USD",
        "clock": SimClock(START),
    }
    return TickIngester(**(fields | changes))  # type: ignore[arg-type]


def _seqs(rows: tuple[RawTick, ...]) -> list[int]:
    return [row.seq for row in rows]


def _events(rows: tuple[RawTick, ...]) -> list[datetime]:
    return [row.tick.ts_event for row in rows]


def test_a_first_fetch_is_sequenced_in_source_order_from_zero() -> None:
    ingester = _ingester()

    result = ingester.ingest([_observation(0), _observation(1), _observation(2)], run_id="r1")

    assert _seqs(result.appended) == [0, 1, 2]
    assert _events(result.appended) == [
        START,
        START + timedelta(seconds=1),
        START + timedelta(seconds=2),
    ]
    assert result.overlap == 0
    assert ingester.next_seq == 3


def test_re_ingesting_an_identical_range_appends_nothing() -> None:
    """Idempotence is what makes seq stable across re-ingests (NN-10)."""
    ingester = _ingester()
    batch = [_observation(0), _observation(1), _observation(2)]
    ingester.ingest(batch, run_id="r1")

    result = ingester.ingest(batch, run_id="r2")

    assert result.appended == ()
    assert result.overlap == 3
    assert ingester.next_seq == 3, "no sequence numbers were consumed or reassigned"


def test_an_overlapping_re_fetch_appends_only_the_true_suffix() -> None:
    ingester = _ingester()
    ingester.ingest([_observation(0), _observation(1), _observation(2)], run_id="r1")

    result = ingester.ingest(
        [_observation(1), _observation(2), _observation(3), _observation(4)], run_id="r2"
    )

    assert result.overlap == 2
    assert _events(result.appended) == [
        START + timedelta(seconds=3),
        START + timedelta(seconds=4),
    ]
    assert _seqs(result.appended) == [3, 4]


def test_repeated_identical_quotes_are_never_collapsed() -> None:
    """The anti-value-hashing case: a feed repeating a quote is completely normal.

    A set-of-hashes filter would keep one of these and drop the rest, corrupting
    tick-count volume and the completeness report.
    """
    ingester = _ingester()
    identical = [_observation(0), _observation(0), _observation(0)]

    result = ingester.ingest(identical, run_id="r1")

    assert len(result.appended) == 3
    assert _seqs(result.appended) == [0, 1, 2]


def test_a_fourth_identical_quote_after_three_stored_is_appended_once() -> None:
    """Maximal overlap keeps the re-fetch idempotent without swallowing new data."""
    ingester = _ingester()
    ingester.ingest([_observation(0), _observation(0), _observation(0)], run_id="r1")

    result = ingester.ingest(
        [_observation(0), _observation(0), _observation(0), _observation(0)], run_id="r2"
    )

    assert result.overlap == 3
    assert len(result.appended) == 1, "exactly the one genuinely new quote"
    assert _seqs(result.appended) == [3]


def test_a_disjoint_fetch_after_a_gap_appends_everything() -> None:
    ingester = _ingester()
    ingester.ingest([_observation(0), _observation(1)], run_id="r1")

    result = ingester.ingest([_observation(90), _observation(91)], run_id="r2")

    assert result.overlap == 0
    assert _seqs(result.appended) == [2, 3]


def test_alignment_survives_a_new_local_receipt_stamp() -> None:
    """A live gap-recovery re-fetch stamps a NEW ts_recv for the very same tick.

    Comparing on ts_recv would make an event fail to match itself, and the splice
    would duplicate every recovered tick.
    """
    ingester = _ingester()
    ingester.ingest([_observation(0), _observation(1)], run_id="r1")

    refetched = [
        _observation(0, recv_second=600),
        _observation(1, recv_second=600),
        _observation(2, recv_second=600),
    ]
    result = ingester.ingest(refetched, run_id="r2")

    assert result.overlap == 2
    assert len(result.appended) == 1
    assert result.appended[0].tick.ts_event == START + timedelta(seconds=2)


def test_a_changed_price_at_the_same_timestamp_does_not_align() -> None:
    """A revision is different content, so it appends rather than silently matching."""
    ingester = _ingester()
    ingester.ingest([_observation(0, bid="1.29000")], run_id="r1")

    result = ingester.ingest([_observation(0, bid="1.29500")], run_id="r2")

    assert result.overlap == 0
    assert len(result.appended) == 1
    assert result.appended[0].run_id == "r2", "the ingest run distinguishes the two rows"


def test_sequencing_is_identical_whether_ingested_in_one_fetch_or_two() -> None:
    """Stability across re-ingests: the split must not change the numbering."""
    whole = _ingester()
    whole.ingest([_observation(index) for index in range(6)], run_id="r1")

    split = _ingester()
    split.ingest([_observation(index) for index in range(4)], run_id="r1")
    split.ingest([_observation(index) for index in range(2, 6)], run_id="r2")

    assert whole.next_seq == split.next_seq == 6


def test_provenance_is_recorded_and_shared_across_one_fetch() -> None:
    """A fetch is one audit event, so its rows share a single ingested_at."""
    clock = SimClock(START)
    ingester = _ingester(clock=clock)

    result = ingester.ingest([_observation(0), _observation(1)], run_id="run-42")

    stamps = {row.ingested_at for row in result.appended}
    assert stamps == {START}
    assert {row.run_id for row in result.appended} == {"run-42"}
    assert {row.source for row in result.appended} == {"dukascopy"}
    assert {row.instrument for row in result.appended} == {"GBP_USD"}


def test_ingested_at_is_audit_only_and_never_on_the_published_event() -> None:
    """SPEC 4.2: the ingest wall clock must not reach the bus."""
    ingester = _ingester()
    row = ingester.ingest([_observation(0)], run_id="r1").appended[0]

    assert not hasattr(row.tick, "ingested_at")
    assert row.tick.available_at == row.tick.ts_event


def test_an_empty_fetch_is_a_no_op() -> None:
    ingester = _ingester()

    result = ingester.ingest([], run_id="r1")

    assert result.appended == ()
    assert result.overlap == 0
    assert ingester.next_seq == 0


def test_an_observation_for_another_instrument_is_refused() -> None:
    ingester = _ingester()
    other = TickObservation(
        instrument="EUR_USD",
        ts_event=START,
        ts_recv=START,
        bid=Decimal("1.08000"),
        ask=Decimal("1.08010"),
    )

    with pytest.raises(ValueError, match="EUR_USD"):
        ingester.ingest([other], run_id="r1")

    assert ingester.next_seq == 0


def test_an_overlap_filling_the_window_refuses_rather_than_risk_duplicates() -> None:
    """Fail closed: a longer overlap cannot be ruled out beyond the retained tail."""
    ingester = _ingester(tail_window=2)
    ingester.ingest([_observation(0), _observation(1), _observation(2)], run_id="r1")

    with pytest.raises(IngestAlignmentError, match="alignment window"):
        ingester.ingest([_observation(1), _observation(2), _observation(3)], run_id="r2")


def test_a_full_window_overlap_with_nothing_to_append_is_allowed() -> None:
    """No remainder means no duplication risk, so the guard must not fire."""
    ingester = _ingester(tail_window=2)
    ingester.ingest([_observation(0), _observation(1)], run_id="r1")

    result = ingester.ingest([_observation(0), _observation(1)], run_id="r2")

    assert result.appended == ()
    assert result.overlap == 2


def test_imputed_receipt_stamps_flow_through_to_the_raw_row() -> None:
    ingester = _ingester()
    observation = TickObservation(
        instrument="GBP_USD",
        ts_event=START,
        ts_recv=None,
        bid=Decimal("1.29000"),
        ask=Decimal("1.29010"),
    )

    row = ingester.ingest([observation], run_id="r1").appended[0]

    assert QualityFlag.TS_RECV_IMPUTED in row.tick.quality_flags
    assert row.tick.ts_recv == START


@pytest.mark.parametrize(("field", "value"), [("tail_window", 0), ("start_seq", -1)])
def test_construction_rejects_nonsense_bounds(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        _ingester(**{field: value})


def test_overlap_length_handles_the_empty_cases() -> None:
    tick = normalize_tick(_observation(0))

    assert overlap_length([], [tick]) == 0
    assert overlap_length([tick], []) == 0
    assert overlap_length([], []) == 0


def test_overlap_length_ignores_a_match_that_is_not_at_the_boundary() -> None:
    """Only a suffix-to-prefix alignment counts; a match in the middle is not one."""
    stored = [normalize_tick(_observation(index)) for index in (0, 1, 2)]
    fetched = [normalize_tick(_observation(index)) for index in (1, 5)]

    assert overlap_length(stored, fetched) == 0
