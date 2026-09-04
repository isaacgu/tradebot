from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradebot.core.time_rules import NEW_YORK
from tradebot.data.boundary_probe import (
    CANDIDATE_OFFSETS_HOURS,
    MIN_ANCHOR_SAMPLES,
    AnchorObservation,
    BarRecord,
    TickRecord,
    plan_bar_windows,
    score_anchor,
    summarise_bar_reach,
    summarise_boundary,
    validate_window_response,
)

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def _bar(day: int, price: float = 1.29) -> BarRecord:
    return BarRecord(open_instant=datetime(2026, 8, day, 0, tzinfo=UTC), open_price=price)


def _observation(bar: BarRecord, ticks: dict[int, tuple[int, float]]) -> AnchorObservation:
    complete_ticks = dict(ticks)
    for index, offset in enumerate(CANDIDATE_OFFSETS_HOURS):
        complete_ticks.setdefault(
            offset,
            (10_000_000 + bar.open_instant.day * 100 + index, 9.99),
        )
    return AnchorObservation(
        bar=bar,
        first_ticks={
            offset: TickRecord(time_msc=msc, bid=bid)
            for offset, (msc, bid) in complete_ticks.items()
        },
        attempted_offsets=frozenset(CANDIDATE_OFFSETS_HOURS),
        failures={},
    )


# --- window planning -------------------------------------------------------------


def test_m1_windows_obey_the_calendar_ceiling_not_only_the_bar_cap() -> None:
    """Both bounds matter: 30,000 M1 bars satisfies the cap and can still stick."""
    windows = plan_bar_windows(
        now=NOW, maxbars=100_000, bar_minutes=1, span=timedelta(days=365 * 20)
    )

    assert windows
    for window in windows:
        assert window.span_days <= 20.0, "the 20-day M1 ceiling binds before the cap"
        assert window.expected_bars == 20 * 1440
    assert windows[0].span_days == pytest.approx(20.0, abs=0.01)


def test_a_daily_window_is_capped_by_span_not_by_an_82_year_bar_count() -> None:
    """30,000 D1 bars is an ~82-year request; the calendar ceiling prevents it."""
    windows = plan_bar_windows(
        now=NOW, maxbars=100_000, bar_minutes=1440, span=timedelta(days=365 * 20)
    )

    assert windows[0].span_days <= 365.0
    assert len(windows) > 1, "a real walk, not one absurd request"


def test_a_smaller_bar_cap_binds_before_the_calendar_ceiling() -> None:
    windows = plan_bar_windows(now=NOW, maxbars=100, bar_minutes=1, span=timedelta(days=30))

    assert windows[0].expected_bars == 30, "100 * 0.3"
    assert windows[0].span_days < 1.0


def test_an_explicit_max_window_overrides_the_timeframe_default() -> None:
    windows = plan_bar_windows(
        now=NOW,
        maxbars=100_000,
        bar_minutes=1,
        span=timedelta(days=30),
        max_window=timedelta(days=2),
    )

    assert windows[0].span_days == pytest.approx(2.0)


def test_a_nonpositive_max_window_is_refused() -> None:
    with pytest.raises(ValueError, match="max_window"):
        plan_bar_windows(
            now=NOW,
            maxbars=100,
            bar_minutes=1,
            span=timedelta(days=1),
            max_window=timedelta(0),
        )


def test_windows_walk_backwards_from_now_without_gaps_or_overlap() -> None:
    windows = plan_bar_windows(now=NOW, maxbars=10, bar_minutes=1440, span=timedelta(days=30))

    assert len(windows) == 10, "3-day windows across a 30-day span"
    assert windows[0].end == NOW
    for earlier, later in zip(windows[1:], windows[:-1], strict=True):
        assert earlier.end == later.start


def test_a_daily_timeframe_needs_far_fewer_windows_than_a_minute_one() -> None:
    span = timedelta(days=365 * 5)
    m1 = plan_bar_windows(now=NOW, maxbars=100_000, bar_minutes=1, span=span)
    d1 = plan_bar_windows(now=NOW, maxbars=100_000, bar_minutes=1440, span=span)

    assert len(d1) < len(m1)


def test_the_walk_is_bounded_even_for_an_absurd_span() -> None:
    windows = plan_bar_windows(
        now=NOW, maxbars=100, bar_minutes=1, span=timedelta(days=365 * 100), max_steps=12
    )

    assert len(windows) == 12
    assert windows[0].plan_truncated
    assert windows[0].planned_span < windows[0].requested_span


