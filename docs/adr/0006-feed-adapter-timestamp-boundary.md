# ADR-0006: Feed adapter timestamp, availability and skew boundary

Status: accepted — in force from P1 start, against SPEC v1.0 SHA-256
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`.

Accepting this ADR introduces **no** Principal-reserved threshold change (SPEC §0 rule 9): the skew
budgets below mirror the existing §3.4(b) alert and §7.5 halt values rather than setting new ones.
Acceptance of this ADR is therefore not approval of a threshold.

See **Implementation status** at the end for what is built and what remains.

## Context

As shipped at Gate 0, `src/tradebot/core/types.py::_event_times` rejected `ts_recv < ts_event` at
construction for every event type. (It is now split into `_platform_event_times` and
`_externally_clocked_event_times` — see Implementation status.)
For `Bar` and `Forecast` both stamps are ours, so the invariant is sound. For `Tick`
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
this may not be deferred to P4. **Discharged** by `tests/replay/test_live_arrival.py`, which drives a
real `WallClock` from an injected wall/monotonic pair advancing in lockstep, so minutes pass instantly
without tripping the clock's own discontinuity guard. It asserts: publishing a live event without
deferral is a look-ahead; the queue withholds an early arrival and admits it on time; a burst is
delivered in source order as time passes; **no event ever reaches a handler ahead of a freshly read
clock**, checked at the subscriber so it holds for the normalizer/queue/bus composition rather than
for any one part; a backfilled arrival is admitted at once but flagged, with its market time
untouched; and under venue-ahead skew the key follows the venue stamp.

Also discharged: imputation flagging (`tests/unit/data/test_normalize.py`) and
`CLOCK_SKEW` propagation into `Bar.quality_flags` (`tests/unit/data/test_bars.py`).
Still owed: per-field-per-vintage decomposition, quarantine-not-drop for `Fill`,
and a wrong-signed-offset case that the two-sided bound catches and a one-sided
bound does not.

## Implementation status

Built at P1 slice 1 (the part that actually blocked adapters):

| Decision | Where |
|---|---|
| `available_at` on `Tick` and `Fill`, with `>= max(ts_event, ts_recv)` enforced | `core/types.py::_externally_clocked_event_times` |
| `Bar` / `Forecast` keep `ts_recv >= ts_event` unconditionally fatal | `core/types.py::_platform_event_times` |
| `Bar` / `Forecast` derive `available_at` from `ts_recv`, so it cannot drift | `core/types.py` properties |
| `ts_recv` never normalized, clamped or offset — anywhere | absence of any such code, asserted by `test_a_present_receipt_stamp_is_never_modified` |
| Skew measured, not asserted: `skew_lb = ts_event - ts_recv` | `Tick.skew_lb`, `Fill.skew_lb` |
| `CLOCK_SKEW`, `TS_RECV_IMPUTED`, `BACKFILLED` flag vocabulary | `core/types.py::QualityFlag` |
| Imputation as exactly `ts_event`, no latency knob | `data/normalize.py::normalize_tick` |
| One shared normalizer for both feed paths (NN-1) | `data/normalize.py` — single implementation |
| Flags sorted and de-duplicated for byte-identical re-ingest (NN-10, §4.6) | `normalize_tick` |
| Bus admits on the availability key **and** each stamp in its own right | `core/bus.py::_validate_availability` |
| Ordered head-of-line publish-deferral queue | `data/deferral.py::DeferralQueue` |
| Accelerated-`WallClock` arrival test | `tests/replay/test_live_arrival.py` |
| `seq` synthesis from arrival position, stable across re-ingests | `data/ingest.py::TickIngester` |
| Position-based overlap splice — never value hashing | `data/ingest.py::overlap_length` |
| Ingest provenance (`source`, `run_id`, audit-only `ingested_at`) | `data/ingest.py::RawTick` |
| `Bar.quality_flags` union from constituent ticks | `data/bars.py::BarBuilder` |
| SPEC 4.3 `seal_latency` receipt rule, one code path for both modes | `data/bars.py::BarBuilder._seal` |

The bus deliberately checks all three stamps rather than the key alone. A well-formed event's key
dominates the other two by construction, but `Event` is a *structural* protocol, so an adapter can
supply an object whose key understates its own stamps. Checking each one keeps a specific rejection
reason for the metrics and denies a malformed event a single field to lie about
(`test_bus_rejects_an_event_whose_key_understates_its_stamps`).

### Clarification: a late arrival is absorbed, not rejected

An early draft of `DeferralQueue` rejected a submission whose availability key
regressed behind its predecessor, on the reasoning that head-of-line blocking would
otherwise silently delay the earlier event. Writing the head-of-line test showed
that to be wrong twice over, and it is recorded here because the question will recur.

First, the two properties are **mutually exclusive**. If keys are forced to be
monotonic then the head always carries the smallest key, so "stop at the first
event that is not observable" can never withhold anything behind it — head-of-line
blocking becomes unreachable and the guarantee it provides is vacuous.

Second, rejecting is wrong on the merits. This ADR chose a single FIFO queue over
per-event timers precisely because timers *"reorder under transit jitter"* — that
is, a live feed genuinely delivers an older tick after a newer one. An out-of-order
key is therefore an expected network condition, not a feed defect, and refusing it
would reject the case the queue exists to absorb.

So the queue holds a late arrival in arrival order and releases it behind its
predecessor. The cost is that it waits past its own key; the benefit is that the
delivered sequence is the one a replay reproduces. Releasing it early would reorder
against the source order ADR-0002 assigns to the adapter.

### Two properties of the splice worth knowing before writing an adapter

**Alignment compares only what the source said** — `ts_event`, `bid`, `ask` and the sizes. It
deliberately excludes `ts_recv`, because a live gap-recovery re-fetch stamps a *new* local receipt
time for the very same tick; comparing on it would make an event fail to match itself and duplicate
every recovered tick. `available_at`, `quality_flags` and `seq` are excluded for the same reason —
the platform derives them, so they cannot be evidence of source identity.

**The alignment window is a hard bound, and it fails closed.** If a re-fetch overlap consumes the
entire retained tail while material remains to append, a longer overlap cannot be ruled out, so
appending could silently duplicate stored rows. The ingester raises `IngestAlignmentError` rather
than guess. An adapter must therefore size `tail_window` (default 1024) above the largest overlap
its source can produce — for a range-addressed archive that is the re-fetch stride; for a live
gap-recovery it is the longest outage the fetch can span.

### A sealed bar is an ordinary deferred event

Found by the end-to-end chain test, and recorded because unit tests structurally
cannot show it. With a non-zero `seal_latency` the builder seals a bar as soon as its
interval ends, but SPEC 4.3's receipt rule puts the resulting `ts_recv` at
`ts_close + seal_latency` — in the *future* at the moment of sealing. Publishing a
freshly sealed bar therefore raises `LookAheadError`, correctly.

So **bars are routed through a `DeferralQueue` exactly like ticks**, and through
their own queue rather than a shared one, so a bar is never head-of-line blocked
behind a later tick. The alternative — delaying the *seal* until
`ts_close + seal_latency` has elapsed — was rejected: it would make the first arm of
the receipt maximum dead code, and would force the builder to hold two accumulators
whenever a new interval opens before the previous one may be sealed.

### Delivered `Bar` event versus clean-layer bar row

The delivered `Bar` event is deliberately narrower than SPEC 4.2's clean-layer
bar row. `spread_max`, `bid_close` and `ask_close` are storage columns, not fields
of the event delivered to strategies. This is the same separation SPEC 4.2 makes
for `source` and `seq`: they belong to a stored tick row, not the delivered `Tick`
event. Expanding the core event is therefore not part of this ADR.

The distinction does not mean those values are already persisted. The current
builder retains the event aggregate only (`spread_mean` and mid-price OHLC), so it
cannot yet populate the three additional clean-layer columns. The storage slice
must add a separate clean-bar row/aggregate that retains them. Until then, SPEC
4.2 clean-bar persistence is incomplete; changing the core `Bar` type instead
requires a separate principal-approved decision.

Still outstanding, in dependency order:

| Decision | Blocked on |
|---|---|
| Raw/clean Parquet layout, full clean-bar storage aggregate and `dataset_id` snapshotting (SPEC 4.2) | ADR-0007 — what is written depends on what is fetched |
| Zero-tick bar emission (SPEC 4.3's configurable alternative) | the expected-liquidity calendar, which distinguishes a closure from an outage |
| Higher timeframes built from lower-timeframe bars | a bar-to-bar path; the tick path satisfies SPEC 4.3 today |
| Clean-layer last-observation-wins and supersession logging for revisions | the storage layer |
| Venue UTC-offset estimator (minimum, whole-hour quantised, alarmed on change) | a concrete adapter |
| Per-field-per-vintage decomposition of point-in-time records | the point-in-time calendar |
| `Fill` quarantine + `RECON_MISMATCH` on non-timestamp validation failure | P4 OMS |
| Freshness / staleness watchdog reading `ts_recv` | live feed |
| TCA local-stamp-to-local-stamp latency pairs | P4 |
| Budget calibration `[VERIFY per broker]` | a chosen broker |

No `Fill` normalizer exists yet: the type-level invariant is in force, but a `Fill` arrives from a
broker adapter, which is P4. The `Tick` path is the one P1 needs.
