from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tradebot.core.time_rules import NEW_YORK
from tradebot.data.session_weeks import (
    audit_weekly_bars,
    dst_mismatch_windows,
    expected_weeks,
    session_week_key,
    week_key,
)


def _utc(year: int, month: int, day: int, hour: int = 22) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _weekday_opens(first_monday: date, weeks: int, hour: int = 22) -> list[datetime]:
    """Five opens per week, Monday to Friday, at a fixed UTC hour."""
    opens: list[datetime] = []
    for week in range(weeks):
        monday = first_monday + timedelta(weeks=week)
        for offset in range(5):
            day = monday + timedelta(days=offset)
            opens.append(datetime(day.year, day.month, day.day, hour, tzinfo=UTC))
    return opens


# --- close labelling -------------------------------------------------------------


def test_a_sunday_open_keys_to_the_week_its_own_date_ends() -> None:
    """The stub surfaces as a sixth bar rather than hiding in the next week.

    2024-10-27 is a Sunday and ISO weekday 7 of 2024-W43, so a session opening then
    joins the Monday-to-Friday bars that week — which is what made the real probe
    report six bars for that week.
    """
    assert session_week_key(_utc(2024, 10, 27, 0), zone=NEW_YORK) == "2024-W43"
    assert session_week_key(_utc(2024, 10, 21, 0), zone=NEW_YORK) == "2024-W43"
    assert session_week_key(_utc(2024, 10, 25, 0), zone=NEW_YORK) == "2024-W43"


def test_keying_on_the_open_would_split_the_week_but_the_close_does_not() -> None:
    """A 22:00 UTC Sunday open is the Monday session; the close keeps them together."""
    sunday_open = _utc(2025, 1, 5, 22)
    monday_open = _utc(2025, 1, 6, 22)

    assert week_key(sunday_open) != week_key(monday_open), "the opens straddle a week"
    assert session_week_key(sunday_open, zone=NEW_YORK) == session_week_key(
        monday_open, zone=NEW_YORK
    )


# --- ISO year and week 53 --------------------------------------------------------


def test_week_key_uses_the_iso_year_not_the_calendar_year() -> None:
    assert week_key(datetime(2024, 12, 30, 12, tzinfo=UTC)) == "2025-W01"
    assert week_key(datetime(2023, 1, 1, 12, tzinfo=UTC)) == "2022-W52"


def test_expected_weeks_crosses_a_53_week_year() -> None:
    """2020 has 53 ISO weeks; the enumeration must not skip W53."""
    span = expected_weeks("2020-W52", "2021-W02")

    assert span == ("2020-W52", "2020-W53", "2021-W01", "2021-W02")


def test_expected_weeks_crosses_an_iso_year_without_a_53rd_week() -> None:
    span = expected_weeks("2024-W51", "2025-W01")

    assert span == ("2024-W51", "2024-W52", "2025-W01")


def test_expected_weeks_is_inclusive_and_single_week_safe() -> None:
    assert expected_weeks("2025-W10", "2025-W10") == ("2025-W10",)


# --- DST mismatch windows --------------------------------------------------------


def test_both_dst_windows_are_returned_for_a_year() -> None:
    spring, autumn = dst_mismatch_windows(2025)

    assert spring.label == "2025-spring"
    assert autumn.label == "2025-autumn"


def test_a_window_starts_the_monday_after_the_transition_sunday() -> None:
    """The transition Sunday closes the PREVIOUS week; including it dilutes the check."""
    spring, autumn = dst_mismatch_windows(2025)

    # US spring-forward 2025-03-09 (Sunday) -> affected sessions start Monday 03-10.
    assert spring.first_session == date(2025, 3, 10)
    assert spring.last_session == date(2025, 3, 30)
    assert "2025-W10" not in spring.weeks, "the transition Sunday's own week is excluded"
    assert spring.weeks[0] == "2025-W11"

    # EU fall-back 2025-10-26 (Sunday) -> affected sessions start Monday 10-27.
    assert autumn.first_session == date(2025, 10, 27)
    assert autumn.last_session == date(2025, 11, 2)
    assert autumn.weeks[0] == "2025-W44"