def test_m1_twenty_year_walk_discloses_the_step_cap_truncation() -> None:
    span = timedelta(days=365 * 20)
    windows = plan_bar_windows(now=NOW, maxbars=100_000, bar_minutes=1, span=span)

    assert len(windows) == 240
    assert windows[0].requested_span_days == pytest.approx(7300.0)
    assert windows[0].planned_span_days == pytest.approx(4800.0)
    assert windows[0].plan_truncated


@pytest.mark.parametrize(
    ("maxbars", "bar_minutes", "cap_fraction"),
    [(0, 1, 0.3), (100, 0, 0.3), (100, 1, 0.0), (100, 1, 1.5)],
)
def test_nonsense_planning_inputs_are_refused(
    maxbars: int, bar_minutes: int, cap_fraction: float
) -> None:
    with pytest.raises(ValueError):
        plan_bar_windows(
            now=NOW,
            maxbars=maxbars,
            bar_minutes=bar_minutes,
            span=timedelta(days=1),
            cap_fraction=cap_fraction,
        )


# --- response validation ---------------------------------------------------------


def test_bars_outside_the_requested_window_are_discarded() -> None:
    """A live run asked for 2012 and received one bar dated 2026-05-29.

    Counting that as data reports depth that does not exist.
    """
    request = plan_bar_windows(
        now=datetime(2013, 1, 1, tzinfo=UTC),
        maxbars=1_000,
        bar_minutes=1440,
        span=timedelta(days=300),
    )[0]
    stray = datetime(2026, 5, 29, tzinfo=UTC)

    outcome = validate_window_response(request, [stray])

    assert outcome.bars_in_range == 0
    assert outcome.bars_out_of_range == 1
    assert outcome.earliest_in_range is None
    assert not outcome.has_data, "an out-of-range answer is not data for this window"


def test_in_range_bars_are_counted_and_the_earliest_reported() -> None:
    request = plan_bar_windows(now=NOW, maxbars=1_000, bar_minutes=1440, span=timedelta(days=10))[0]
    inside = [request.start + timedelta(hours=hours) for hours in (1, 5, 3)]

    outcome = validate_window_response(request, inside)

    assert outcome.bars_in_range == 3
    assert outcome.bars_out_of_range == 0
    assert outcome.earliest_in_range == request.start + timedelta(hours=1)
    assert outcome.has_data


def test_the_window_end_is_exclusive() -> None:
    request = plan_bar_windows(now=NOW, maxbars=1_000, bar_minutes=1440, span=timedelta(days=10))[0]

    outcome = validate_window_response(request, [request.start, request.end])

    assert outcome.bars_in_range == 1, "start is inclusive, end is not"
    assert outcome.bars_out_of_range == 1


def test_a_mixed_response_keeps_only_the_in_range_earliest() -> None:
    request = plan_bar_windows(now=NOW, maxbars=1_000, bar_minutes=1440, span=timedelta(days=10))[0]
    opens = [request.start - timedelta(days=400), request.start + timedelta(hours=2)]

    outcome = validate_window_response(request, opens)

    assert outcome.earliest_in_range == request.start + timedelta(hours=2)
    assert outcome.bars_out_of_range == 1


def test_bar_reach_reports_why_the_walk_stopped() -> None:
    requests = plan_bar_windows(now=NOW, maxbars=10, bar_minutes=1440, span=timedelta(days=30))
    outcomes = [
        validate_window_response(requests[0], [requests[0].start + timedelta(hours=1)]),
        validate_window_response(requests[1], []),
    ]

    reach = summarise_bar_reach("D1", outcomes)

    assert reach.terminal_visible_earliest == requests[0].start + timedelta(hours=1)
    assert reach.windows_walked == 2
    assert reach.is_broker_depth is False
    assert "no in-range bars" in reach.stopped_because


def test_bar_reach_names_an_out_of_range_only_stop_distinctly() -> None:
    requests = plan_bar_windows(now=NOW, maxbars=1_000, bar_minutes=1440, span=timedelta(days=30))
    outcomes = [validate_window_response(requests[0], [datetime(2000, 1, 1, tzinfo=UTC)])]

    reach = summarise_bar_reach("M1", outcomes)

    assert reach.terminal_visible_earliest is None
    assert reach.total_out_of_range == 1
    assert "out-of-range" in reach.stopped_because


def test_bar_reach_surfaces_a_request_error() -> None:
    request = plan_bar_windows(now=NOW, maxbars=1_000, bar_minutes=1440, span=timedelta(days=10))[0]
    outcomes = [validate_window_response(request, [], error="Terminal: Call failed")]

    reach = summarise_bar_reach("M1", outcomes)

    assert "Terminal: Call failed" in reach.stopped_because


