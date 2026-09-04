from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from tradebot.core.errors import AmbiguousLocalTimeError, NonexistentLocalTimeError
from tradebot.core.time_rules import (
    fx_session_bounds,
    fx_trading_day_start,
    is_fx_market_open,
    local_time_utc,
)


@pytest.mark.parametrize(
    ("local_date", "zone", "local_time", "expected"),
    [
        # US is on DST while the UK is not.
        (
            date(2025, 3, 17),
            "America/New_York",
            time(9, 30),
            datetime(2025, 3, 17, 13, 30, tzinfo=UTC),
        ),
        (date(2025, 3, 17), "Europe/London", time(8), datetime(2025, 3, 17, 8, tzinfo=UTC)),
        # UK has left DST while the US remains on DST.
        (
            date(2025, 10, 27),
            "America/New_York",
            time(9, 30),
            datetime(2025, 10, 27, 13, 30, tzinfo=UTC),
        ),
        (date(2025, 10, 27), "Europe/London", time(8), datetime(2025, 10, 27, 8, tzinfo=UTC)),
    ],
)
def test_mismatched_dst_weeks(
    local_date: date, zone: str, local_time: time, expected: datetime
) -> None:
    assert local_time_utc(local_date, local_time, zone) == expected


@pytest.mark.parametrize(
    ("timestamp", "expected_open"),
    [
        (datetime(2025, 1, 5, 21, 59, tzinfo=UTC), False),
        (datetime(2025, 1, 5, 22, 0, tzinfo=UTC), True),
        (datetime(2025, 7, 6, 20, 59, tzinfo=UTC), False),
        (datetime(2025, 7, 6, 21, 0, tzinfo=UTC), True),
        (datetime(2025, 1, 10, 21, 59, tzinfo=UTC), True),
        (datetime(2025, 1, 10, 22, 0, tzinfo=UTC), False),
        (datetime(2025, 7, 11, 21, 0, tzinfo=UTC), False),
    ],
)
def test_sunday_open_and_friday_close(timestamp: datetime, expected_open: bool) -> None:
    assert is_fx_market_open(timestamp) is expected_open


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (datetime(2024, 2, 29, 20, tzinfo=UTC), datetime(2024, 2, 28, 22, tzinfo=UTC)),
        (datetime(2024, 2, 29, 23, tzinfo=UTC), datetime(2024, 2, 29, 22, tzinfo=UTC)),
        (datetime(2025, 1, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, 22, tzinfo=UTC)),
    ],
)
def test_fx_trading_day_boundary_handles_leap_and_year(
    timestamp: datetime, expected: datetime
) -> None:
    assert fx_trading_day_start(timestamp) == expected


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        # A weekday boundary is inclusive.
        (datetime(2025, 1, 8, 22, tzinfo=UTC), datetime(2025, 1, 8, 22, tzinfo=UTC)),
        (datetime(2025, 7, 9, 21, tzinfo=UTC), datetime(2025, 7, 9, 21, tzinfo=UTC)),
        # Friday close and closed-weekend inputs retain Thursday's valid start.
        (datetime(2025, 1, 10, 22, tzinfo=UTC), datetime(2025, 1, 9, 22, tzinfo=UTC)),
        (datetime(2025, 1, 11, 18, tzinfo=UTC), datetime(2025, 1, 9, 22, tzinfo=UTC)),
        (datetime(2025, 1, 12, 18, tzinfo=UTC), datetime(2025, 1, 9, 22, tzinfo=UTC)),
        # Sunday open creates the next start inclusively.
        (datetime(2025, 1, 12, 22, tzinfo=UTC), datetime(2025, 1, 12, 22, tzinfo=UTC)),
    ],
)
def test_fx_trading_day_start_skips_closed_weekend_boundaries(
    timestamp: datetime, expected: datetime
) -> None:
    assert fx_trading_day_start(timestamp) == expected


@pytest.mark.parametrize(
    ("local_date", "expected"),
    [
        (date(2025, 1, 15), datetime(2025, 1, 15, 13, 30, tzinfo=UTC)),
        (date(2025, 7, 15), datetime(2025, 7, 15, 12, 30, tzinfo=UTC)),
    ],
)
def test_new_york_0830_conversion_in_standard_and_daylight_time(
    local_date: date, expected: datetime
) -> None:
    assert local_time_utc(local_date, time(8, 30), "America/New_York") == expected


