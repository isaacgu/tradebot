# ADR-0007: Data source selection

Status: not started — blocked on Gate-0 sign-off. The Principal's practical constraints are recorded
below and are **not** open questions; what remains is measurement and licence confirmation.

## Context

SPEC §4.1 lists candidate sources and requires the implementing agent to evaluate them and record the
choice here. Nothing was invented at Phase 0.

Principal's constraints, confirmed 2026-09-04:

| Constraint | Value |
|---|---|
| Broker | An MT5-platform retail broker; account held, demo available |
| Index representation | Cash/spot index CFDs; `data_only` in v1 |
| Historical data budget | Free sources only |
| Account base currency | USD |
| Deep historical scope | GBP/USD and EUR/USD |

Consequences that follow directly. USD base means GBP/USD and a USD-quoted index need no conversion
leg, while a GBP-denominated or EUR-denominated index CFD does — but the **profit currency is a
per-broker per-symbol property** and MUST be read from the broker's own symbol specification, never
inferred from the underlying's denomination `[VERIFY]`. `SYMBOL_CURRENCY_MARGIN` may differ from
`SYMBOL_CURRENCY_PROFIT`, which can introduce a third series.

## Decision (to be completed)

Selection axes, all of which must be answered per candidate before it is chosen:

1. History depth for the actual instruments and representation chosen.
2. Bid/ask fidelity — whether both sides are genuinely present throughout, or one side is carried
   forward from a previous update.
3. Granularity, timestamp precision and timezone convention.
4. One-off vs recurring cost. Note that a "requester-pays" bulk route is **not free**: the requester
   pays the cloud provider's request and egress charges.
5. Licence terms for bulk programmatic download, indefinite internal storage of derived data, and
   non-redistribution.
6. Whether the prices are the broker's own (calibration fidelity) or another venue's (depth).
7. Index representation, and whether an index series exists at all at that source.
8. **Revision / immutability** — a source that silently re-issues history threatens NN-10's
   `dataset_id`, and a server-side store that is corrected against another timeframe cannot satisfy
   §4.6's byte-identical rebuild unless snapshotted once with a `dataset_id`.

Per-candidate `[VERIFY]` items — none of these may be asserted from recollection:

- **Third-party FX tick archives:** depth and continuity of bid/ask for the deep-scope pairs; terms
  of use for bulk download and internal storage; whether an automated route requires prior written
  consent; whether quotes are that venue's own pool; the meaning of any "volume" field; the licence
  covering any bulk-transfer route, and its cost.
- **Broker history:** maximum retrievable depth (tick and bar, separately), API pacing, whether the
  client caps bar history by a local setting, whether real tick history exists for the period or only
  minute bars, demo-vs-live identity, and terms of use.
- **Paid vendors:** pricing, venue coverage, depth, derived-data licence. Out of scope while the
  budget is free-only, but recorded so the axis is not lost.

**Licence gate.** A source is a *candidate* until its terms permit the intended use **in writing**.
Where a vendor's terms restrict database construction or require prior written consent for automated
acquisition, silence is not consent. The written request must state the exact route and intended
concurrency, the instruments and date ranges, raw and derived retention with backups and checksums,
that use is private research and own-account trading, that neither raw nor cleaned data will be
redistributed, whether derived aggregate reports are permitted, and whether a commercial or
supplementary licence is required. Until an affirmative answer arrives, P1 proceeds on a
broker-sourced or explicitly licensed fallback so the phase is not blocked on a third party's reply.

**Measurement before decision.** The broker's own retrievable depth is a one-hour probe on the demo
account. That measurement **precedes** any decision that assumes the broker's history is too short —
sequencing it the other way would settle the largest question in the plan on an assumption.

**Two-source composition.** §4.1's pattern stands: a deep third-party archive for research history,
the traded broker's own history for cost calibration. The broker is the *calibration* source, not the
depth source. The two will genuinely disagree on price for the same instant — they are different
liquidity pools — so venue is part of the instrument key (§4.2) and no merged series is produced.

**Discharging §6.4.** A practice account with the candidate broker is opened during P1 to measure
retrievable depth, granularity and pacing, because §6.4 requires results to be re-run on the trading
broker's data *before Gate 3*. Naming a candidate for data purposes does not commit the P4 broker
decision.

## Consequences

To be completed when the ADR is written.

## Verification

Gate 1 = §4.6. Every acceptance test must be evaluable against whatever source is chosen, and where
one is structurally not evaluable (a single-venue CFD has no cross-source check and no third-party
known-answer chart), the evidence file records that explicitly rather than leaving a check that
silently never runs.
