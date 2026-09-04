# Phase-0 Architect handoff

## Assumptions made

- Submission of the document authorizes creation of a reviewable P0 candidate, but does not
  silently change its draft status or authorize Gate 0/P1.
- P0 “paper mode” means live-shaped forecast wiring. P4 still owns the PaperBroker, OMS, risk,
  reconciliation, and real broker adapter.
- P0 “backtest” is a causal smoke/replay, not performance evidence, so it emits no PnL and does
  not pretend that P2's cost model exists.
- NN-4 kill switches and NN-9 reconciliation become applicable when P4 introduces an execution
  path; they are not weakened or stubbed in P0.
- Both spring and autumn US/UK DST mismatch windows satisfy the impossible contemporary phrase
  “UK-DST-only week.” ADR-0003 records the exact interpretation.

## Principal decisions — ALL RESOLVED 2026-09-04

All eight are closed and adopted in ADR-0004. Recorded here for provenance; do not re-litigate.

1. **Freeze or edit** → Frozen at v1.0 **with errata**, supplied draft preserved byte-for-byte at
   `docs/SPEC-supplied-2026-09-03.md`. ADR-0004.
2. **Receipt-time admission** → Confirmed. Availability is `max(ts_event, ts_recv, available_at)`.
   NN-6's storage obligation is *not* subsumed by it. ADR-0002.
3. **P0 smoke-test boundaries** → Confirmed. NN-7 **applies** and is discharged by the label, not
   waived. The NN-1 code-parity claim is narrowed to `code_parity`, and the paper-mode limitation is stated
   explicitly in the evidence pack.
4. **FX daily intervals** → Confirmed: half-open `[17:00 NY, next 17:00 NY)`, boundary tick belongs
   to the new session, labelled by close, Sunday open labelled MONDAY. Broker-priced instruments use
   **their own** broker trading-day boundary; exchange cash-session bars are separate features.
5. **Clock thresholds** → **Three** distinct policies, not two, and ADR-0003's justification was a
   category error, now corrected. Local guard (1 s) ≠ host sync (250 ms) ≠ broker skew (§7.5).
6. **Gate-0 dashboard screenshot** → §10.6 rewritten to a three-status evidence model. Gate-0
   observability is the Prometheus exposition; only the screenshot *format* is deferred. ADR-0005.
7. **Data and broker sources** → Split three ways. The *decision* defers to P4; the broker's *price
   history* does not, because §6.4 binds before Gate 3. Constraints recorded in ADR-0007.
8. **`ts_recv >= ts_event`** → Retained as fatal for platform-produced events (`Bar`, `Forecast`);
   replaced for externally-clocked events (`Tick`, `Fill`) by an explicit `available_at` plus a
   measured, flagged, budgeted skew observable. **No epsilon anywhere.** ADR-0006 blocks the first
   P1 adapter.

Additional Principal decisions taken at the same time: cash/spot index CFDs with indices
`data_only`; Principal-reserved thresholds (§0 rule 9); `ENTRY_HALT` / `FLATTEN_HALT` halt states
(§7.5); position-level mandatory broker stops (§8.3); deep history moved from the P1 gate to a P3
entry requirement; branch protection on `master`.

## Evidence

- Design: `docs/adr/0001-platform-and-p0-stack.md`, ADR-0002, ADR-0003.
- Interface resolution: `docs/interfaces.md`.
- Gate candidate: `docs/reports/gate0_evidence.md`.
- Committed-SHA CI evidence: run `33852037018`, candidate
  `4de5f7a540ed216b3568141bd83392af3189c3cf`; details and hashes are in the gate pack.

## What the next role needs to know

The spec is frozen, Isaac Gumbi recorded Principal approval on 2026-09-04, and committed-SHA CI is
green. **The Data Engineer must still not begin P1** until an independent human reviewer signs the
evidence pack and `master` has the §12.3 private-repository enforcement. GitHub rejected the
required ruleset with HTTP 403 because the account needs Pro; making the trading-system repository
public is not an acceptable workaround. Every required evidence category must read `PROVIDED`.

**Read ADR-0006 first; it blocks the first adapter.** When P1 begins: preserve both `ts_event` and
`ts_recv`, add `available_at` and never normalise the raw local stamp, publish in
`(available_at, source, seq)` order behind a single head-of-line deferral queue, and use one shared
normaliser for both the historical and live paths (NN-1). Synthesise `seq` where the source has none
and keep it stable across re-ingests; deduplicate by **position over an overlap window, never by
hashing values**. Introduce a raw tick schema capable of representing locked and crossed quotes
(`ask <= bid`), flag them, and exclude them from clean mid/bar/trading delivery; the delivered `Tick`
continues to require `ask > bid`. Make clean bars use `ts_event == ts_close` and seal them with the
configured `seal_latency`. Treat venue as part of the instrument key — never merge two venues into
one series.

Two traps worth naming. Freshness and staleness read `ts_recv`, never `available_at`. And backfilled
events legitimately arrive years old with a current receipt stamp, so any arrival bound must exempt
them or the trader halts on its own recovery path.

Real instrument facts and data-source terms marked `[VERIFY]` require primary-source research and
ADRs. A third-party source is a *candidate* until its licence permits the intended use **in
writing** — silence is not consent (ADR-0007).
