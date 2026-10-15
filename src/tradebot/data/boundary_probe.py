"""Pure analysis for the broker trading-day boundary probe (ADR-0007).

Everything here is deliberately free of MetaTrader — it takes plain records and
returns plain results, so mypy and CI cover it. The Windows script keeps only the
IPC calls that cannot run anywhere else.

That split exists because of a specific failure: an earlier probe carried this logic
inside the script, the weekly-audit API it called was renamed underneath it, and the
break only surfaced on a live run against a broker terminal. Four references were
stale. None of it was reachable by the test suite, because the module cannot even be
imported without MetaTrader installed.

Three pieces live here.

**Window planning** sizes each bar request from the terminal's own bar cap, so a
structurally oversized request cannot be issued. A flat one-year M1 window is roughly
525,600 bars against a 100,000-bar limit, and that call hangs rather than failing.

**Response validation** checks that returned bars actually fall inside the window
that was asked for. A live run requested 2012 and received a single bar dated
2026-05-29, which the walk then recorded as a successful 2012 window — reading depth
that was not there. Out-of-range bars are counted and excluded.

**Anchor scoring** decides whether the reported bar epoch IS the session boundary, by
asking which candidate offset's first tick reproduces the bar's open price.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tradebot.data.session_weeks import WeeklyAudit, audit_weekly_bars

#: Candidate session-boundary offsets, in hours, tried against each bar.
CANDIDATE_OFFSETS_HOURS: tuple[int, ...] = (0, -1, -2, -3, 1, 2, 3)

#: Fraction of the terminal's bar cap a single request may ask for.
WINDOW_CAP_FRACTION = 0.3

#: Absolute tolerance when matching a tick's bid against a bar's open price.
PRICE_TOLERANCE = 1e-9

#: Fewest anchor samples that may produce a resolution at all.
MIN_ANCHOR_SAMPLES = 8

MAX_WALK_STEPS = 240

#: Hard calendar ceiling per request, by timeframe. The bar-count cap alone is not
#: enough: 30,000 M1 bars is still a request that can stick, and 30,000 D1 bars is an
#: ~82-year span. A request must satisfy BOTH bounds.
MAX_WINDOW_BY_BAR_MINUTES: Mapping[int, timedelta] = {
    1: timedelta(days=20),
    1440: timedelta(days=365),
}
DEFAULT_MAX_WINDOW = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class BarRecord:
    """One daily bar reduced to what the analysis needs."""

    open_instant: datetime
    open_price: float


@dataclass(frozen=True, slots=True)
class TickRecord:
    """One tick reduced to its identity and its bid."""

    time_msc: int
    bid: float


@dataclass(frozen=True, slots=True)
class WindowRequest:
    """A bar range to ask the terminal for, sized to stay under its cap."""

    start: datetime
    end: datetime
    expected_bars: int
    requested_span: timedelta
    planned_span: timedelta
    plan_truncated: bool

    @property
    def span_days(self) -> float:
        """Return the request span in days, for the artifact."""
        return round((self.end - self.start).total_seconds() / 86400, 2)

    @property
    def requested_span_days(self) -> float:
        """Return the complete reach the caller asked the walk to cover."""
        return round(self.requested_span.total_seconds() / 86400, 2)

    @property
    def planned_span_days(self) -> float:
        """Return the reach represented by every window the planner emitted."""
        return round(self.planned_span.total_seconds() / 86400, 2)


@dataclass(frozen=True, slots=True)
class WindowOutcome:
    """What one window request actually returned, after range validation."""

    request: WindowRequest
    bars_in_range: int
    bars_out_of_range: int
    earliest_in_range: datetime | None
    error: str | None

    @property
    def has_data(self) -> bool:
        """Return whether the window yielded any bar genuinely inside it."""
        return self.bars_in_range > 0


@dataclass(frozen=True, slots=True)
class BarReach:
    """How far back the terminal could actually show bars. A floor, not broker depth."""

    timeframe: str
    terminal_visible_earliest: datetime | None
    windows_walked: int
    total_out_of_range: int
    stopped_because: str
    requested_span_days: float
    planned_span_days: float
    walked_span_days: float
    plan_truncated: bool
    is_broker_depth: bool = False


@dataclass(frozen=True, slots=True)
class AnchorObservation:
    """A bar plus the first tick each candidate offset found for it."""

    bar: BarRecord
    first_ticks: Mapping[int, TickRecord]
    attempted_offsets: frozenset[int]
    failures: Mapping[int, str]

    @property
    def complete(self) -> bool:
        """Return whether every declared candidate produced its own query result."""
        expected = frozenset(CANDIDATE_OFFSETS_HOURS)
        return (
            self.attempted_offsets == expected
            and not self.failures
            and frozenset(self.first_ticks) == expected
        )

    @property
    def failure_reason(self) -> str | None:
        """Render incomplete candidate outcomes without hiding which offset failed."""
        if self.complete:
            return None
        missing_attempts = sorted(set(CANDIDATE_OFFSETS_HOURS) - self.attempted_offsets)
        missing_results = sorted(
            set(self.attempted_offsets) - set(self.first_ticks) - set(self.failures)
        )
        parts = [f"offset {offset:+d}: not attempted" for offset in missing_attempts]
        parts.extend(
            f"offset {offset:+d}: {reason}" for offset, reason in sorted(self.failures.items())
        )
        parts.extend(f"offset {offset:+d}: no recorded outcome" for offset in missing_results)
        return "; ".join(parts) or "candidate coverage is incomplete"


@dataclass(frozen=True, slots=True)
class AnchorVerdict:
    """Which candidate offset, if any, reproduces the bars' open prices."""

    resolved: bool
    offset_hours: int | None
    matches: Mapping[int, int]
    eligible: Mapping[int, int]
    tied_offsets: tuple[int, ...]
    shared_tick_discards: int
    observations: int
    samples: int
    incomplete_observations: int
    incomplete_reasons: tuple[str, ...]
    reason: str

    @property
    def epochs_are_true_utc(self) -> bool | None:
        """Return True when the reported epoch is itself the boundary, else None."""
        return None if not self.resolved else self.offset_hours == 0