@pytest.mark.parametrize(
    ("year", "us_spring", "eu_spring", "eu_autumn", "us_autumn"),
    [
        (2025, date(2025, 3, 9), date(2025, 3, 30), date(2025, 10, 26), date(2025, 11, 2)),
        (2024, date(2024, 3, 10), date(2024, 3, 31), date(2024, 10, 27), date(2024, 11, 3)),
        (2026, date(2026, 3, 8), date(2026, 3, 29), date(2026, 10, 25), date(2026, 11, 1)),
    ],
)
def test_window_bounds_match_the_real_transition_calendar(
    year: int, us_spring: date, eu_spring: date, eu_autumn: date, us_autumn: date
) -> None:
    spring, autumn = dst_mismatch_windows(year)

    assert spring.first_session == us_spring + timedelta(days=1)
    assert spring.last_session == eu_spring
    assert autumn.first_session == eu_autumn + timedelta(days=1)
    assert autumn.last_session == us_autumn


def test_december_transition_arithmetic_does_not_wrap_wrongly() -> None:
    """The last-Sunday helper crosses a year boundary via the following month."""
    autumn = dst_mismatch_windows(2025)[1]

    assert autumn.last_session.year == 2025


# --- weekly audit ----------------------------------------------------------------


def test_a_clean_run_flags_nothing() -> None:
    opens = _weekday_opens(date(2025, 1, 6), weeks=6)

    audit = audit_weekly_bars(opens, zone=NEW_YORK)

    assert audit.clean
    assert audit.missing_weeks == ()
    assert audit.weeks_not_five == {}
    assert len(audit.interior_weeks) == 4, "first and last weeks are partial by nature"


def test_a_completely_missing_interior_week_is_visible() -> None:
    """The bug this closes: a week with no bars has no key to iterate."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=2) + _weekday_opens(date(2025, 1, 27), weeks=2)

    audit = audit_weekly_bars(opens, zone=NEW_YORK)

    assert "2025-W04" in audit.missing_weeks
    assert audit.weeks_not_five["2025-W04"] == 0
    assert not audit.clean


def test_a_sixth_bar_in_a_week_is_flagged() -> None:
    """A Sunday stub inside a DST mismatch window is exactly this shape."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=4)
    opens.append(_utc(2025, 1, 19, 0))  # a Sunday stub closing inside 2025-W03

    audit = audit_weekly_bars(opens, zone=NEW_YORK)

    assert audit.weeks_not_five == {"2025-W03": 6}
    assert audit.missing_weeks == ()


def test_duplicate_bar_opens_are_reported() -> None:
    opens = _weekday_opens(date(2025, 1, 6), weeks=4)
    opens.append(opens[7])

    audit = audit_weekly_bars(opens, zone=NEW_YORK)

    assert len(audit.duplicate_opens) == 1
    assert opens[7].isoformat() in audit.duplicate_opens
    assert not audit.clean


def test_the_first_and_last_weeks_are_never_judged() -> None:
    """A sample almost always begins and ends mid-week."""
    opens = [
        _utc(2025, 1, 9),  # a lone Thursday, partial first week
        *_weekday_opens(date(2025, 1, 13), weeks=1),
        _utc(2025, 1, 20),  # a lone Monday, partial last week
    ]

    audit = audit_weekly_bars(opens, zone=NEW_YORK)

    assert audit.interior_weeks == ("2025-W03",)
    assert audit.weeks_not_five == {}


def test_an_empty_sample_audits_cleanly_rather_than_raising() -> None:
    audit = audit_weekly_bars([], zone=NEW_YORK)

    assert audit.clean
    assert audit.counts == {}
    assert audit.interior_weeks == ()


def test_an_audit_spanning_a_53_week_year_enumerates_every_week() -> None:
    opens = _weekday_opens(date(2020, 12, 14), weeks=5)

    audit = audit_weekly_bars(opens, zone=NEW_YORK)

    assert "2020-W53" in audit.interior_weeks
    assert audit.clean
