"""Session-week auditing for the SPEC 4.4 check-10(c) alignment fingerprint.

Check 10(c) requires a full trading week to contain exactly five daily bars,
"evaluated SPECIFICALLY inside the US/EU DST mismatch windows" — because a server
following EU dates is aligned with a 17:00 New York boundary for roughly 48 weeks a
year and emits its one-hour stub only in those windows, so a randomly sampled week
passes 48 times in 52.

Two details make the difference between a working check and a noisy one.

**Weeks are keyed by the session's CLOSE, not its open** (SPEC 3.4's labelling rule).
A bar opening Sunday 22:00 UTC belongs to a trading week that Sunday *ends* in ISO
terms; keying on the open would split every week and flag all of them.

**Absent weeks are enumerated, not merely counted.** A week with no bars at all has
no key to iterate, so an audit built only from observed keys cannot see a whole
missing week — the most serious gap it is supposed to find.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

BARS_PER_FULL_WEEK = 5
_NOMINAL_SESSION = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class MismatchWindow:
    """A span where US and EU daylight time disagree, in close-labelled weeks."""

    label: str
    first_session: date
    last_session: date
    weeks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeeklyAudit:
    """The outcome of auditing daily-bar opens against the five-per-week rule."""

    counts: Mapping[str, int]
    interior_weeks: tuple[str, ...]
    missing_weeks: tuple[str, ...]
    weeks_not_five: Mapping[str, int]
    duplicate_opens: tuple[str, ...]

    @property
    def clean(self) -> bool:
        """Return whether every interior week held exactly five distinct bars."""
        return not self.missing_weeks and not self.weeks_not_five and not self.duplicate_opens


def _nth_sunday(year: int, month: int, nth: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))


def _last_sunday(year: int, month: int) -> date:
    following = date(year + (month == 12), (month % 12) + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def week_key(moment: datetime) -> str:
    """Return the ISO week key of *moment*, e.g. ``2024-W43``.

    Uses the ISO year, not the calendar year, so 2024-12-30 keys as ``2025-W01``.
    """
    iso = moment.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def session_week_key(
    open_instant: datetime,
    *,
    zone: ZoneInfo,
    session_length: timedelta = _NOMINAL_SESSION,
) -> str:
    """Return the close-labelled ISO week key for a session opening at *open_instant*.

    The close is approximated as ``open + session_length``, which is safe for week
    grouping: a 23- or 25-hour DST session cannot move a close across a week
    boundary. A short stub session attaches to the ISO week its own open date ends,
    which is what surfaces it as a sixth bar rather than hiding it.
    """
    return week_key((open_instant + session_length).astimezone(zone))


def expected_weeks(first: str, last: str) -> tuple[str, ...]:
    """Return every ISO week key from *first* to *last* inclusive, gaps included.

    Walks Mondays via :meth:`date.fromisocalendar`, so 53-week years and ISO-year
    rollovers are handled by the calendar rather than by arithmetic on the label.
    """
    monday = _monday_of(first)
    end = _monday_of(last)
    keys: list[str] = []
    while monday <= end:
        iso = monday.isocalendar()
        keys.append(f"{iso[0]}-W{iso[1]:02d}")
        monday += timedelta(weeks=1)
    return tuple(keys)


def _monday_of(key: str) -> date:
    iso_year, iso_week = key.split("-W")
    return date.fromisocalendar(int(iso_year), int(iso_week), 1)


def dst_mismatch_windows(year: int) -> tuple[MismatchWindow, ...]:
    """Return the two spans where US and EU daylight time disagree, post-2007 rules.

    Each window starts on the **Monday after** the transition Sunday. The transition
    Sunday is ISO weekday 7, so it closes the week *before* the affected sessions;
    starting from its own week would pull in one unaffected week and dilute the check.
    """
    us_spring = _nth_sunday(year, 3, 2)
    eu_spring = _last_sunday(year, 3)
    eu_autumn = _last_sunday(year, 10)
    us_autumn = _nth_sunday(year, 11, 1)
    return (
        _window(f"{year}-spring", us_spring, eu_spring),
        _window(f"{year}-autumn", eu_autumn, us_autumn),
    )


def _window(label: str, transition: date, until: date) -> MismatchWindow:
    first = transition + timedelta(days=1)
    last = until
    keys: list[str] = []
    cursor = first
    while cursor <= last:
        key = week_key(datetime(cursor.year, cursor.month, cursor.day))
        if key not in keys:
            keys.append(key)
        cursor += timedelta(days=1)
    return MismatchWindow(label=label, first_session=first, last_session=last, weeks=tuple(keys))


def audit_weekly_bars(
    opens: Sequence[datetime],
    *,
    zone: ZoneInfo,
    session_length: timedelta = _NOMINAL_SESSION,
) -> WeeklyAudit:
    """Audit daily-bar *opens* against the five-bars-per-full-week rule.

    The first and last observed weeks are excluded from judgement, because a sample
    almost always begins and ends mid-week. Everything between them is interior and
    must hold exactly five distinct sessions.
    """
    if not opens:
        return WeeklyAudit(
            counts={},
            interior_weeks=(),
            missing_weeks=(),
            weeks_not_five={},
            duplicate_opens=(),
        )

    seen: Counter[datetime] = Counter(opens)
    duplicates = tuple(sorted(moment.isoformat() for moment, count in seen.items() if count > 1))

    counts: Counter[str] = Counter()
    for moment in opens:
        counts[session_week_key(moment, zone=zone, session_length=session_length)] += 1

    observed = sorted(counts)
    span = expected_weeks(observed[0], observed[-1])
    interior = span[1:-1] if len(span) > 2 else ()
    missing = tuple(key for key in interior if counts[key] == 0)
    not_five = {key: counts[key] for key in interior if counts[key] != BARS_PER_FULL_WEEK}
    return WeeklyAudit(
        counts=dict(counts),
        interior_weeks=interior,
        missing_weeks=missing,
        weeks_not_five=not_five,
        duplicate_opens=duplicates,
    )
