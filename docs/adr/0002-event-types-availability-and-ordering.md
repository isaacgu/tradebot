# ADR-0002: Event types, availability, and deterministic ordering

Status: accepted — adopted against SPEC v1.0 (2026-09-04), SHA-256 `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`. Errata adopted at freeze are enumerated in ADR-0004.

## Context

SPEC §3.1 requires every event to have `ts_event` and `ts_recv` and forbids delivery when market
time is in the future. The §3.6 sketch conflicts with that rule: `Bar` has only open/close,
`Forecast` has only `ts`, metadata is mutable, and attribution fields default empty. Checking
only `ts_event` also permits a late observation to be consumed before it was available. The spec
does not define equal-time, subscriber, or re-entrant ordering.

Alternatives considered:

1. Follow the sketch literally and gate only `ts_event`. Rejected because it permits point-in-time
   leakage and creates incompatible event shapes.
2. Wrap every payload in a generic envelope with IDs and timestamps. Rejected for P0 because it
   adds nesting and serialisation machinery before storage/audit requirements are implemented.
3. Use concrete frozen/slotted dataclasses satisfying a small structural `Event` protocol, with
   both timestamps on each event and a FIFO bus. Chosen as the smallest strict contract.

## Decision

Every bus event exposes UTC `ts_event` (when it happened) and UTC `ts_recv` (when this platform
observed it). A bar's `ts_event` is its close and its read-only `ts_close` alias prevents two close
fields from diverging. Before any subscriber is invoked, the bus requires both timestamps to be
less than or equal to `clock.now()`; effectively availability is `max(ts_event, ts_recv)`. P1
point-in-time datasets may add a later explicit `available_at`, which must join the same maximum.
Platform-defined market events also require `ts_recv >= ts_event`; adapters must surface clock
skew explicitly rather than admitting an impossible causal ordering.

Events use exact-type subscriptions. External events are published in deterministic feed order.
Subscribers run in registration order. An event published by a handler is appended to the FIFO
queue and runs only after all handlers for the current event. A handler exception clears queued
work, halts the bus, records a failure observation, and propagates an `EventDispatchError`.
There is no timestamp sort inside the bus: upstream replay/feed adapters own stable source order,
which prevents the bus from guessing how to reorder genuinely late live events.

Forecast metadata is a tuple of string pairs rather than a mutable dict. Forecasts must be finite
inside `[-20, 20]`. `OrderRequest` is an intent rather than a market event; its provenance fields
are mandatory, typed enums replace free-form order strings, and all prices/quantities are
`Decimal`. Optional strategy capabilities are separate protocols so `on_tick` is genuinely
optional.

Client order IDs are deterministic idempotency keys derived from explicit run identity, logical
sequence, event time, strategy, instrument, and environment. Identical retry inputs produce the
same ID; random UUID fragments are prohibited because they break replay and retry idempotency.
The readable prefix is followed by 128 bits of SHA-256 output. P4 broker mappings must preserve a
one-to-one durable mapping if a venue imposes a shorter identifier limit.

## Consequences

A tick whose market time is old but receipt time is future cannot leak. Re-entrant behavior is
stable and non-recursive. Upstream adapters must provide deterministic source ordering and the
simulation driver must advance its clock before admission. Generic payload envelopes, event IDs,
persistent sequence numbers, and cross-process ordering remain future ADR topics when audit
storage is introduced.

HANDOFF Q2 and Q8 are resolved by this ADR as amended at the v1.0 freeze. Availability is
`A = max(ts_event, ts_recv, available_at)`. Replay feeds MUST publish in `(available_at, source, seq)`
order, stably, because the driver advances the clock to `A` before admission and `SimClock` refuses
to move backward. NN-6's storage obligation is NOT subsumed by the admission gate: every vintage of
a revisable field is still stored and still tested at Gate 1.

From P1, `ts_recv >= ts_event` is retained unconditionally for **platform-produced** events (`Bar`,
`Forecast`), where both stamps are ours and a violation is our own bug, and is replaced for
**externally-clocked** events (`Tick`, `Fill`) by `available_at >= max(ts_event, ts_recv)` plus a
measured, flagged, budgeted skew observable. The reasoning: an ordering between two uncontrolled
clocks — the venue's and the local host's — can only be measured, never asserted, so a hard
constructor rejection would fail on ordinary sub-millisecond skew and would destroy the evidence
the exception was meant to surface. `ts_recv` remains the **unmodified local host stamp**: no
`max()`, no epsilon, no normalisation, anywhere. Normalising it was considered and rejected because
it would silently defeat the §4.5 staleness watchdog (which computes `now - last ts_recv`), would
make the constructor branch unreachable on every production path while its unit test stayed green,
and would give one column two meanings across the raw and clean layers. **No epsilon exists
anywhere in the system.** Details are specified in ADR-0006, which blocks the first adapter.

ADR-0002 remains the SOLE definition of the `client_order_id` input tuple; SPEC §8.2 cites it and
must not restate it.

## Verification

Unit tests cover future market time, future receipt time, equality admission, FIFO subscriber
order, re-entrant order, deep metadata immutability, mandatory order attribution, and fail-closed
handler behavior. Replay tests compare canonical forecast trace hashes across both Gate-0 wirings.
