# Phase-0 interface contract

This document narrows the §3.6 sketch where the master specification is inconsistent. ADR-0002
and ADR-0003 contain the rationale. SPEC §3.6 is explicitly non-normative; **this document and those
ADRs are the binding contract**, against SPEC v1.0
(SHA-256 `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`).

## NN-1 comparison vocabulary

Two named strengths; the terms are never interchanged, and the unqualified term is prohibited in artifacts.

- **Code parity** — identical `Strategy` / `Portfolio` / `RiskManager` / `OMS` code, with only
  `DataFeed`, `Broker` and `Clock` swapped. Demonstrable from P0.
- **Behavioural parity** — SPEC §6.8's test: identical order sequences under an accelerated
  `WallClock` with `PaperBroker`. A Gate-4 obligation, wholly outstanding.

## Event admission

An event must expose `ts_event` and `ts_recv`, both UTC. The bus admits it only when its availability
timestamp has passed:

```text
available_at <= clock.now()  AND  ts_event <= clock.now()  AND  ts_recv <= clock.now()
```

Every event exposes `available_at`. `Tick` and `Fill` **store** it, and their constructor enforces
`available_at >= max(ts_event, ts_recv)`. `Bar` and `Forecast` **derive** it from `ts_recv`, which
their own fatal `ts_recv >= ts_event` invariant already makes the maximum — so it cannot drift.

The bus checks all three stamps, not just the key. For a well-formed event the key dominates the
other two, so the key alone would suffice; but `Event` is a *structural* protocol, so an adapter can
supply an object whose key understates its own stamps. Checking each one keeps a specific rejection
reason for the metrics and denies a malformed event a single field to lie about.

`ts_event` and `ts_recv` come from different clocks — the venue's and the local host's. Adapters MUST
measure the difference and never assume it is zero or non-negative. `ts_recv` is the unmodified local
observation and is never clamped, offset or normalised: it is what the SPEC §4.5 staleness watchdog
measures. Skew is reported instead, via `skew_lb` and the `CLOCK_SKEW` quality flag.

`tradebot.data.normalize` is the **single** normalizer for both the historical and live feed paths
(NN-1). It imputes a missing receipt stamp as exactly `ts_event` under `TS_RECV_IMPUTED`, with no
per-source latency knob, and sorts flags so re-ingesting the same source bytes is byte-identical.
See ADR-0006.

For a `Bar`, `ts_event == ts_close`; the bar is not visible while it is still forming. Feed order
is authoritative. The bus preserves publication and subscription order and appends nested events.
A handler exception halts the instance and no queued event continues.

## Time capabilities

Strategies receive `StrategyContext(clock: ReadClock)`, whose only operation is `now()`. Runtime
adapters use `Clock`, which adds `sleep_until()` and `schedule()`. Only a simulation driver owns
`SimClock.advance_to()`.

## Strategy capabilities

`BarStrategy`, `TickStrategy`, and `FillAwareStrategy` are separate structural protocols. A bar
strategy is therefore not forced to implement a misleading no-op tick handler. No context exposes
the broker, data feed, environment mode, or host clock.

`Broker` is a generic structural port with the required connect, order, reconciliation-snapshot,
instrument-specification, and event-stream operations. Its result schemas remain type parameters
until the OMS domain model is frozen in P4; no broker SDK or executable adapter exists in P0.

## Configuration and provenance

Runtime YAML is safe-loaded with duplicate-key rejection and validated by frozen Pydantic models
with recursive `extra="forbid"`. Hash input is canonical JSON of the resolved non-secret model.
Order provenance fields have no empty defaults. `UNCOMMITTED` is an explicit development marker,
not a valid release identity.

`client_order_id` is defined solely by ADR-0002 and is deterministic: identical retry inputs produce
an identical ID, and random fragments are prohibited because they break NN-10 and make §6.8's
identical-ID check unsatisfiable. It is the internal audit key and is **never shortened to fit a
broker field**; a bounded broker tag is derived separately where a venue requires one.

## Clock capabilities

Both clocks reject a `schedule()` deadline already in the past. A scheduled callback that raises
latches a visible failure — `ScheduledHandle.failed`, and on `WallClock` also `schedule_failure` plus
an optional injected supervisor — rather than being dropped by the event loop. `SimClock` marks the
handle and re-raises, because there the simulation driver is the caller.