def test_bar_reach_does_not_call_a_truncated_plan_exhausted() -> None:
    requests = plan_bar_windows(
        now=NOW,
        maxbars=100_000,
        bar_minutes=1,
        span=timedelta(days=365 * 20),
    )
    outcomes = [
        validate_window_response(request, [request.start + timedelta(minutes=1)])
        for request in requests
    ]

    reach = summarise_bar_reach("M1", outcomes)

    assert reach.plan_truncated
    assert reach.walked_span_days == pytest.approx(4800.0)
    assert reach.planned_span_days == pytest.approx(4800.0)
    assert reach.requested_span_days == pytest.approx(7300.0)
    assert "safety step cap" in reach.stopped_because


def test_an_empty_walk_is_reported_not_crashed() -> None:
    reach = summarise_bar_reach("M1", [])

    assert reach.terminal_visible_earliest is None
    assert reach.stopped_because == "no windows planned"


# --- anchor scoring --------------------------------------------------------------


def test_a_unique_offset_reproducing_the_bar_opens_resolves() -> None:
    observations = [
        _observation(_bar(day), {0: (1000 + day, 1.29), 1: (2000 + day, 1.31)})
        for day in range(1, 13)
    ]

    verdict = score_anchor(observations)

    assert verdict.resolved
    assert verdict.offset_hours == 0
    assert verdict.epochs_are_true_utc is True
    assert verdict.matches == {0: 12}


def test_candidates_sharing_one_tick_do_not_vote() -> None:
    """The live index shape: the market is shut, so both candidates find one tick.

    Counting it twice would manufacture agreement out of a single observation.
    """
    observations = [
        _observation(_bar(day), {0: (5000, 1.29), 1: (5000, 1.29)}) for day in range(1, 13)
    ]

    verdict = score_anchor(observations)

    assert not verdict.resolved
    assert verdict.shared_tick_discards == 24, "12 bars x 2 candidates"
    assert verdict.matches == {}
    assert verdict.observations == 12
    assert verdict.samples == 12


def test_discarded_shared_tick_bars_do_not_satisfy_the_sample_minimum() -> None:
    unique = [_observation(_bar(day), {0: (1000 + day, 1.29)}) for day in range(1, 8)]
    shared = []
    for day in range(8, 13):
        same_tick = 5000 + day
        shared.append(
            _observation(
                _bar(day),
                {offset: (same_tick, 1.29) for offset in CANDIDATE_OFFSETS_HOURS},
            )
        )

    verdict = score_anchor(unique + shared)

    assert verdict.observations == 12
    assert verdict.samples == 7
    assert verdict.matches == {0: 7}
    assert not verdict.resolved
    assert f"at least {MIN_ANCHOR_SAMPLES}" in verdict.reason


def test_an_incomplete_candidate_observation_never_votes() -> None:
    observations = [
        AnchorObservation(
            bar=_bar(day),
            first_ticks={
                offset: TickRecord(time_msc=100_000 + day * 10 + offset, bid=1.29)
                for offset in CANDIDATE_OFFSETS_HOURS
                if offset != 1
            },
            attempted_offsets=frozenset(CANDIDATE_OFFSETS_HOURS),
            failures={1: "timed out"},
        )
        for day in range(1, 13)
    ]

    verdict = score_anchor(observations)

    assert not verdict.resolved
    assert verdict.observations == 12
    assert verdict.samples == 0
    assert verdict.incomplete_observations == 12
    assert verdict.incomplete_reasons == ("offset +1: timed out",) * 12
    assert verdict.matches == {}


def test_a_partial_candidate_mapping_cannot_claim_to_be_complete() -> None:
    observations = [
        AnchorObservation(
            bar=_bar(day),
            first_ticks={0: TickRecord(time_msc=1000 + day, bid=1.29)},
            attempted_offsets=frozenset({0}),
            failures={},
        )
        for day in range(1, 13)
    ]

    verdict = score_anchor(observations)

    assert not verdict.resolved
    assert verdict.samples == 0
    assert verdict.incomplete_observations == 12
    assert all("not attempted" in reason for reason in verdict.incomplete_reasons)


def test_a_tie_is_never_broken_by_candidate_order() -> None:
    """Offsets 0 and +1 both scored 12/12 live; insertion order must not decide."""
    observations = [
        _observation(_bar(day), {0: (1000 + day, 1.29), 1: (9000 + day, 1.29)})
        for day in range(1, 13)
    ]

    verdict = score_anchor(observations)

    assert not verdict.resolved
    assert verdict.offset_hours is None
    assert verdict.tied_offsets == (0, 1)
    assert verdict.epochs_are_true_utc is None
    assert "tied" in verdict.reason


