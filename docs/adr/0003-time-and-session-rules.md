# ADR-0003: Clock, timezone, and FX boundary rules

Status: accepted — adopted against SPEC v1.0 (2026-09-04), SHA-256 `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`. Errata adopted at freeze are enumerated in ADR-0004.

## Context

SPEC §3.4 mandates aware UTC internally, IANA zones for local market rules, a 17:00 New York FX
boundary, simulation and wall clocks, `sleep_until`, and `schedule`. The §3.6 sketch omits
`schedule`. The spec also asks for a “UK-DST-only” week, but modern UK DST is contained within US
DST; the relevant cases are spring and autumn mismatch windows. It does not define ambiguous or
nonexistent local civil times, FX interval inclusion, schedule tie order, or the response to an
NTP step. The 250 ms host-sync alert and 1 second trading-path skew halt are different policies.

Alternatives considered:

1. Fixed UTC offsets and `datetime.now()` inside consumers. Rejected because DST changes and
   strategy access to the host clock violate the spec.
2. Clamp wall time so it never regresses. Rejected because hiding a discontinuity is unsafe.
3. Inject UTC wall and monotonic sources, raise on discontinuity, and use an explicitly advanced
   simulation clock. Chosen because behavior is deterministic and fail closed.

## Decision

All internal inputs are required to be aware and zero-offset, then normalized to `datetime.UTC`.
Local conversion uses `zoneinfo` objects loaded directly from the dependency-locked `tzdata` wheel
rather than mutable host timezone files. Nonexistent local times raise; ambiguous times require explicit
`fold`. An FX session-day starts inclusively at 17:00 New York Sunday through Thursday and ends
at the next market close; Friday and Saturday do not invent new session-day starts. A daily bar
is labelled by its closing `ts_event`.

`Clock` exposes `now`, async `sleep_until`, and synchronous callback `schedule`. `SimClock` moves
only through the simulation driver, rejects regression, and executes equal-deadline callbacks by
registration sequence. The driver peeks an event, advances to `max(ts_event, ts_recv)`, and only
then publishes it; strategies receive only `ReadClock` and cannot advance time.

`WallClock` compares wall elapsed time with an injected monotonic source. Raw or monotonic
regression raises. A discrepancy greater than 1 second also raises, and the threshold is
constructor-injectable.

**The 1-second threshold is justified on its own terms and is NOT derived from SPEC §7.5.** An
earlier draft of this ADR claimed it "follows SPEC §7.5's trading-path clock-skew halt"; that was a
category error and is withdrawn. §7.5's breaker measures our UTC against the *broker's server
clock*; this guard measures our realtime against our own monotonic source and can detect nothing
about a broker. The two numbers are equal by coincidence, not by derivation, and MUST NOT be
unified. SPEC §7.5's broker-server-time skew halt is **unimplemented** in P0 — it is not even
representable against the current `Broker` port, which exposes no server-time method — and is a
Gate-4 criterion.

The guard fires on three distinct causes, not on "an NTP step" alone: (i) an out-of-band realtime
step (an operator setting the clock, or `chronyd` stepping under a `makestep` line — note that
chrony's own default behaviour is to *slew*, and `makestep` is a configuration line on the host,
not a daemon default [VERIFY `chrony.conf(5)`]); (ii) a **suspend/pause gap** where
`CLOCK_MONOTONIC` stops and realtime does not — VM pause, snapshot restore, and commonly live
migration, which is the most likely real trigger on a VPS; (iii) on non-Linux hosts, unmatched
slew. Narrowing the ADR to "only a discrete step" would hand a future engineer a licence to widen
or bypass the guard after the first migration trip.

Two properties are recorded so nobody rediscovers them under pressure. The guard is **sticky**:
after a raise the baseline is never refreshed, so every later `now()` also raises and recovery is a
process restart, not an in-process acknowledgement. It is **asymmetric**: a backward step smaller
than `max_step` raises `ClockMovedBackwardError` only until wall time catches up, then passes
silently.

