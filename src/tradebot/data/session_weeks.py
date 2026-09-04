"""Session-week auditing for the SPEC 4.4 check-10(c) alignment fingerprint.

Check 10(c) requires a full trading week to contain exactly five daily bars,
"evaluated SPECIFICALLY inside the US/EU DST mismatch windows" — a server following
EU dates is aligned with a 17:00 New York boundary for roughly 48 weeks a year and
emits its one-hour stub only in those windows, so a randomly sampled week passes 48
times in 52.

Four details separate a working check from a noisy or dishonest one.

**Weeks are keyed by the session's CLOSE** (SPEC 3.4's labelling rule). A bar opening
Sunday 22:00 UTC belongs to a week that Sunday *ends* in ISO terms; keying on the open
would split every week and flag all of them.

**Absent weeks are enumerated, not merely counted.** A week with no bars has no key to
iterate, so an audit built from observed keys alone cannot see a wholly missing week.

**A mismatch window has two distinct week sets.** The *fully affected* sessions begin
the Monday after the transition Sunday — but the transition Sunday itself closes the
*preceding* week, and that is exactly where a stub bar lands. Auditing only the Monday
onward misses the anomaly the check exists to find; auditing only the transition week
misses the sustained divergence. Both are reported.

**The audit never passes on absence.** Five bars a week is the expectation for a 24/5
instrument in a normal week, but a holiday-shortened week legitimately holds four, and
SPEC 2.4 puts that knowledge in the expected-liquidity calendar. Without that calendar
the audit reports :data:`WeekAuditStatus.INDETERMINATE` — never ``PASSED`` — and with too
little data it reports ``INSUFFICIENT_DATA``. A quality check that returns "fine" when
it cannot tell is worse than no check.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

BARS_PER_FULL_WEEK = 5
MIN_INTERIOR_WEEKS = 4
_NOMINAL_SESSION = timedelta(hours=24)


class WeekAuditStatus(StrEnum):
    """Outcome of a weekly-bar audit. Only ``PASSED`` asserts correctness."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INDETERMINATE = "INDETERMINATE"
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class MismatchWindow:
    """A span where US and EU daylight time disagree, in close-labelled ISO weeks."""

    label: str
    transition: date
    first_full_session: date
    last_session: date
    transition_weeks: tuple[str, ...]
    fully_affected_weeks: tuple[str, ...]

    @property
    def audit_weeks(self) -> tuple[str, ...]:
        """Every week the fingerprint should inspect: stub week plus affected weeks."""
        merged = list(self.transition_weeks)
        merged.extend(week for week in self.fully_affected_weeks if week not in merged)
        return tuple(merged)


@dataclass(frozen=True, slots=True)
class WeeklyAudit:
    """The outcome of auditing daily-bar opens against the five-per-week rule."""

    status: WeekAuditStatus
    counts: Mapping[str, int]
    interior_weeks: tuple[str, ...]
    missing_weeks: tuple[str, ...]
    weeks_off_expected: Mapping[str, int]
    duplicate_opens: tuple[str, ...]
    calendar_supplied: bool
    reason: str

    @property
    def anomalies(self) -> bool:
        """Return whether anything at all deviated, regardless of pass/fail status."""
        return bool(self.missing_weeks or self.weeks_off_expected or self.duplicate_opens)


def _nth_sunday(year: int, month: int, nth: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))


