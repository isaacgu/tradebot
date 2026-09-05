from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradebot.core.errors import InvalidTimestampError
from tradebot.core.types import QualityFlag
from tradebot.data.normalize import TickObservation, availability_key, normalize_tick


def _utc(hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2025, 3, 17, hour, minute, tzinfo=UTC)


def _observation(**changes: object) -> TickObservation:
    fields: dict[str, object] = {
        "instrument": "GBP_USD",
        "ts_event": _utc(12),
        "ts_recv": _utc(12),
        "bid": Decimal("1.29000"),
        "ask": Decimal("1.29010"),
    }
    return TickObservation(**(fields | changes))  # type: ignore[arg-type]


def test_a_present_receipt_stamp_is_never_modified() -> None:
    """ts_recv is what the staleness watchdog measures; normalising it erodes NN-4."""
    tick = normalize_tick(_observation(ts_event=_utc(12), ts_recv=_utc(12, 1)))

    assert tick.ts_recv == _utc(12, 1)
    assert tick.ts_event == _utc(12)
    assert tick.available_at == _utc(12, 1)
    assert tick.quality_flags == ()


def test_a_missing_receipt_stamp_is_imputed_as_exactly_ts_event() -> None:
    """No latency knob: a second latency parameter would double-count SPEC 6.2's."""
    tick = normalize_tick(_observation(ts_recv=None))

    assert tick.ts_recv == tick.ts_event == _utc(12)
    assert tick.available_at == _utc(12)
    assert tick.quality_flags == (QualityFlag.TS_RECV_IMPUTED,)


def test_venue_ahead_of_local_is_flagged_and_measured_not_corrected() -> None:
    tick = normalize_tick(_observation(ts_event=_utc(12, 1), ts_recv=_utc(12)))

    assert tick.ts_recv == _utc(12), "the raw local observation survives"
    assert tick.available_at == _utc(12, 1), "the key dominates both stamps"
    assert tick.quality_flags == (QualityFlag.CLOCK_SKEW,)
    assert tick.skew_lb.total_seconds() == 60


def test_backfilled_observations_are_tagged() -> None:
    """SPEC 4.5: tagged so they are never admitted as fresh."""
    tick = normalize_tick(_observation(ts_recv=_utc(12, 5), backfilled=True))

    assert QualityFlag.BACKFILLED in tick.quality_flags


def test_flags_are_sorted_and_deduplicated_for_byte_identical_reingest() -> None:
    """NN-10 and SPEC 4.6: the same source bytes must normalize identically."""
    first = normalize_tick(
        _observation(
            ts_event=_utc(12, 1),
            ts_recv=None,
            backfilled=True,
            source_flags=("VENDOR_SUSPECT", "CLOCK_SKEW"),
        )
    )
    second = normalize_tick(
        _observation(
            ts_event=_utc(12, 1),
            ts_recv=None,
            backfilled=True,
            source_flags=("CLOCK_SKEW", "VENDOR_SUSPECT"),
        )
    )

    assert first.quality_flags == second.quality_flags
    assert first.quality_flags == tuple(sorted(set(first.quality_flags)))
    assert first.quality_flags == (
        "BACKFILLED",
        "CLOCK_SKEW",
        "TS_RECV_IMPUTED",
        "VENDOR_SUSPECT",
    )


def test_an_imputed_stamp_never_also_reports_skew() -> None:
    """Imputation sets ts_recv == ts_event exactly, so no skew can be inferred."""
    tick = normalize_tick(_observation(ts_recv=None))

    assert QualityFlag.CLOCK_SKEW not in tick.quality_flags
    assert tick.skew_lb.total_seconds() == 0


def test_availability_key_is_the_maximum_of_both_stamps() -> None:
    assert availability_key(_utc(12), _utc(11)) == _utc(12)
    assert availability_key(_utc(11), _utc(12)) == _utc(12)
    assert availability_key(_utc(12), _utc(12)) == _utc(12)


@pytest.mark.parametrize("field", ["ts_event", "ts_recv"])
def test_normalize_rejects_a_naive_stamp(field: str) -> None:
    with pytest.raises(InvalidTimestampError, match=field):
        normalize_tick(_observation(**{field: datetime(2025, 3, 17, 12)}))


def test_the_normalizer_output_is_admissible_by_construction() -> None:
    """Whatever it returns must satisfy the Tick invariant, including under skew."""
    for observation in (
        _observation(ts_recv=None),
        _observation(ts_event=_utc(12, 1), ts_recv=_utc(12)),
        _observation(ts_event=_utc(12), ts_recv=_utc(12, 1)),
    ):
        tick = normalize_tick(observation)
        assert tick.available_at >= tick.ts_event
        assert tick.available_at >= tick.ts_recv
