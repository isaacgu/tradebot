"""Session-week auditing for the SPEC 4.4 check-10(c) alignment fingerprint.

Check 10(c) requires a full trading week to contain exactly five daily bars,
"evaluated SPECIFICALLY inside the US/EU DST mismatch windows" — a server following
EU dates agrees with a 17:00 New York boundary for roughly 48 weeks a year and emits
its stub only there, so a randomly sampled week passes 48 times in 52.

Five things separate a working check from a noisy or dishonest one.

**Weeks are keyed by the session's real CLOSE, not by a nominal one.** SPEC 3.4 labels
a daily bar by its close, and a stub session is exactly where an approximation breaks:
a Sunday stub opening 21:00 UTC truly closes at Monday 00:00 UTC, which keys to the
Sunday's own ISO week, whereas ``open + 24h`` would key it to the following week and
hide the sixth bar. The next open supplies an exact close only while sessions are
contiguous. Across a closure the close is honestly represented as a 23--25 hour
interval; it is keyed only when the whole interval proves the same ISO week.

**Absent weeks are enumerated, not merely counted.** A week with no bars has no key to
iterate, so an audit built from observed keys alone cannot see a wholly missing week.

**A mismatch window has two distinct week sets.** The fully affected sessions begin the
Monday after the transition Sunday, but that Sunday closes the *preceding* week, which
is where the stub lands. Auditing only one set misses half the evidence.

**Excess is structural and unconditional.** A close-labelled ISO week holds at most
five weekday sessions, so a sixth bar is a stub — no calendar, sample size or coverage
gate can legalise it. That check therefore runs before every other consideration.

**Nothing passes on absence.** A holiday-shortened week legitimately holds four, and
SPEC 2.4 puts that knowledge in the expected-liquidity calendar. Without it a shortfall
is indistinguishable from a dropped session, so the verdict is ``INDETERMINATE``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from tradebot.core.timestamps import require_utc

BARS_PER_FULL_WEEK = 5
MIN_INTERIOR_WEEKS = 4

#: Daily bars may be shortened or widened by one hour at a DST transition. Together
#: with :data:`MAX_SESSION`, this bounds an unknown close without inventing one.
MIN_SESSION = timedelta(hours=23)

#: Longest a daily session may legitimately run: 24 h plus a DST hour. A "next open"
#: further away than this means the previous session ended at its nominal length and a
#: market closure intervened, not that the session lasted the whole weekend.
MAX_SESSION = timedelta(hours=25)


class WeekAuditStatus(StrEnum):
    """Outcome of a weekly-bar audit. Only ``PASSED`` asserts correctness."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INDETERMINATE = "INDETERMINATE"
    PASSED = "PASSED"
    FAILED = "FAILED"


class FingerprintStatus(StrEnum):
    """Outcome of the SPEC 4.4 check-10(c) DST alignment fingerprint.

    ``ALIGNED`` is the only gate-grade pass and demands a complete spring window, a
    complete autumn window, and calendar coverage. ``PROVISIONALLY_ALIGNED`` says the
    evidence seen is consistent with alignment but does not meet that bar.
    """

    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    INDETERMINATE = "INDETERMINATE"
    PROVISIONALLY_ALIGNED = "PROVISIONALLY_ALIGNED"
    ALIGNED = "ALIGNED"
    MISALIGNED = "MISALIGNED"


@dataclass(frozen=True, slots=True)
class MismatchWindow:
    """A span where US and EU daylight time disagree, in close-labelled ISO weeks."""

    label: str
    season: str
    transition: date
    first_full_session: date
    last_session: date
    transition_weeks: tuple[str, ...]
    fully_affected_weeks: tuple[str, ...]

    @property
    def audit_weeks(self) -> tuple[str, ...]:
        """Every week the fingerprint inspects: the stub week plus the affected weeks."""
        merged = list(self.transition_weeks)
        merged.extend(week for week in self.fully_affected_weeks if week not in merged)
        return tuple(merged)