def test_a_leading_offset_below_majority_does_not_resolve() -> None:
    observations = [
        _observation(_bar(day), {0: (1000 + day, 1.29 if day < 3 else 9.99)})
        for day in range(1, 13)
    ]

    verdict = score_anchor(observations)

    assert not verdict.resolved
    assert "not a strict majority" in verdict.reason


def test_a_nonzero_winning_offset_means_epochs_are_not_the_boundary() -> None:
    observations = [
        _observation(_bar(day), {0: (1000 + day, 9.99), -2: (2000 + day, 1.29)})
        for day in range(1, 13)
    ]

    verdict = score_anchor(observations)

    assert verdict.resolved
    assert verdict.offset_hours == -2
    assert verdict.epochs_are_true_utc is False


def test_no_observations_resolves_nothing() -> None:
    verdict = score_anchor([])

    assert not verdict.resolved
    assert verdict.samples == 0
    assert "no candidate offset" in verdict.reason


def test_every_declared_candidate_offset_is_scoreable() -> None:
    for offset in CANDIDATE_OFFSETS_HOURS:
        observations = [
            _observation(_bar(day), {offset: (1000 + day, 1.29)}) for day in range(1, 13)
        ]
        verdict = score_anchor(observations)
        assert verdict.resolved
        assert verdict.offset_hours == offset


# --- boundary summary ------------------------------------------------------------


def test_a_midnight_utc_run_is_summarised_as_not_1700_local() -> None:
    """The measured FBS shape: 00:00 UTC opens, i.e. 19:00/20:00 New York."""
    opens = [datetime(2026, 8, day, 0, tzinfo=UTC) for day in range(1, 29)]

    summary = summarise_boundary(opens, zone=NEW_YORK)

    assert summary.utc_open_histogram == {"00:00": 28}
    assert set(summary.local_open_histogram) <= {"19:00", "20:00"}
    assert summary.boundary_is_1700_local is False
    assert summary.histogram_alone_is_ambiguous


def test_a_1700_new_york_run_is_recognised() -> None:
    opens = [datetime(2026, 8, day, tzinfo=UTC).replace(hour=21) for day in range(1, 29)]

    summary = summarise_boundary(opens, zone=NEW_YORK)

    assert summary.local_open_histogram == {"17:00": 28}
    assert summary.boundary_is_1700_local is True


def test_the_summary_carries_the_weekly_audit_through() -> None:
    opens = [datetime(2026, 8, day, 0, tzinfo=UTC) for day in range(1, 29)]

    summary = summarise_boundary(opens, zone=NEW_YORK)

    assert summary.audit.counts
    assert summary.bars == 28


def test_an_empty_run_summarises_without_raising() -> None:
    summary = summarise_boundary([], zone=NEW_YORK)

    assert summary.bars == 0
    assert summary.first_open is None
    assert summary.utc_open_histogram == {}


def test_opens_are_summarised_in_chronological_order_regardless_of_input() -> None:
    opens = [datetime(2026, 8, day, 0, tzinfo=UTC) for day in (5, 1, 3)]

    summary = summarise_boundary(opens, zone=NEW_YORK)

    assert summary.first_open == datetime(2026, 8, 1, 0, tzinfo=UTC)
    assert summary.last_open == datetime(2026, 8, 5, 0, tzinfo=UTC)


def test_exactly_half_is_not_a_majority() -> None:
    """6/12 and 2/5 previously resolved; a coin toss is not a measurement."""
    observations = [
        _observation(_bar(day), {0: (1000 + day, 1.29 if day <= 6 else 9.99)})
        for day in range(1, 13)
    ]

    verdict = score_anchor(observations)

    assert verdict.matches == {0: 6}
    assert not verdict.resolved
    assert "not a strict majority" in verdict.reason


def test_a_tiny_sample_cannot_resolve_however_unanimous() -> None:
    observations = [_observation(_bar(day), {0: (1000 + day, 1.29)}) for day in range(1, 6)]

    verdict = score_anchor(observations)

    assert verdict.matches == {0: 5}
    assert not verdict.resolved
    assert f"at least {MIN_ANCHOR_SAMPLES}" in verdict.reason


def test_the_live_ten_of_twelve_result_still_resolves() -> None:
    """The measured FBS GBPUSD anchor must survive the stricter rule."""
    observations = [
        _observation(_bar(day), {0: (1000 + day, 1.29 if day <= 10 else 9.99)})
        for day in range(1, 13)
    ]

    verdict = score_anchor(observations)

    assert verdict.matches == {0: 10}
    assert verdict.resolved
    assert verdict.offset_hours == 0