def _last_sunday(year: int, month: int) -> date:
    following = date(year + (month == 12), (month % 12) + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def week_key(moment: datetime | date) -> str:
    """Return the ISO week key of *moment*, e.g. ``2024-W43``, using the ISO year."""
    iso = moment.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def session_week_key(
    open_instant: datetime,
    *,
    zone: ZoneInfo,
    session_length: timedelta = _NOMINAL_SESSION,
) -> str:
    """Return the close-labelled ISO week key for a session opening at *open_instant*.

    The close is approximated as ``open + session_length``; a 23- or 25-hour DST
    session cannot move a close across a week boundary, so this is exact for grouping.
    A short stub session attaches to the ISO week its own open date ends, which is what
    surfaces it as a sixth bar rather than hiding it in the following week.
    """
    return week_key((open_instant + session_length).astimezone(zone))


def _monday_of(key: str) -> date:
    iso_year, iso_week = key.split("-W")
    return date.fromisocalendar(int(iso_year), int(iso_week), 1)


def expected_weeks(first: str, last: str) -> tuple[str, ...]:
    """Return every ISO week key from *first* to *last* inclusive, gaps included.

    Walks Mondays via :meth:`date.fromisocalendar`, so 53-week years and ISO-year
    rollovers are handled by the calendar rather than arithmetic on the label.
    """
    monday, end = _monday_of(first), _monday_of(last)
    keys: list[str] = []
    while monday <= end:
        keys.append(week_key(monday))
        monday += timedelta(weeks=1)
    return tuple(keys)


def _weeks_between(first: date, last: date) -> tuple[str, ...]:
    keys: list[str] = []
    cursor = first
    while cursor <= last:
        key = week_key(cursor)
        if key not in keys:
            keys.append(key)
        cursor += timedelta(days=1)
    return tuple(keys)


def dst_mismatch_windows(year: int) -> tuple[MismatchWindow, ...]:
    """Return the two spans where US and EU daylight time disagree, post-2007 rules.

    Each window carries **both** week sets. ``fully_affected_weeks`` starts the Monday
    after the transition Sunday, because that Sunday closes the preceding week.
    ``transition_weeks`` is that preceding week — where a stub bar actually appears,
    as the live 2024-W43 six-bar result demonstrated.
    """
    return (
        _window(f"{year}-spring", _nth_sunday(year, 3, 2), _last_sunday(year, 3)),
        _window(f"{year}-autumn", _last_sunday(year, 10), _nth_sunday(year, 11, 1)),
    )


def _window(label: str, transition: date, until: date) -> MismatchWindow:
    first_full = transition + timedelta(days=1)
    return MismatchWindow(
        label=label,
        transition=transition,
        first_full_session=first_full,
        last_session=until,
        transition_weeks=(week_key(transition),),
        fully_affected_weeks=_weeks_between(first_full, until),
    )


def audit_weekly_bars(
    opens: Sequence[datetime],
    *,
    zone: ZoneInfo,
    expected_per_week: Mapping[str, int] | None = None,
    session_length: timedelta = _NOMINAL_SESSION,
    min_interior_weeks: int = MIN_INTERIOR_WEEKS,
) -> WeeklyAudit:
    """Audit daily-bar *opens* against expected sessions per week.

    *expected_per_week* comes from SPEC 2.4's expected-liquidity calendar. Supply it
    and the audit can return ``PASSED`` or ``FAILED``; omit it and the best available
    outcome is ``INDETERMINATE``, because a four-bar week around Good Friday is
    correct and nothing here can tell that from a missing session.

    The first and last observed weeks are never judged: a sample almost always begins
    and ends mid-week.
    """
    if not opens:
        return WeeklyAudit(
            status=WeekAuditStatus.INSUFFICIENT_DATA,
            counts={},
            interior_weeks=(),
            missing_weeks=(),
            weeks_off_expected={},
            duplicate_opens=(),
            calendar_supplied=expected_per_week is not None,
            reason="no bars supplied",
        )

    seen: Counter[datetime] = Counter(opens)
    duplicates = tuple(sorted(moment.isoformat() for moment, count in seen.items() if count > 1))

    counts: Counter[str] = Counter()
    for moment in opens:
        counts[session_week_key(moment, zone=zone, session_length=session_length)] += 1

    observed = sorted(counts)
    span = expected_weeks(observed[0], observed[-1])
    interior = span[1:-1] if len(span) > 2 else ()

    if len(interior) < min_interior_weeks:
        return WeeklyAudit(
            status=WeekAuditStatus.INSUFFICIENT_DATA,
            counts=dict(counts),
            interior_weeks=interior,
            missing_weeks=(),
            weeks_off_expected={},
            duplicate_opens=duplicates,
            calendar_supplied=expected_per_week is not None,
            reason=(
                f"{len(interior)} interior week(s); at least {min_interior_weeks} "
                "are needed to judge"
            ),
        )

    missing = tuple(key for key in interior if counts[key] == 0)
    off: dict[str, int] = {}
    for key in interior:
        expected = (
            BARS_PER_FULL_WEEK
            if expected_per_week is None
            else expected_per_week.get(key, BARS_PER_FULL_WEEK)
        )
        if counts[key] != expected:
            off[key] = counts[key]

    if duplicates:
        status, reason = WeekAuditStatus.FAILED, "duplicate bar opens are never legitimate"
    elif expected_per_week is None:
        status = WeekAuditStatus.INDETERMINATE
        reason = (
            "no expected-liquidity calendar (SPEC 2.4); a holiday-shortened week is "
            "indistinguishable from a missing session"
        )
    elif missing or off:
        status, reason = WeekAuditStatus.FAILED, "week counts deviate from the calendar"
    else:
        status, reason = WeekAuditStatus.PASSED, "every interior week matched the calendar"

    return WeeklyAudit(
        status=status,
        counts=dict(counts),
        interior_weeks=interior,
        missing_weeks=missing,
        weeks_off_expected=off,
        duplicate_opens=duplicates,
        calendar_supplied=expected_per_week is not None,
        reason=reason,
    )