def test_ambiguous_local_time_requires_fold() -> None:
    with pytest.raises(AmbiguousLocalTimeError):
        local_time_utc(date(2025, 11, 2), time(1, 30), "America/New_York")

    assert local_time_utc(date(2025, 11, 2), time(1, 30), "America/New_York", fold=0) == datetime(
        2025, 11, 2, 5, 30, tzinfo=UTC
    )
    assert local_time_utc(date(2025, 11, 2), time(1, 30), "America/New_York", fold=1) == datetime(
        2025, 11, 2, 6, 30, tzinfo=UTC
    )


def test_nonexistent_local_time_is_rejected() -> None:
    with pytest.raises(NonexistentLocalTimeError):
        local_time_utc(date(2025, 3, 9), time(2, 30), "America/New_York")
    with pytest.raises(NonexistentLocalTimeError, match="fold"):
        local_time_utc(date(2025, 3, 10), time(2, 30), "America/New_York", fold=1)


@pytest.mark.parametrize(
    ("zone", "local_clock"),
    [
        ("Europe/London", time(1, 30)),
        ("Europe/Berlin", time(2, 30)),
    ],
)
def test_european_spring_transition_gaps_are_rejected(zone: str, local_clock: time) -> None:
    with pytest.raises(NonexistentLocalTimeError):
        local_time_utc(date(2025, 3, 30), local_clock, zone)


@pytest.mark.parametrize(
    ("zone", "local_clock"),
    [
        ("Europe/London", time(1, 30)),
        ("Europe/Berlin", time(2, 30)),
    ],
)
def test_european_autumn_transition_folds_require_selection(zone: str, local_clock: time) -> None:
    with pytest.raises(AmbiguousLocalTimeError):
        local_time_utc(date(2025, 10, 26), local_clock, zone)
    assert local_time_utc(date(2025, 10, 26), local_clock, zone, fold=0) == datetime(
        2025, 10, 26, 0, 30, tzinfo=UTC
    )
    assert local_time_utc(date(2025, 10, 26), local_clock, zone, fold=1) == datetime(
        2025, 10, 26, 1, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        # Exactly at the boundary: the tick belongs to the NEW session, never both.
        (
            datetime(2025, 1, 8, 22, tzinfo=UTC),
            (datetime(2025, 1, 8, 22, tzinfo=UTC), datetime(2025, 1, 9, 22, tzinfo=UTC)),
        ),
        # One microsecond earlier is still the previous session.
        (
            datetime(2025, 1, 8, 21, 59, 59, 999999, tzinfo=UTC),
            (datetime(2025, 1, 7, 22, tzinfo=UTC), datetime(2025, 1, 8, 22, tzinfo=UTC)),
        ),
        # Sunday open starts the session labelled Monday.
        (
            datetime(2025, 1, 12, 22, tzinfo=UTC),
            (datetime(2025, 1, 12, 22, tzinfo=UTC), datetime(2025, 1, 13, 22, tzinfo=UTC)),
        ),
    ],
)
def test_fx_session_bounds_is_half_open(
    timestamp: datetime, expected: tuple[datetime, datetime]
) -> None:
    assert fx_session_bounds(timestamp) == expected


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2025, 1, 10, 22, tzinfo=UTC),  # Friday close instant
        datetime(2025, 1, 11, 18, tzinfo=UTC),  # Saturday
        datetime(2025, 1, 12, 18, tzinfo=UTC),  # Sunday before the open
    ],
)
def test_fx_session_bounds_fails_closed_when_the_market_is_shut(timestamp: datetime) -> None:
    """fx_trading_day_start answers with an already-closed session; bounds must not."""
    assert fx_session_bounds(timestamp) is None
    assert fx_trading_day_start(timestamp) is not None


@pytest.mark.parametrize("year", [2003, 2025])
def test_session_lengths_are_24h_and_closures_are_47_48_or_49h(year: int) -> None:
    """Scoped to the supported range: tzdata has a 23 h session in 1942 (US War Time)."""
    probe = datetime(year, 1, 1, 12, tzinfo=UTC)
    end_of_year = datetime(year + 1, 1, 1, tzinfo=UTC)
    seen_lengths: set[float] = set()
    seen_closures: set[float] = set()
    previous_end: datetime | None = None

    while probe < end_of_year:
        bounds = fx_session_bounds(probe)
        if bounds is not None:
            start, end = bounds
            seen_lengths.add((end - start).total_seconds() / 3600)
            if previous_end is not None and start > previous_end:
                seen_closures.add((start - previous_end).total_seconds() / 3600)
            previous_end = end
            probe = end + timedelta(hours=1)
            continue
        probe += timedelta(hours=1)

    assert seen_lengths == {24.0}
    assert seen_closures <= {47.0, 48.0, 49.0}
    assert seen_closures, "a full year must contain weekend closures"