Sleeps and schedules use the event loop's monotonic timer, re-check the guarded wall clock on every
wake, and re-arm rather than firing before their UTC target during an allowed slow slew.
**Both clocks reject a `schedule()` deadline that is already past** — previously only `SimClock`
did, while `WallClock` fired immediately — and a scheduled callback that raises latches a visible
failure on both the handle (`failed`) and the clock (`schedule_failure`), optionally notifying an
injected supervisor. See Consequences for the defect this closes.

The “US-DST-only” and “UK-DST-only” test requirements are interpreted as follows, and this
interpretation is **adopted** at the v1.0 freeze rather than left open. Under the rules in force
since 2007, UK summer time is strictly contained within US daylight time, so no contemporary week
has the UK on DST and the US off it; the meaningful contemporary cases are the spring US-only
window (US 2nd Sunday March → UK/EU last Sunday March, ~2–3 weeks) and the autumn US-only window
(UK/EU last Sunday October → US 1st Sunday November, ~1 week), plus each zone's own transition.
**This was not true historically:** under the US 1987–2006 rule a genuine UK-DST-only window of
roughly one week existed each spring from 1995 to 2006, which lies inside the maximum historical
depth contemplated at SPEC §4.1 (2003+). If pre-2007 history is ingested, tests MUST add a
pre-2007 spring window. All such logic is derived from `zoneinfo` for the actual date and never
from a rule stated in prose.

## Consequences

DST behavior does not depend on the host timezone, and clock steps surface rather than being
masked. A WallClock discontinuity must be connected to the system breaker once the trading path
exists. P0 scheduled callbacks are infrastructure hooks; timer events and business scheduling are
deferred until a consuming component needs them.

**Session bucketing.** `fx_session_bounds(ts) -> (start, end) | None` is the only function a bar
builder may bucket with; it returns `None` when the market is closed. `fx_trading_day_start` is
retained only as a boundary helper and MUST NOT be used to bucket ticks — for a Saturday input it
returns the start of a session that has already closed, which is correct as a "most recent
boundary" answer and wrong as a membership answer. It becomes `datetime | None` or is deleted; its
only importer today is a test, so making it fail closed is free now and expensive later. A
docstring is not a guard.

**Scoped session-length invariant.** For every year in the dataset's supported range, every FX
session-day is exactly 24 hours and the weekend closure is 47 h (spring), 48 h (normal) or 49 h
(autumn). This is *scoped, not universal*: tzdata carries a non-Sunday US transition
(1942-02-09, US War Time) that produces a 23-hour session, so the property test must be bounded to
the supported range. Consumers must compute session length as `ts_event - ts_open` rather than
assuming 24 h, because SPEC §4.3 puts non-FX daily bars on other boundaries where half-days exist.

**Closed defect — `WallClock.schedule()` fail-open.** As originally implemented, an exception
raised inside `_WallScheduledHandle._wake` — from the clock guard itself or from the callback —
escaped into asyncio's default exception handler. The callback was silently dropped, `_timer` was
already `None` and `_cancelled` still `False`, so the handle reported itself live while being
permanently dead. `now()` was fail-closed; `schedule()` was fail-open and silent. This was
invisible to the entire test suite because every clock and bus test used `SimClock`. It is now
fixed: `_wake` latches, the handle exposes `failed`, the clock latches `schedule_failure`, and an
optional injected supervisor is notified (a supervisor that itself raises has its error attached as
a note rather than re-entering the loop). `SimClock` marks its handle failed and re-raises, since
there the simulation driver *is* the caller. **P4 constraint:** stop verification and reconciliation
MUST NOT depend on the entry scheduler — a dead entry timer must not be able to take protective
maintenance down with it.

## Verification

Tests cover both DST mismatch windows, winter/summer Sunday open and Friday close, the New York
17:00 boundary, leap day, year rollover, ambiguous/nonexistent local times, schedule cancellation
and tie order, simulation regression, backward NTP movement, and a +2 second injected step.
