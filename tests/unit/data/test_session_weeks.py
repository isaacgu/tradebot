from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tradebot.core.errors import InvalidTimestampError
from tradebot.core.time_rules import NEW_YORK
from tradebot.data.session_weeks import (
    MAX_SESSION,
    MIN_SESSION,
    DstFingerprint,
    FingerprintStatus,
    WeekAuditStatus,
    WeeklyAudit,
    audit_weekly_bars,
    dst_mismatch_windows,
    expected_weeks,
    fingerprint_dst_alignment,
    session_closes,
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


def _audit(opens: list[datetime], **kw: object) -> WeeklyAudit:
    """Audit with a final close derived from the sample, so no session is dropped."""
    final = max(opens) + timedelta(hours=24) if opens else None
    return audit_weekly_bars(opens, zone=NEW_YORK, final_close=final, **kw)  # type: ignore[arg-type]


def _fingerprint(opens: list[datetime], **kw: object) -> DstFingerprint:
    final = max(opens) + timedelta(hours=24) if opens else None
    return fingerprint_dst_alignment(opens, zone=NEW_YORK, final_close=final, **kw)  # type: ignore[arg-type]


# --- close labelling -------------------------------------------------------------


def test_a_short_sunday_stub_keys_to_the_sunday_own_week() -> None:
    """The real close is what surfaces the stub as a sixth bar.

    2024-10-27 is a Sunday and ISO weekday 7 of 2024-W43. A stub opening 21:00 UTC
    truly closes at Monday 00:00 UTC, which keys to W43 — joining that week's weekday
    bars. Approximating the close as open + 24h would key it to W44 and hide it.
    """
    stub_open = _utc(2024, 10, 27, 21)
    real_close = _utc(2024, 10, 28, 0)
    naive_close = stub_open + timedelta(hours=24)

    assert session_week_key(real_close, zone=NEW_YORK) == "2024-W43"
    assert session_week_key(naive_close, zone=NEW_YORK) == "2024-W44", "the old approximation"


def test_closes_come_from_the_next_open_when_sessions_are_contiguous() -> None:
    opens = [_utc(2024, 10, 27, 21), _utc(2024, 10, 28, 0)]

    pairs = session_closes(opens, final_close=_utc(2024, 10, 29, 0))

    assert pairs[0].opened_at == _utc(2024, 10, 27, 21)
    assert pairs[0].closed_at == _utc(2024, 10, 28, 0)
    assert pairs[1].closed_at == _utc(2024, 10, 29, 0)


def test_a_weekend_gap_retains_an_honest_close_interval() -> None:
    """A closure proves an interval and week, not a fabricated close instant."""
    friday, sunday = _utc(2025, 1, 10, 22), _utc(2025, 1, 12, 22)

    pairs = session_closes([friday, sunday], final_close=_utc(2025, 1, 13, 22))

    assert pairs[0].closed_at is None
    assert pairs[0].earliest_close == friday + MIN_SESSION
    assert pairs[0].latest_close == friday + MAX_SESSION
    assert pairs[0].week_key(zone=NEW_YORK) == week_key(date(2025, 1, 10))


def test_a_gap_crossing_a_week_boundary_is_explicitly_ambiguous() -> None:
    sunday = _utc(2025, 1, 5, 0)
    wednesday = _utc(2025, 1, 8, 0)

    closure = session_closes([sunday, wednesday])[0]

    assert closure.closed_at is None
    assert closure.week_key(zone=ZoneInfo("UTC")) is None

    audit = audit_weekly_bars(
        [sunday, wednesday],
        zone=ZoneInfo("UTC"),
        final_close=_utc(2025, 1, 9, 0),
        min_interior_weeks=0,
    )
    assert audit.status is WeekAuditStatus.INSUFFICIENT_DATA
    assert audit.ambiguous_closes == (sunday.isoformat(),)
    assert set(audit.ambiguous_weeks) == {"2025-W01", "2025-W02"}


def test_the_final_session_is_dropped_without_an_explicit_close() -> None:
    opens = [_utc(2025, 1, 6, 22), _utc(2025, 1, 7, 22)]

    assert len(session_closes(opens)) == 1
    assert len(session_closes(opens, final_close=_utc(2025, 1, 8, 22))) == 2


@pytest.mark.parametrize(
    "final_close",
    [
        _utc(2025, 1, 7, 22),
        _utc(2025, 1, 7, 21),
        _utc(2025, 1, 9, 0),
    ],
)
def test_final_close_must_follow_the_last_open_within_the_session_bound(
    final_close: datetime,
) -> None:
    with pytest.raises(ValueError, match="strictly after"):
        session_closes([_utc(2025, 1, 7, 22)], final_close=final_close)


@pytest.mark.parametrize("field", ["session open", "final_close"])
def test_naive_timestamps_are_rejected(field: str) -> None:
    naive = datetime(2025, 1, 6, 22)
    opens = [naive] if field == "session open" else [_utc(2025, 1, 6, 22)]
    final = None if field == "session open" else naive

    with pytest.raises(InvalidTimestampError, match=field):
        session_closes(opens, final_close=final)


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
    assert spring.first_full_session == date(2025, 3, 10)
    assert spring.last_session == date(2025, 3, 30)
    assert "2025-W10" not in spring.fully_affected_weeks
    assert spring.fully_affected_weeks[0] == "2025-W11"

    # EU fall-back 2025-10-26 (Sunday) -> affected sessions start Monday 10-27.
    assert autumn.first_full_session == date(2025, 10, 27)
    assert autumn.last_session == date(2025, 11, 2)
    assert autumn.fully_affected_weeks[0] == "2025-W44"


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

    assert spring.transition == us_spring
    assert spring.first_full_session == us_spring + timedelta(days=1)
    assert spring.last_session == eu_spring
    assert autumn.transition == eu_autumn
    assert autumn.first_full_session == eu_autumn + timedelta(days=1)
    assert autumn.last_session == us_autumn


def test_december_transition_arithmetic_does_not_wrap_wrongly() -> None:
    """The last-Sunday helper crosses a year boundary via the following month."""
    autumn = dst_mismatch_windows(2025)[1]

    assert autumn.last_session.year == 2025


# --- weekly audit ----------------------------------------------------------------


def test_a_clean_run_flags_nothing() -> None:
    opens = _weekday_opens(date(2025, 1, 6), weeks=6)

    audit = _audit(opens)

    assert audit.status is WeekAuditStatus.INDETERMINATE
    assert not audit.anomalies
    assert audit.missing_weeks == ()
    assert len(audit.interior_weeks) == 4, "first and last weeks are partial by nature"


def test_a_completely_missing_interior_week_is_visible() -> None:
    """The bug this closes: a week with no bars has no key to iterate."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=3) + _weekday_opens(date(2025, 2, 3), weeks=3)

    audit = _audit(opens)

    assert "2025-W05" in audit.missing_weeks, "the wholly absent week has no key of its own"
    assert audit.weeks_off_expected["2025-W05"] == 0
    assert audit.anomalies


def test_a_sixth_bar_in_a_week_is_flagged() -> None:
    """A Sunday stub inside a DST mismatch window is exactly this shape."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=7)
    opens.append(_utc(2025, 1, 26, 0))  # a Sunday stub closing inside 2025-W04

    audit = _audit(opens)

    assert audit.weeks_off_expected == {"2025-W04": 6}
    assert audit.missing_weeks == ()


def test_duplicate_bar_opens_are_reported() -> None:
    opens = _weekday_opens(date(2025, 1, 6), weeks=7)
    opens.append(opens[12])

    audit = _audit(opens)

    assert len(audit.duplicate_opens) == 1
    assert opens[12].isoformat() in audit.duplicate_opens
    assert audit.status is WeekAuditStatus.FAILED


def test_the_first_and_last_weeks_are_never_judged() -> None:
    """A sample almost always begins and ends mid-week."""
    opens = [
        _utc(2025, 1, 9),  # a lone Thursday, partial first week
        *_weekday_opens(date(2025, 1, 13), weeks=1),
        _utc(2025, 1, 20),  # a lone Monday, partial last week
    ]

    audit = _audit(opens)

    assert audit.interior_weeks == ("2025-W03",)
    assert audit.status is WeekAuditStatus.INSUFFICIENT_DATA


def test_an_empty_sample_audits_cleanly_rather_than_raising() -> None:
    audit = audit_weekly_bars([], zone=NEW_YORK)

    assert audit.status is WeekAuditStatus.INSUFFICIENT_DATA, "absence must never pass"
    assert audit.counts == {}
    assert audit.interior_weeks == ()


def test_an_audit_spanning_a_53_week_year_enumerates_every_week() -> None:
    opens = _weekday_opens(date(2020, 12, 14), weeks=5)

    audit = _audit(opens)

    assert "2020-W53" in audit.interior_weeks
    assert not audit.anomalies


# --- transition stub vs fully affected -------------------------------------------


def test_a_window_reports_the_transition_stub_week_separately() -> None:
    """Monday-start alone would miss the stub; the measured 2024-W43 case proves it.

    EU fell back Sunday 2024-10-27, so the fully affected sessions begin Monday
    2024-10-28 in 2024-W44 — but the six-bar week the live probe found was W43, the
    week the transition Sunday itself closes.
    """
    autumn = dst_mismatch_windows(2024)[1]

    assert autumn.transition == date(2024, 10, 27)
    assert autumn.transition_weeks == ("2024-W43",)
    assert "2024-W43" not in autumn.fully_affected_weeks
    assert autumn.fully_affected_weeks[0] == "2024-W44"
    assert "2024-W43" in autumn.audit_weeks, "the fingerprint must inspect the stub week"
    assert "2024-W44" in autumn.audit_weeks


def test_audit_weeks_is_ordered_and_deduplicated() -> None:
    spring = dst_mismatch_windows(2025)[0]

    assert spring.audit_weeks[0] == spring.transition_weeks[0]
    assert len(spring.audit_weeks) == len(set(spring.audit_weeks))


def test_the_stub_week_matches_where_a_sunday_session_actually_keys() -> None:
    """The window's stub week and the session key must agree, or the check misfires."""
    autumn = dst_mismatch_windows(2024)[1]
    sunday_session = datetime(2024, 10, 27, 0, tzinfo=UTC)

    assert session_week_key(sunday_session, zone=NEW_YORK) in autumn.transition_weeks


# --- status model ----------------------------------------------------------------


def test_without_a_calendar_a_clean_run_is_indeterminate_not_pass() -> None:
    """SPEC 2.4: a holiday-shortened week is legitimate and only the calendar knows."""
    audit = audit_weekly_bars(_weekday_opens(date(2025, 1, 6), weeks=8), zone=NEW_YORK)

    assert audit.status is WeekAuditStatus.INDETERMINATE
    assert not audit.calendar_supplied
    assert "calendar" in audit.reason


def test_with_a_calendar_a_matching_run_passes() -> None:
    opens = _weekday_opens(date(2025, 1, 6), weeks=8)
    audit_without = _audit(opens)
    calendar = dict.fromkeys(audit_without.interior_weeks, 5)

    audit = _audit(opens, expected_per_week=calendar)

    assert audit.status is WeekAuditStatus.PASSED
    assert audit.calendar_supplied


def test_a_holiday_shortened_week_passes_when_the_calendar_expects_four() -> None:
    """The live index pass showed four-bar weeks around Good Friday; not a defect."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=8)
    short = [moment for moment in opens if moment.date() != date(2025, 1, 24)]
    audit_without = _audit(short)
    calendar = dict.fromkeys(audit_without.interior_weeks, 5) | {"2025-W04": 4}

    audit = _audit(short, expected_per_week=calendar)

    assert audit.status is WeekAuditStatus.PASSED
    assert audit.weeks_off_expected == {}


def test_the_same_short_week_fails_when_the_calendar_expects_five() -> None:
    opens = _weekday_opens(date(2025, 1, 6), weeks=8)
    short = [moment for moment in opens if moment.date() != date(2025, 1, 24)]
    audit_without = _audit(short)
    calendar = dict.fromkeys(audit_without.interior_weeks, 5)

    audit = _audit(short, expected_per_week=calendar)

    assert audit.status is WeekAuditStatus.FAILED
    assert audit.weeks_off_expected == {"2025-W04": 4}


def test_too_few_interior_weeks_is_insufficient_not_a_pass() -> None:
    opens = _weekday_opens(date(2025, 1, 6), weeks=3)

    audit = _audit(opens)

    assert audit.status is WeekAuditStatus.INSUFFICIENT_DATA
    assert "interior week" in audit.reason


def test_duplicates_fail_even_with_a_satisfied_calendar() -> None:
    """A duplicated bar is never legitimate, so it outranks a matching calendar."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=8)
    opens.append(opens[10])
    calendar = dict.fromkeys(_audit(opens).interior_weeks, 5)

    audit = _audit(opens, expected_per_week=calendar)

    assert audit.status is WeekAuditStatus.FAILED
    assert audit.duplicate_opens
    assert "cannot be interpreted" in audit.reason


# --- the four status edge cases ---------------------------------------------------


def test_an_empty_calendar_is_not_a_calendar() -> None:
    """`{}` must not yield PASSED by defaulting every uncovered week to five."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=8)

    audit = audit_weekly_bars(opens, zone=NEW_YORK, expected_per_week={})

    assert audit.status is WeekAuditStatus.INSUFFICIENT_DATA
    assert audit.uncovered_weeks == audit.interior_weeks
    assert "does not cover" in audit.reason


def test_a_partially_covering_calendar_names_the_uncovered_weeks() -> None:
    opens = _weekday_opens(date(2025, 1, 6), weeks=8)
    interior = _audit(opens).interior_weeks
    partial = dict.fromkeys(interior[:2], 5)

    audit = _audit(opens, expected_per_week=partial)

    assert audit.status is WeekAuditStatus.INSUFFICIENT_DATA
    assert audit.uncovered_weeks == interior[2:]


def test_duplicates_fail_even_in_a_sample_too_short_to_judge() -> None:
    """A known defect must not be buried under INSUFFICIENT_DATA."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=2)
    opens.append(opens[3])

    audit = _audit(opens)

    assert audit.status is WeekAuditStatus.FAILED
    assert len(audit.interior_weeks) < 4, "the sample really is too short to judge counts"
    assert audit.duplicate_opens


def test_an_extra_bar_fails_without_any_calendar() -> None:
    """A holiday can only remove sessions, so a sixth bar is never explicable."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=7)
    opens.append(_utc(2025, 1, 26, 0))  # a Sunday stub closing inside 2025-W04

    audit = _audit(opens)

    assert audit.status is WeekAuditStatus.FAILED
    assert audit.structural_excess == {"2025-W04": 6}
    assert "no calendar or sample size can legalise it" in audit.reason


def test_a_structural_excess_does_not_hide_shortfall_evidence() -> None:
    """Verdict precedence must not short-circuit the evidence the fingerprint needs."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=7)
    opens = [moment for moment in opens if moment.date() != date(2025, 1, 15)]
    opens.append(_utc(2025, 1, 26, 0))

    audit = _audit(opens)

    assert audit.status is WeekAuditStatus.FAILED
    assert audit.structural_excess == {"2025-W04": 6}
    assert audit.weeks_shortfall == {"2025-W03": 4}


# --- the check-10(c) fingerprint -------------------------------------------------


def _autumn_2025_opens(weeks: int = 7) -> list[datetime]:
    """Weekday opens spanning the 2025 autumn mismatch window with margin either side."""
    return _weekday_opens(date(2025, 10, 6), weeks=weeks)


def test_quiet_january_weeks_cannot_satisfy_the_fingerprint() -> None:
    """The generic audit is happy with any four weeks; 10(c) is not."""
    opens = _weekday_opens(date(2025, 1, 6), weeks=8)

    fingerprint = _fingerprint(opens)

    assert fingerprint.status is FingerprintStatus.INSUFFICIENT_COVERAGE
    assert fingerprint.covered_windows == ()
    assert "never spans one" in fingerprint.reason


def test_one_clean_season_without_a_calendar_is_only_provisional() -> None:
    """Autumn alone cannot speak for spring: the two shift in opposite directions.

    An earlier version asserted ALIGNED here, which contradicted both SPEC's
    spring-and-autumn requirement and the probe's own stated limitation that 10(c)
    cannot pass without the expected-liquidity calendar.
    """
    fingerprint = _fingerprint(_autumn_2025_opens())

    assert fingerprint.status is FingerprintStatus.PROVISIONALLY_ALIGNED
    assert "2025-autumn" in fingerprint.covered_windows
    assert fingerprint.seasons_covered == ("autumn",)
    assert "spring and autumn shift in opposite directions" in fingerprint.reason
    assert "no expected-liquidity calendar" in fingerprint.reason
    assert fingerprint.anomalous_weeks == {}


def test_a_sunday_stub_inside_a_covered_window_is_misaligned() -> None:
    """The measured FBS shape exactly: 00:00-boundary bars plus a Sunday 00:00 stub.

    The stub's real close is the Monday 00:00 open, which in New York is the Sunday
    evening — so it keys to the Sunday's own week and shows up as a sixth bar there.
    """
    opens = _weekday_opens(date(2025, 10, 6), weeks=7, hour=0)
    opens.append(_utc(2025, 10, 26, 0))

    fingerprint = _fingerprint(opens)

    assert fingerprint.status is FingerprintStatus.MISALIGNED
    assert "2025-W43" in fingerprint.anomalous_weeks
    assert "stub bar" in fingerprint.reason


def test_a_short_week_in_a_covered_window_is_indeterminate_without_a_calendar() -> None:
    """Good Friday falls inside the spring window, so a shortfall is explicable."""
    opens = [m for m in _autumn_2025_opens() if m.date() != date(2025, 10, 22)]

    fingerprint = _fingerprint(opens)

    assert fingerprint.status is FingerprintStatus.INDETERMINATE
    assert "expected-liquidity calendar" in fingerprint.reason


def test_a_short_week_against_a_calendar_expecting_five_is_misaligned() -> None:
    opens = [m for m in _autumn_2025_opens() if m.date() != date(2025, 10, 22)]
    interior = _audit(opens).interior_weeks
    calendar = dict.fromkeys(interior, 5)

    fingerprint = _fingerprint(opens, expected_per_week=calendar)

    assert fingerprint.status is FingerprintStatus.MISALIGNED


def test_a_short_week_the_calendar_expects_is_not_an_anomaly() -> None:
    opens = [m for m in _autumn_2025_opens() if m.date() != date(2025, 10, 22)]
    interior = _audit(opens).interior_weeks
    calendar = dict.fromkeys(interior, 5) | {"2025-W43": 4}

    fingerprint = _fingerprint(opens, expected_per_week=calendar)

    assert fingerprint.anomalous_weeks == {}, "the calendar explains the short week"
    assert fingerprint.status is FingerprintStatus.PROVISIONALLY_ALIGNED, "autumn only"


def test_a_partially_covered_window_is_listed_but_not_judged() -> None:
    """Coverage requires every week the window names, stub week included."""
    opens = _weekday_opens(date(2025, 10, 20), weeks=4)

    fingerprint = _fingerprint(opens)

    assert fingerprint.status is FingerprintStatus.INSUFFICIENT_COVERAGE
    assert "2025-autumn" in fingerprint.partially_covered_windows
    assert "2025-autumn" not in fingerprint.covered_windows


def test_duplicates_block_a_fingerprint_verdict() -> None:
    opens = _autumn_2025_opens()
    opens.append(opens[10])

    fingerprint = _fingerprint(opens)

    assert fingerprint.status is FingerprintStatus.INDETERMINATE
    assert "duplicate" in fingerprint.reason


def test_an_unjudgeable_audit_never_reports_alignment() -> None:
    """An INSUFFICIENT_DATA audit leaves no deviations, which must not read as ALIGNED."""
    opens = _autumn_2025_opens()

    fingerprint = fingerprint_dst_alignment(opens, zone=NEW_YORK, expected_per_week={})

    assert fingerprint.status is FingerprintStatus.INSUFFICIENT_COVERAGE
    assert "could not judge" in fingerprint.reason


def test_the_fingerprint_records_counts_for_every_window_week() -> None:
    fingerprint = _fingerprint(_autumn_2025_opens())

    counts = fingerprint.window_counts["2025-autumn"]
    assert set(counts) == {"2025-W43", "2025-W44"}


def _full_2025_opens() -> list[datetime]:
    """A full close-labelled year with both mismatch seasons safely interior."""
    return _weekday_opens(date(2025, 1, 6), weeks=52, hour=0)


def _full_2025_calendar() -> dict[str, int]:
    return dict.fromkeys(_audit(_full_2025_opens()).interior_weeks, 5)


def test_a_six_bar_july_week_cannot_yield_a_clean_fingerprint() -> None:
    opens = _full_2025_opens()
    opens.append(_utc(2025, 7, 13, 0))
    calendar = _full_2025_calendar()

    audit = _audit(opens, expected_per_week=calendar)
    fingerprint = _fingerprint(opens, expected_per_week=calendar)

    assert audit.status is WeekAuditStatus.FAILED
    assert audit.structural_excess == {"2025-W28": 6}
    assert fingerprint.status is FingerprintStatus.INDETERMINATE


def test_a_calendar_shortfall_outside_the_windows_cannot_yield_clean() -> None:
    opens = [m for m in _full_2025_opens() if m.date() != date(2025, 7, 16)]
    calendar = _full_2025_calendar()

    audit = _audit(opens, expected_per_week=calendar)
    fingerprint = _fingerprint(opens, expected_per_week=calendar)

    assert audit.status is WeekAuditStatus.FAILED
    assert audit.weeks_shortfall == {"2025-W29": 4}
    assert fingerprint.status is FingerprintStatus.INDETERMINATE


def test_outside_excess_cannot_hide_an_inside_shortfall() -> None:
    opens = [m for m in _full_2025_opens() if m.date() != date(2025, 3, 19)]
    opens.append(_utc(2025, 7, 13, 0))
    calendar = _full_2025_calendar()

    audit = _audit(opens, expected_per_week=calendar)
    fingerprint = _fingerprint(opens, expected_per_week=calendar)

    assert audit.structural_excess == {"2025-W28": 6}
    assert audit.weeks_shortfall == {"2025-W12": 4}
    assert fingerprint.status is FingerprintStatus.MISALIGNED
    assert fingerprint.anomalous_weeks["2025-W12"] == 4


def test_no_failed_audit_can_enumerate_as_clean_across_defect_combinations() -> None:
    """Exhaust all 64 combinations of outside, spring and autumn defects."""
    baseline = _full_2025_opens()
    calendar = _full_2025_calendar()
    removals = {
        1: date(2025, 7, 16),  # outside-window shortfall
        3: date(2025, 3, 19),  # spring shortfall
        5: date(2025, 10, 29),  # autumn shortfall
    }
    additions = {
        0: _utc(2025, 7, 13, 0),  # outside-window excess
        2: _utc(2025, 3, 9, 0),  # spring transition-week excess
        4: _utc(2025, 10, 26, 0),  # autumn transition-week excess
    }

    for mask in range(64):
        removed = {day for bit, day in removals.items() if mask & (1 << bit)}
        opens = [moment for moment in baseline if moment.date() not in removed]
        opens.extend(moment for bit, moment in additions.items() if mask & (1 << bit))

        audit = _audit(opens, expected_per_week=calendar)
        fingerprint = _fingerprint(opens, expected_per_week=calendar)

        if audit.status is WeekAuditStatus.FAILED:
            assert fingerprint.status not in {
                FingerprintStatus.ALIGNED,
                FingerprintStatus.PROVISIONALLY_ALIGNED,
            }, f"mask {mask:06b} returned clean from a failed audit"
        if mask & 0b111100:
            assert fingerprint.status is FingerprintStatus.MISALIGNED, f"mask {mask:06b}"
