# ADR-0007: Data source selection

Status: in progress — the broker depth probe is complete and its results are recorded under
**Measured facts** below. Two items remain open: the M1/D1 pass (blocked on the terminal's
`Max. bars in chart` cap) and the data-licence answer.

Probe report: `docs/reports/fbs-depth-probe.json`, SHA-256
`ed169302c39b68f57b51087b7e2af60fbdfd8e3bada94f4077376bf78de3ba83`, run against `FBS-Demo`,
MetaTrader 5 build 6140.

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

## Measured facts

Measured 2026-09-04 on the FBS demo server. These supersede the corresponding `[VERIFY]` items;
anything still marked `[VERIFY]` below was **not** settled by the probe.

### Retrievable tick depth

| Logical | Broker symbol | Earliest tick | Span |
|---|---|---|---|
| GBP/USD | `GBPUSD` | 2011-12-19 | ~14.7 yr |
| EUR/USD | `EURUSD` | 2011-12-19 | ~14.7 yr |
| UK100 | `UK100` | 2020-11-30 | ~5.8 yr |
| GER40 | `DE30` | 2023-06-09 | ~3.2 yr |
| US30 | `US30` | 2025-04-15 | ~1.4 yr |
| US500 | `US500` | 2026-01-29 | ~0.6 yr |
| US100 | `US100` | 2026-03-31 | ~0.4 yr |

**The FX result changes this ADR's shape.** SPEC §13 asked for ≥ 8 years of GBP/USD and EUR/USD
ticks; the broker alone supplies ~14.7. So the deep-history requirement is met by **the broker we
would actually trade with**, which is what §4.1 *prefers* for calibration and what §6.4 *requires*
before Gate 3 — one source satisfying both, instead of a third-party archive plus a separate
calibration pass. A third-party archive is consequently **not on the critical path**; it becomes a
candidate second source for §4.4 #7 cross-source agreement, nothing more.

**The index result reinforces `data_only` on measured grounds.** Only UK100 clears §13's ≥ 5-year
target, and it does so on the traded venue. More decisively, SPEC §2.1 requires backtests to include
the 2016 Brexit vote, the 2016-10-07 flash crash, the 2020 COVID crunch and the 2022 gilt crisis:
FX history covers **all four**, and **no index covers any of them**. The earlier argument for
descoping — that research and execution would sit on two different brokers' CFDs — no longer
applies, since index history would now come from the execution venue itself. The argument that
stands is depth, and it is now a number rather than an assumption.

### Instrument specifications

- **Cash CFDs confirmed:** every index has `expiration_time = 0` and `trade_calc_mode = 2`. No
  expiry, no rollover logic — matching the Principal's v1 instrument-type decision.
- **Pip arithmetic confirms SPEC §2.1's hand-calculated table exactly.** `GBPUSD` has
  `trade_contract_size = 100000`, `point = 1e-05`, `digits = 5`, `trade_tick_value = 1.0`. One pip
  (0.0001) on a standard lot is therefore USD 10.00, as §2.1 states. §10.2's financial-correctness
  test now has measured broker data to assert against.
- **Triple-swap day differs by asset class, and this is a real trap.** `swap_rollover3days` is **3
  (Wednesday) for FX** and **5 (Friday) for every index CFD**. §2.1 states the Wednesday convention
  with a `[VERIFY]`; it holds for FX only. A cost model applying one rule to both would mis-accrue
  index financing on two days of every week.
- **`trade_tick_value` is a live, FX-rate-derived number, not a constant.** `UK100` reports
  `0.135342` and `DE30` `0.116211` — 0.1 GBP and 0.1 EUR converted to USD at the prevailing rate. It
  therefore **cannot be cached in `configs/instruments.yaml`**, and the §2.3 startup verification
  must not equality-compare it; it is a live-read field per §2.4. Only the invariant inputs
  (contract size, point, digits, tick size) are comparable.
- **`currency_margin` differs from `currency_profit`:** `GBPUSD` margins in GBP and profits in USD.
  Against a USD account that is a third conversion series, exactly as flagged. Index margin
  currencies follow their profit currencies (GBP, EUR, USD).
- **Tick fields:** `time_msc` is present, settling that `[VERIFY]`. FX ticks carry `last = 0`,
  `volume = 0`, `volume_real = 0`, confirming there is no traded volume and that
  `VolumeKind.TICK_COUNT` is the correct label. A 10,000-tick recent sample was 100 % two-sided with
  zero crossed or locked quotes — recent only; historical density and two-sidedness back to 2011 are
  still `[VERIFY]`.
- **Suffixed variants exist:** `GBPUSDw` and `EURUSDw` alongside the unsuffixed symbols, out of 585
  total. The probe mapped the logical names to the **unsuffixed** symbols. That mapping is now an
  explicit decision to record, and the variants' depth and specifications are unmeasured `[VERIFY]`.

### Account facts

- **The account is HEDGING, not netting** (`margin_mode = 2`). This bears directly on §8.3: under
  hedging the broker holds many positions per symbol, each with its own ticket and its own SL/TP, so
  a per-position native stop is straightforwardly achievable. The cost is that §8.2's target-position
  model must be imposed in software — never open an opposing ticket, always close by explicit
  position ticket to reach target — and NN-9 reconciliation becomes a set comparison over tickets
  rather than a scalar. Whether FBS offers a netting account type at all is `[VERIFY]`, and which
  mode to require is a P4 broker-ADR decision.
- Account currency USD, `FBS Markets Inc.`, server `FBS-Demo`, demo confirmed.
- `trade_allowed = False` — algo trading is disabled in the terminal. Irrelevant to a read-only
  probe; it blocks any order path and must be enabled before P4 contract tests.

### Still open

- **M1/D1 depth is inconclusive.** `copy_rates_range` from 2000 failed (`-1 Terminal: Call failed`)
  against `maxbars = 100000`. Re-run with `Max. bars in chart` set to Unlimited.
- **The broker's trading-day boundary is unmeasured, and cannot be measured the way the probe first
  tried.** MT5 tick and bar epochs are UTC, so comparing a quote's timestamp against our own clock
  reveals nothing about the server's session offset. The observable route is **D1 bar open times**,
  which are UTC epochs and therefore expose the boundary directly — so the re-run must cover D1, not
  only M1. This is the input SPEC §4.3 needs for a broker-priced instrument's daily bars, and §6.3
  needs for the financing accrual boundary.
- **Session hours are unmeasured.** `SYMBOL_SESSION_OPEN` / `SYMBOL_SESSION_CLOSE` are session
  open/close **prices**, not times (`GBPUSD` reports 1.35246 / 1.35224), so they do not answer the
  question. Session times require the session-quote/session-trade calls.
- The data-licence answer for any third-party second source.

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
