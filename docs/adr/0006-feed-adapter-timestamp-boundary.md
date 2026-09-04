# ADR-0006: Feed adapter timestamp, availability and skew boundary

Status: proposed — **blocks the first P1 adapter.** Nothing in `src/` implements this yet; P0 ships
unchanged.

## Context

`src/tradebot/core/types.py::_event_times` rejects `ts_recv < ts_event` at construction for every
platform event. For `Bar` and `Forecast` both stamps are ours, so the invariant is sound. For `Tick`
and `Fill` it is not a *validatable* invariant at all: `ts_event` comes from the venue's or broker's
clock and `ts_recv` from the local host, and an ordering between two uncontrolled clocks can only be
**measured**, never asserted. With well-disciplined NTP a fast feed will still deliver events with
`ts_recv` microseconds to milliseconds before `ts_event`. That is ordinary skew, not corruption.

A hard constructor rejection would therefore halt a healthy feed, destroy the evidence in the act of
raising, and generate exactly the pressure SPEC §12.1 #6 forbids — an engineer weakening a guard to
get production running.

Alternatives considered:

1. Keep the strict rejection everywhere. Rejected: cannot distinguish 400 µs of jitter from a corrupt
   feed, cannot record what it saw, cannot alert, cannot report to TCA. Refusing to construct a
   `Fill` for an execution that already happened leaves an unrecorded live position and guarantees an
   NN-9 mismatch over a clock offset.
2. A configured tolerance ε: admit when `ts_recv >= ts_event - ε`. Rejected: genuinely weakens the
   mechanism, permits `ts_recv < ts_event` inside the system so every downstream latency computation
   can go negative, and makes admission depend on a config value not carried on the event (NN-10).
3. Normalise: `ts_recv = max(ts_recv_raw, ts_event)`. Considered seriously and rejected on three
   grounds. It erodes an NN-4 kill switch, because §4.5's staleness watchdog computes
   `now − last ts_recv` and a normalised stamp in the future makes the watchdog count late. Once the
   adapter always normalises, the constructor branch becomes structurally unreachable on every
   production path while its unit test stays green — the invariant disabled *by omission*, which is
   worse than disabling it explicitly. And `ts_recv` would mean different things in the raw and clean
   layers under one column name.
4. Admit and flag without normalising. Rejected: surrenders the invariant and gains nothing option 5
   does not.
5. An explicit, checkable `available_at` key plus a measured skew observable. **Chosen.**

## Decision

- `Tick` and `Fill` gain `available_at: datetime`. The core invariant becomes
  `available_at >= ts_event` **and** `available_at >= ts_recv` — a comparison the platform can
  actually validate.
- `ts_recv` stays the **unmodified local host stamp**: no `max()`, no epsilon, no normalisation,
  ever, in core or in an adapter. Every stored field is a value the system actually observed.
- `Bar` and `Forecast` keep `ts_recv >= ts_event` unconditionally as fatal. A violation there is our
  bar builder's or strategy's bug.
- `OrderRequest` carries no timestamps and is unaffected.
- **Skew is measured, not asserted.** Per event, record `skew_lb = ts_event − ts_recv`, attach
  `CLOCK_SKEW` to `quality_flags` when positive, and feed the §7.5(C2) estimator. Construction never
  fails for a timestamp or skew reason; a budget breach fires the breaker instead, keeping the
  fail-closed decision on the *trading* path where it belongs.
- **A `Fill` is never dropped for a timestamp or skew reason.** That scope is binding and does not
  exempt it from NN-3 attribution or any other validation. A `Fill` that still cannot be constructed
  is persisted to quarantine **and** raises `RECON_MISMATCH` — never silently discarded.
- **Publish deferral, ordered.** Live adapters hold each event until `clock.now() >= available_at`,
  in a **single head-of-line delay queue in strict source order** — never per-event timers, which
  reorder under transit jitter and would void ADR-0002's guarantee that upstream adapters own stable
  source order.
- **Backfill is exempt from any live-tail arrival bound.** Warm-up, gap-recovery and reconnect
  fetches legitimately return events hours or days old stamped with a current `ts_recv`. They are
  tagged and never admitted as fresh. A naive two-sided bound would halt the trader on the system's
  own documented recovery path.
- **Freshness and quality read the LOCAL stamp** (`ts_recv`), never `available_at` — §4.5 staleness,
  §7.4 check 3, the §7.5 data-stale breaker, §4.4 #1 and #5.
- **TCA** computes latency from local-stamp-to-local-stamp pairs. Broker-clock-derived durations are
  advisory, may be negative, and are reported — never clipped to zero. `CLOCK_SKEW`-flagged records
  are segmented or excluded from slippage calibration.
- **`seq` is load-bearing, not optional.** Where a source supplies no sequence number, synthesise it
  from arrival position within one fetch, stable across re-ingests of the same range. Deduplicate on
  re-fetch by **aligning an overlap window by position**, never by hashing values: a feed that
  repeats an identical quote is normal, and value-hash dedup silently drops real ticks, corrupting
  tick-count volume, the completeness report and the byte-identical rebuild test.
- **Imputation for history with no receipt stamp:** `ts_recv := ts_event` exactly, flagged
  `TS_RECV_IMPUTED`. No per-source latency knob — a second latency parameter would double-count
  against §6.2's single execution-latency parameter, and mis-models reality anyway, since feed
  latency is near-zero most of the time and spikes precisely at news and session opens.
- **Where the venue's UTC offset is not exposed by the API**, it MUST be pinned empirically and
  quantised to whole hours, estimated by the **minimum** of `(venue stamp − our now)` over samples
  confirmed fresh within the poll window — not the median. One-way delay is additive and
  non-negative, so the minimum is the unbiased estimator and the median is biased by the delay
  distribution; and a last-tick sample taken on a quiet symbol, over a weekend or a holiday, tracks
  staleness rather than offset. A discrete change in the estimate raises an alarm rather than being
  silently absorbed, because a wrong-signed offset pushes `ts_event` into the past, where a one-sided
  invariant passes silently forever and every bar boundary is also wrong.
- **The shared normaliser is used by BOTH the historical and live feed paths** (NN-1). One
  implementation, two callers.

## Consequences

**No choice on this ADR unblocks P1 by itself.** `bus.py` rejects on `ts_event > clock.now()` *before*
the `ts_recv` branch, so an adapter that stamps `ts_recv` at arrival still raises `LookAheadError`
under every option considered. The publish-deferral rule is what makes live admission work. Record
this so no P1 author believes normalisation buys admissibility.

Budgets — alert at rolling-60 s p99 skew > 250 ms (mirroring §3.4(b)); halt at any single confirmed
sample > 1 s (§7.5) — are rule-9 defaults **pending measurement** `[VERIFY — no broker chosen; stamp
granularity, matching-engine-vs-gateway stamping and venue clock discipline are per-broker facts]`.

## Verification

The deferral queue is live-only and is **not** exercised by the replay code-parity harness, since backtest
gets ordering free from `SimClock.advance_to`. P1 MUST add an accelerated-`WallClock` arrival test;
this may not be deferred to P4. Plus: imputation flagging, per-field-per-vintage decomposition,
`CLOCK_SKEW` propagation into `Bar.quality_flags`, quarantine-not-drop for `Fill`, and a
wrong-signed-offset case that the two-sided bound catches and a one-sided bound does not.