@dataclass(frozen=True, slots=True)
class SessionClose:
    """Evidence for one session close.

    ``closed_at`` is populated only when the close is known exactly. A market-closure
    gap instead carries the honest inclusive interval ``earliest_close`` through
    ``latest_close``. :meth:`week_key` returns a key only when both ends prove the same
    close-labelled ISO week; ``None`` exposes a genuinely ambiguous assignment.
    """

    opened_at: datetime
    closed_at: datetime | None
    earliest_close: datetime
    latest_close: datetime

    def week_key(self, *, zone: ZoneInfo) -> str | None:
        """Return the proven close-labelled week, or ``None`` if it is ambiguous."""
        first = session_week_key(self.earliest_close, zone=zone)
        last = session_week_key(self.latest_close, zone=zone)
        return first if first == last else None


@dataclass(frozen=True, slots=True)
class WeeklyAudit:
    """The outcome of auditing daily-bar sessions against expected counts per week."""

    status: WeekAuditStatus
    counts: Mapping[str, int]
    interior_weeks: tuple[str, ...]
    missing_weeks: tuple[str, ...]
    structural_excess: Mapping[str, int]
    weeks_excess: Mapping[str, int]
    weeks_shortfall: Mapping[str, int]
    duplicate_opens: tuple[str, ...]
    ambiguous_closes: tuple[str, ...]
    ambiguous_weeks: tuple[str, ...]
    uncovered_weeks: tuple[str, ...]
    calendar_supplied: bool
    reason: str

    @property
    def weeks_off_expected(self) -> Mapping[str, int]:
        """Return every judged week whose count deviated, in either direction."""
        return {**self.weeks_shortfall, **self.weeks_excess, **self.structural_excess}

    @property
    def anomalies(self) -> bool:
        """Return whether anything at all deviated, regardless of status."""
        return bool(self.weeks_off_expected or self.duplicate_opens or self.ambiguous_closes)


@dataclass(frozen=True, slots=True)
class DstFingerprint:
    """The check-10(c) verdict: does the series align with our boundary in the windows?"""

    status: FingerprintStatus
    covered_windows: tuple[str, ...]
    partially_covered_windows: tuple[str, ...]
    seasons_covered: tuple[str, ...]
    window_counts: Mapping[str, Mapping[str, int]]
    anomalous_weeks: Mapping[str, int]
    reason: str


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


def session_closes(
    opens: Sequence[datetime], *, final_close: datetime | None = None
) -> tuple[SessionClose, ...]:
    """Return exact or bounded close evidence for each closable session.

    Every timestamp must be timezone-aware UTC; a naive one is rejected rather than
    silently assumed. The next open is the exact close only when it is no more than
    :data:`MAX_SESSION` away. A longer gap proves only that the close lies between
    :data:`MIN_SESSION` and :data:`MAX_SESSION` after the open, so that interval is
    retained instead of fabricating ``open + 25h``.

    The final session is dropped unless *final_close* is supplied, because its close is
    genuinely unknown. An explicit final close must be strictly after the final open
    and no more than :data:`MAX_SESSION` later.
    """
    checked = [require_utc(moment, field="session open") for moment in opens]
    ordered = sorted(set(checked))
    checked_final = (
        require_utc(final_close, field="final_close") if final_close is not None else None
    )
    closes: list[SessionClose] = []
    for index, opened in enumerate(ordered):
        if index + 1 < len(ordered):
            following = ordered[index + 1]
            if following - opened <= MAX_SESSION:
                closes.append(SessionClose(opened, following, following, following))
            else:
                closes.append(
                    SessionClose(
                        opened,
                        None,
                        opened + MIN_SESSION,
                        opened + MAX_SESSION,
                    )
                )
        elif checked_final is not None:
            elapsed = checked_final - opened
            if elapsed <= timedelta(0) or elapsed > MAX_SESSION:
                raise ValueError(
                    "final_close must be strictly after the final session open and "
                    f"no more than {MAX_SESSION} later"
                )
            closes.append(SessionClose(opened, checked_final, checked_final, checked_final))
        else:
            continue
    return tuple(closes)