@dataclass(frozen=True, slots=True)
class BoundarySummary:
    """Histograms and weekly audit for a run of daily-bar opens."""

    bars: int
    first_open: datetime | None
    last_open: datetime | None
    utc_open_histogram: Mapping[str, int]
    local_open_histogram: Mapping[str, int]
    boundary_is_1700_local: bool
    audit: WeeklyAudit

    @property
    def histogram_alone_is_ambiguous(self) -> bool:
        """Always true: a uniform 00:00 histogram fits two different worlds.

        A genuine UTC-midnight boundary and a non-zero-offset server whose local
        midnight is encoded as a UTC epoch produce identical timestamps, so only the
        price anchor can separate them.
        """
        return True


def plan_bar_windows(
    *,
    now: datetime,
    maxbars: int,
    bar_minutes: int,
    span: timedelta,
    cap_fraction: float = WINDOW_CAP_FRACTION,
    max_steps: int = MAX_WALK_STEPS,
    max_window: timedelta | None = None,
) -> tuple[WindowRequest, ...]:
    """Return windows walking back from *now*, bounded by BOTH bar count and span.

    Two independent ceilings, because either alone leaves a bad request possible. The
    bar-count cap keeps a request inside what the terminal will serve; the calendar
    ceiling keeps it inside what it will serve *promptly*. A 30,000-bar M1 request
    satisfies the first and can still stick, and 30,000 D1 bars is an ~82-year span
    that no walk should ever ask for.

    Requests are ordered most-recent first, because the walk stops at the first window
    with no data.
    """
    if maxbars < 1:
        raise ValueError("maxbars must be at least 1")
    if bar_minutes < 1:
        raise ValueError("bar_minutes must be at least 1")
    if not 0 < cap_fraction <= 1:
        raise ValueError("cap_fraction must be in (0, 1]")
    if span <= timedelta(0):
        raise ValueError("span must be positive")
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")

    # `is None`, not `or`: timedelta(0) is falsy, so an explicitly zero ceiling would
    # otherwise fall through to the default and be accepted silently.
    ceiling = (
        MAX_WINDOW_BY_BAR_MINUTES.get(bar_minutes, DEFAULT_MAX_WINDOW)
        if max_window is None
        else max_window
    )
    if ceiling <= timedelta(0):
        raise ValueError("max_window must be positive")

    by_count = timedelta(minutes=max(1, int(maxbars * cap_fraction)) * bar_minutes)
    window = min(by_count, ceiling)
    expected = max(1, int(window.total_seconds() / 60 / bar_minutes))
    needed = -(-int(span.total_seconds()) // int(window.total_seconds()))
    steps = min(max_steps, needed)
    planned_span = min(span, window * steps)
    truncated = planned_span < span
    return tuple(
        WindowRequest(
            start=max(now - span, now - window * (step + 1)),
            end=now - window * step,
            expected_bars=(
                expected
                if now - window * (step + 1) >= now - span
                else max(
                    1,
                    int(((now - window * step) - (now - span)).total_seconds() / 60 / bar_minutes),
                )
            ),
            requested_span=span,
            planned_span=planned_span,
            plan_truncated=truncated,
        )
        for step in range(steps)
    )


def validate_window_response(
    request: WindowRequest,
    opens: Sequence[datetime],
    *,
    error: str | None = None,
) -> WindowOutcome:
    """Judge one window's response, discarding bars that fall outside the request.

    A terminal may answer a range it cannot serve with whatever it does have — a live
    run asked for 2012 and got a single 2026 bar. Counting that as data would report
    depth that does not exist, so out-of-range bars are excluded from both the count
    and the earliest-bar result, and surfaced in their own field.
    """
    in_range = [moment for moment in opens if request.start <= moment < request.end]
    return WindowOutcome(
        request=request,
        bars_in_range=len(in_range),
        bars_out_of_range=len(opens) - len(in_range),
        earliest_in_range=min(in_range) if in_range else None,
        error=error,
    )


def summarise_bar_reach(timeframe: str, outcomes: Sequence[WindowOutcome]) -> BarReach:
    """Reduce a walk's outcomes to the earliest bar genuinely inside a request."""
    earliest: datetime | None = None
    for outcome in outcomes:
        if outcome.earliest_in_range is not None and (
            earliest is None or outcome.earliest_in_range < earliest
        ):
            earliest = outcome.earliest_in_range
    requested_span_days = outcomes[0].request.requested_span_days if outcomes else 0.0
    planned_span_days = outcomes[0].request.planned_span_days if outcomes else 0.0
    walked_span_days = (
        round((outcomes[0].request.end - outcomes[-1].request.start).total_seconds() / 86400, 2)
        if outcomes
        else 0.0
    )
    truncated = outcomes[0].request.plan_truncated if outcomes else False
    if not outcomes:
        stopped = "no windows planned"
    elif outcomes[-1].error is not None:
        stopped = f"request error: {outcomes[-1].error}"
    elif not outcomes[-1].has_data:
        stopped = (
            "window returned no in-range bars"
            if outcomes[-1].bars_out_of_range == 0
            else f"window returned only {outcomes[-1].bars_out_of_range} out-of-range bar(s)"
        )
    elif truncated:
        stopped = "walk reached the safety step cap before the requested span"
    else:
        stopped = "walk exhausted the planned windows"
    return BarReach(
        timeframe=timeframe,
        terminal_visible_earliest=earliest,
        windows_walked=len(outcomes),
        total_out_of_range=sum(outcome.bars_out_of_range for outcome in outcomes),
        stopped_because=stopped,
        requested_span_days=requested_span_days,
        planned_span_days=planned_span_days,
        walked_span_days=walked_span_days,
        plan_truncated=truncated,
    )


def score_anchor(observations: Sequence[AnchorObservation]) -> AnchorVerdict:
    """Return which candidate offset reproduces the bars' open prices, if one does.

    Two guards, both from a live false positive on index symbols.

    *A candidate only votes if its first tick is its own.* Forward tick reads mean
    that when the market is shut between two candidate instants, both resolve to the
    SAME tick — one observation counted twice, discriminating nothing. Any tick shared
    by more than one candidate for a bar is discarded for that bar.

    *A tie is never a resolution.* A live index run scored offsets 0 and +1 at 12/12
    each; picking the first by insertion order would have declared a boundary that the
    evidence does not support.
    """
    scores: Counter[int] = Counter()
    eligible: Counter[int] = Counter()
    discards = 0
    usable = 0
    incomplete = 0
    incomplete_reasons: list[str] = []
    for observation in observations:
        if not observation.complete:
            incomplete += 1
            if observation.failure_reason:
                incomplete_reasons.append(observation.failure_reason)
            continue
        seen = Counter(tick.time_msc for tick in observation.first_ticks.values())
        unique_ticks = {
            offset: tick
            for offset, tick in observation.first_ticks.items()
            if seen[tick.time_msc] == 1
        }
        discards += len(observation.first_ticks) - len(unique_ticks)
        if not unique_ticks:
            continue
        usable += 1
        for offset, tick in unique_ticks.items():
            # Eligibility is PER OFFSET: this observation tested this candidate, whether
            # or not it matched. Pooling it into one `usable` count lets a bar that
            # discriminated only OTHER offsets inflate the winner's denominator.
            eligible[offset] += 1
            if abs(tick.bid - observation.bar.open_price) <= PRICE_TOLERANCE:
                scores[offset] += 1

    observations_checked = len(observations)
    samples = usable
    if not scores:
        return AnchorVerdict(
            resolved=False,
            offset_hours=None,
            matches={},
            eligible=dict(eligible),
            tied_offsets=(),
            shared_tick_discards=discards,
            observations=observations_checked,
            samples=samples,
            incomplete_observations=incomplete,
            incomplete_reasons=tuple(incomplete_reasons),
            reason="no candidate offset reproduced a bar open price",
        )

    ranked = scores.most_common()
    top = ranked[0][1]
    tied = tuple(sorted(offset for offset, hits in ranked if hits == top))
    if len(tied) > 1:
        return AnchorVerdict(
            resolved=False,
            offset_hours=None,
            matches=dict(scores),
            eligible=dict(eligible),
            tied_offsets=tied,
            shared_tick_discards=discards,
            observations=observations_checked,
            samples=samples,
            incomplete_observations=incomplete,
            incomplete_reasons=tuple(incomplete_reasons),
            reason=(
                f"offsets {list(tied)} tied at {top}; a tie means the candidates are "
                "indistinguishable, not that the first one is right"
            ),
        )

    winner = tied[0]
    # Both gates are measured against the WINNER'S OWN tested count, never the pooled
    # usable count. An observation where the winner's tick collapsed onto another
    # candidate tested every OTHER offset but told us nothing about this one, so
    # counting it here would credit the winner with evidence that does not exist. That
    # is how a 7-of-7 result presented itself as 7/12 and cleared a floor of 8.
    #
    # `tested - top` is therefore real contrary evidence: the winner had a clean shot
    # and missed. A strict majority of those clean shots is the bar; the live 10/12
    # and 12/12 FX results still clear it.
    tested = eligible[winner]
    enough = tested >= MIN_ANCHOR_SAMPLES
    majority = top * 2 > tested
    resolved = enough and majority
    if not enough:
        reason = (
            f"offset {winner} was tested in only {tested} observation(s); at least "
            f"{MIN_ANCHOR_SAMPLES} are needed, and {usable} usable bar(s) do not supply "
            "them when the candidate's own tick was shared elsewhere"
        )
    elif not majority:
        reason = (
            f"offset {winner} matched {top} of the {tested} observation(s) that tested "
            "it, which is not a strict majority"
        )
    else:
        reason = (
            f"offset {winner} uniquely reproduced {top} of the {tested} bar open(s) that tested it"
        )
    return AnchorVerdict(
        resolved=resolved,
        offset_hours=winner if resolved else None,
        matches=dict(scores),
        eligible=dict(eligible),
        tied_offsets=(),
        shared_tick_discards=discards,
        observations=observations_checked,
        samples=samples,
        incomplete_observations=incomplete,
        incomplete_reasons=tuple(incomplete_reasons),
        reason=reason,
    )


def summarise_boundary(
    opens: Sequence[datetime],
    *,
    zone: ZoneInfo,
    expected_per_week: Mapping[str, int] | None = None,
) -> BoundarySummary:
    """Build the histograms and weekly audit for a run of daily-bar opens."""
    if not opens:
        return BoundarySummary(
            bars=0,
            first_open=None,
            last_open=None,
            utc_open_histogram={},
            local_open_histogram={},
            boundary_is_1700_local=False,
            audit=audit_weekly_bars([], zone=zone, expected_per_week=expected_per_week),
        )
    ordered = sorted(opens)
    utc_hist = Counter(moment.strftime("%H:%M") for moment in ordered)
    local_hist = Counter(moment.astimezone(zone).strftime("%H:%M") for moment in ordered)
    return BoundarySummary(
        bars=len(ordered),
        first_open=ordered[0],
        last_open=ordered[-1],
        utc_open_histogram=dict(sorted(utc_hist.items())),
        local_open_histogram=dict(sorted(local_hist.items())),
        boundary_is_1700_local=set(local_hist) == {"17:00"},
        audit=audit_weekly_bars(ordered, zone=zone, expected_per_week=expected_per_week),
    )