def session_week_key(close_instant: datetime, *, zone: ZoneInfo) -> str:
    """Return the ISO week key a session belongs to, given its real close."""
    return week_key(require_utc(close_instant, field="session close").astimezone(zone))


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
    ``transition_weeks`` is that preceding week — where a stub bar actually appears.
    """
    return (
        _window("spring", year, _nth_sunday(year, 3, 2), _last_sunday(year, 3)),
        _window("autumn", year, _last_sunday(year, 10), _nth_sunday(year, 11, 1)),
    )


def _window(season: str, year: int, transition: date, until: date) -> MismatchWindow:
    first_full = transition + timedelta(days=1)
    return MismatchWindow(
        label=f"{year}-{season}",
        season=season,
        transition=transition,
        first_full_session=first_full,
        last_session=until,
        transition_weeks=(week_key(transition),),
        fully_affected_weeks=_weeks_between(first_full, until),
    )


def _validate_calendar(expected: Mapping[str, int]) -> None:
    """Reject a calendar that could legalise a structurally impossible week.

    A close-labelled ISO week holds at most five weekday sessions, so an expectation of
    six would let a stub bar through as "expected". That is a configuration error, not
    a data condition, so it raises rather than returning a status.
    """
    for key, value in expected.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"calendar value for {key} must be an int, got {value!r}")
        if not 0 <= value <= BARS_PER_FULL_WEEK:
            raise ValueError(
                f"calendar value for {key} must be within 0..{BARS_PER_FULL_WEEK}, got {value}"
            )


def audit_weekly_bars(
    opens: Sequence[datetime],
    *,
    zone: ZoneInfo,
    expected_per_week: Mapping[str, int] | None = None,
    final_close: datetime | None = None,
    min_interior_weeks: int = MIN_INTERIOR_WEEKS,
) -> WeeklyAudit:
    """Audit daily-bar sessions against expected counts per week.

    Evidence collection and verdict precedence are deliberately separate. Every
    count, duplicate, excess, shortfall, uncovered week and ambiguous close is
    collected before a status is chosen. A high-priority defect therefore cannot hide
    softer evidence that the DST fingerprint still needs.
    """
    calendar_supplied = expected_per_week is not None
    if expected_per_week is not None:
        _validate_calendar(expected_per_week)

    def _result(
        status: WeekAuditStatus,
        reason: str,
        *,
        counts: Mapping[str, int],
        interior: tuple[str, ...] = (),
        missing: tuple[str, ...] = (),
        structural: Mapping[str, int] | None = None,
        excess: Mapping[str, int] | None = None,
        shortfall: Mapping[str, int] | None = None,
        duplicates: tuple[str, ...] = (),
        ambiguous: tuple[str, ...] = (),
        ambiguous_weeks: tuple[str, ...] = (),
        uncovered: tuple[str, ...] = (),
    ) -> WeeklyAudit:
        return WeeklyAudit(
            status=status,
            counts=dict(counts),
            interior_weeks=interior,
            missing_weeks=missing,
            structural_excess=dict(structural or {}),
            weeks_excess=dict(excess or {}),
            weeks_shortfall=dict(shortfall or {}),
            duplicate_opens=duplicates,
            ambiguous_closes=ambiguous,
            ambiguous_weeks=ambiguous_weeks,
            uncovered_weeks=uncovered,
            calendar_supplied=calendar_supplied,
            reason=reason,
        )

    if not opens:
        return _result(WeekAuditStatus.INSUFFICIENT_DATA, "no bars supplied", counts={})

    seen: Counter[datetime] = Counter(require_utc(moment, field="session open") for moment in opens)
    duplicates = tuple(sorted(m.isoformat() for m, count in seen.items() if count > 1))

    counts: Counter[str] = Counter()
    candidate_weeks: set[str] = set()
    ambiguous: list[str] = []
    ambiguous_week_keys: set[str] = set()
    for closure in session_closes(tuple(seen), final_close=final_close):
        first = session_week_key(closure.earliest_close, zone=zone)
        last = session_week_key(closure.latest_close, zone=zone)
        candidate_weeks.update((first, last))
        proven = closure.week_key(zone=zone)
        if proven is None:
            ambiguous.append(closure.opened_at.isoformat())
            ambiguous_week_keys.update((first, last))
        else:
            counts[proven] += 1

    if not candidate_weeks:
        return _result(
            WeekAuditStatus.INSUFFICIENT_DATA,
            "no session could be closed; supply final_close or more than one bar",
            counts={},
            duplicates=duplicates,
        )

    observed = sorted(candidate_weeks)
    span = expected_weeks(observed[0], observed[-1])
    interior = span[1:-1] if len(span) > 2 else ()
    structural = {key: value for key, value in counts.items() if value > BARS_PER_FULL_WEEK}
    missing = tuple(key for key in interior if counts[key] == 0)
    uncovered = (
        tuple(key for key in interior if key not in expected_per_week)
        if expected_per_week is not None
        else ()
    )
    excess: dict[str, int] = {}
    shortfall: dict[str, int] = {}
    for key in interior:
        if expected_per_week is not None and key not in expected_per_week:
            continue
        expected = BARS_PER_FULL_WEEK if expected_per_week is None else expected_per_week[key]
        if counts[key] > expected:
            excess[key] = counts[key]
        elif counts[key] < expected:
            shortfall[key] = counts[key]

    # Status precedence is applied only after every evidence field above is complete.
    status = WeekAuditStatus.PASSED
    reason = "every interior week matched the calendar"
    if duplicates:
        status = WeekAuditStatus.FAILED
        reason = (
            "duplicate bar opens are never legitimate, at any sample size; week counts "
            "cannot be interpreted until they are removed"
        )
    elif structural:
        status = WeekAuditStatus.FAILED
        reason = (
            f"{len(structural)} week(s) hold more than {BARS_PER_FULL_WEEK} sessions; a "
            "close-labelled week cannot, so this is a stub bar and no calendar or "
            "sample size can legalise it"
        )
    elif ambiguous:
        status = WeekAuditStatus.INSUFFICIENT_DATA
        reason = (
            f"{len(ambiguous)} session close(s) cross an ISO-week boundary within the "
            f"{MIN_SESSION}..{MAX_SESSION} close interval and cannot be keyed honestly"
        )
    elif len(interior) < min_interior_weeks:
        status = WeekAuditStatus.INSUFFICIENT_DATA
        reason = f"{len(interior)} interior week(s); at least {min_interior_weeks} are needed"
    elif uncovered:
        status = WeekAuditStatus.INSUFFICIENT_DATA
        reason = (
            f"the calendar does not cover {len(uncovered)} interior week(s); an "
            "uncovered week is not assumed to hold five bars"
        )
    elif excess:
        status = WeekAuditStatus.FAILED
        reason = f"more bars than the calendar expects in {len(excess)} week(s)"
    elif expected_per_week is None:
        status = WeekAuditStatus.INDETERMINATE
        reason = (
            "no expected-liquidity calendar (SPEC 2.4); a holiday-shortened week is "
            "indistinguishable from a dropped session"
        )
    elif shortfall:
        status = WeekAuditStatus.FAILED
        reason = f"{len(shortfall)} week(s) hold fewer bars than the calendar expects"

    return _result(
        status,
        reason,
        counts=counts,
        interior=interior,
        missing=missing,
        structural=structural,
        excess=excess,
        shortfall=shortfall,
        duplicates=duplicates,
        ambiguous=tuple(ambiguous),
        ambiguous_weeks=tuple(sorted(ambiguous_week_keys)),
        uncovered=uncovered,
    )


def fingerprint_dst_alignment(
    opens: Sequence[datetime],
    *,
    zone: ZoneInfo,
    expected_per_week: Mapping[str, int] | None = None,
    final_close: datetime | None = None,
) -> DstFingerprint:
    """Return the SPEC 4.4 check-10(c) verdict for *opens*.

    Deliberately NOT :func:`audit_weekly_bars`: the generic audit can be satisfied by
    any four quiet January weeks, which says nothing about DST alignment.

    Three invariants govern the verdict.

    **Evidence of misalignment needs no coverage.** A known excess in ANY week that a
    mismatch window touches — fully covered or not — is a stub, and a stub is the thing
    10(c) looks for. Full coverage gates only the *clean* verdict.

    **Evidence collection survives audit failure.** A structural defect elsewhere in
    the sample must not erase a calendar-confirmed deviation inside a mismatch window.

    **A clean verdict is gate-grade only with a passed audit, both seasons and a
    calendar.** Spring and
    autumn shift in opposite directions, so one season cannot speak for the other, and
    without the expected-liquidity calendar a shortfall cannot be told from a holiday.
    Anything short of that is ``PROVISIONALLY_ALIGNED``.
    """
    audit = audit_weekly_bars(
        opens, zone=zone, expected_per_week=expected_per_week, final_close=final_close
    )
    interior = set(audit.interior_weeks)
    off_expected = audit.weeks_off_expected
    known_excess = {**audit.structural_excess, **audit.weeks_excess}
    confirmed_deviation = dict(known_excess)
    if expected_per_week is not None:
        confirmed_deviation.update(
            {
                week: count
                for week, count in audit.weeks_shortfall.items()
                if week not in audit.ambiguous_weeks
            }
        )

    covered: list[str] = []
    partial: list[str] = []
    seasons: set[str] = set()
    window_counts: dict[str, Mapping[str, int]] = {}
    anomalous: dict[str, int] = {}
    confirmed_in_window: dict[str, int] = {}

    for year in sorted({moment.year for moment in opens}):
        for window in dst_mismatch_windows(year):
            weeks = window.audit_weeks
            touched = [week for week in weeks if week in audit.counts or week in interior]
            if not touched:
                continue
            window_counts[window.label] = {week: audit.counts.get(week, 0) for week in weeks}
            # Misalignment evidence is collected from every touched week, covered or not.
            confirmed_in_window.update(
                {week: confirmed_deviation[week] for week in weeks if week in confirmed_deviation}
            )
            anomalous.update(
                {week: audit.counts.get(week, 0) for week in weeks if week in off_expected}
            )
            if all(week in interior for week in weeks):
                covered.append(window.label)
                seasons.add(window.season)
            else:
                partial.append(window.label)

    both_seasons = {"spring", "autumn"} <= seasons
    result = (
        FingerprintStatus.INSUFFICIENT_COVERAGE,
        "no DST mismatch window is fully covered and no stub was seen in one",
    )
    if confirmed_in_window:
        result = (
            FingerprintStatus.MISALIGNED,
            f"{len(confirmed_in_window)} mismatch-window week(s) deviate from the "
            "calendar or hold a structural stub bar; coverage is not required to believe "
            "evidence this direct",
        )
    elif audit.duplicate_opens:
        # Counts are built from unique opens, so a duplicate cannot manufacture the
        # direct evidence handled above. By itself, however, it poisons a clean verdict.
        result = (
            FingerprintStatus.INDETERMINATE,
            "the weekly audit failed on duplicate opens; resolve that before judging 10(c)",
        )
    elif audit.ambiguous_closes:
        result = (
            FingerprintStatus.INDETERMINATE,
            "one or more session-close intervals cross an ISO-week boundary; 10(c) "
            "cannot treat an unknown week assignment as clean evidence",
        )
    elif audit.status is WeekAuditStatus.FAILED:
        result = (
            FingerprintStatus.INDETERMINATE,
            "the weekly audit failed outside the mismatch windows; a failed sample "
            "cannot produce a clean alignment fingerprint",
        )
    elif audit.status is WeekAuditStatus.INSUFFICIENT_DATA:
        result = (
            FingerprintStatus.INSUFFICIENT_COVERAGE,
            f"the weekly audit could not judge the sample: {audit.reason}",
        )
    elif not covered:
        result = (
            FingerprintStatus.INSUFFICIENT_COVERAGE,
            "no DST mismatch window is fully covered by the judged interior weeks; "
            "10(c) cannot be evaluated on evidence that never spans one",
        )
    elif anomalous and expected_per_week is None:
        result = (
            FingerprintStatus.INDETERMINATE,
            "a covered window is short of five bars, which a holiday could explain; "
            "supply the expected-liquidity calendar to decide",
        )
    elif anomalous:
        result = (
            FingerprintStatus.MISALIGNED,
            "a covered mismatch window deviates from the calendar",
        )
    elif audit.status is WeekAuditStatus.PASSED and both_seasons and expected_per_week is not None:
        result = (
            FingerprintStatus.ALIGNED,
            f"{len(covered)} window(s) covering both seasons matched the calendar",
        )
    else:
        missing_parts = []
        if not both_seasons:
            missing_parts.append(
                f"only the {'/'.join(sorted(seasons))} season is covered; "
                "spring and autumn shift in opposite directions"
            )
        if expected_per_week is None:
            missing_parts.append("no expected-liquidity calendar")
        result = (
            FingerprintStatus.PROVISIONALLY_ALIGNED,
            "consistent with alignment but not gate-grade: " + "; ".join(missing_parts),
        )

    return DstFingerprint(
        status=result[0],
        covered_windows=tuple(covered),
        partially_covered_windows=tuple(partial),
        seasons_covered=tuple(sorted(seasons)),
        window_counts=window_counts,
        anomalous_weeks={**anomalous, **confirmed_in_window},
        reason=result[1],
    )
