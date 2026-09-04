# ADR-0007: Data source selection

Status: in progress — the `boundary-pass-v5` broker probe is complete and its results are recorded
under **Measured facts** below. Four items remain open: historical tick density and two-sided
continuity during acquisition, true broker M1 depth (as distinct from terminal-visible reach), SPEC
§2.4's expected-liquidity calendar, and the written licence answer for any third-party source retained
for cross-source validation. The D1 walk is complete as a terminal-visible measurement; it is
deliberately not labelled immutable broker depth.

Probe report: `docs/reports/fbs-depth-probe.json`, SHA-256
`653a08605dc7929e6427c1e758e6708a90ecc333f1dc83e03c9f8a5942592e1d`, completed 2026-09-04
against `FBS-Demo`, MetaTrader 5 build 6140.

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

These are the earliest ticks retrievable from this demo terminal **as of this run**, not a claim that
the returned floor is immutable maximum broker depth. Four index floors moved earlier as terminal
history synchronised between runs; the report's hash makes this particular observation reproducible.

| Logical | Broker symbol | Earliest tick | Span |
|---|---|---|---|
| GBP/USD | `GBPUSD` | 2011-12-19 | ~14.7 yr |
| EUR/USD | `EURUSD` | 2011-12-19 | ~14.7 yr |
| UK100 | `UK100` | 2020-11-30 | ~5.8 yr |
| GER40 | `DE30` | 2021-03-05 | ~5.5 yr |
| US30 | `US30` | 2023-03-06 | ~3.5 yr |
| US500 | `US500` | 2025-02-27 | ~1.5 yr |
| US100 | `US100` | 2025-10-13 | ~0.9 yr |

**The FX result changes this ADR's shape, but does not yet discharge source acceptance.** SPEC §13
asks for ≥ 8 years of GBP/USD and EUR/USD ticks; the earliest retrievable tick gives both pairs a
~14.7-year date-span reach on the broker we would actually trade with. That clears the date-range
threshold **provisionally**, subject to acquiring the range and verifying density, bid/ask continuity
and reproducibility before Gate 3. FBS is therefore the primary FX depth candidate as well as the
§4.1/§6.4 calibration venue. A third-party archive is not required merely to reach an early date; it
remains a fallback if acquisition fails and an optional second source for §4.4 #7 agreement.

**The index result reinforces `data_only` on measured grounds.** UK100 and GER40 have retrievable
start dates more than five years old, so their date spans provisionally clear §13's numeric target;
continuity is unverified. Neither can contain the complete stress set SPEC §2.1 mandates: their start
dates permit the 2022 gilt crisis but exclude the 2016 Brexit vote, the 2016-10-07 flash crash and the
2020 COVID crunch, while the three US indices are shallower still. The FX start dates permit all four,
again subject to acquisition and continuity checks. Using execution-venue index history avoids a
venue mismatch where it exists, but depth still prevents an index-strategy Gate-3 claim. `data_only`
therefore remains the honest v1 scope.

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
- **`trade_tick_value` is a live, FX-rate-derived number, not a constant.** The UK100 and DE30 values
  changed slightly between live passes because they are 0.1 GBP and 0.1 EUR converted to USD at the
  prevailing rate. The snapshot values remain in the hashed report, but they **cannot be cached in
  `configs/instruments.yaml`**, and the §2.3 startup verification must not equality-compare them; this
  is a live-read field per §2.4. Only the invariant inputs (contract size, point, digits, tick size)
  are comparable.
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

- **M1 broker depth remains unresolved; D1 terminal-visible reach is now measured.** An earlier draft
  claimed `copy_rates_from(date, count=1)` would sidestep the `maxbars = 100000` cap the way
  `copy_ticks_from(date, count=1)` does. **That claim was wrong and is withdrawn.** Rate calls count
  backwards from the requested date and are bounded by terminal chart history. V5 instead makes
  maxbars-derived, calendar-bounded requests and labels every result `terminal_visible_earliest` with
  `is_broker_depth: false`. All M1 plans are explicitly truncated to 4,800 of the requested 7,300
  days and stopped after only 120–140 walked days when a window returned one out-of-range bar; the
  visible floor is in May 2026, so another identical run cannot establish true depth. The D1 walks
  reached the full requested 7,300 days for FX (the September 2006 request floor) and 5,475 days for
  the indices (terminal-visible bars around May/June 2013), but retain the same no-broker-depth label.
  Resolving M1 requires a deliberate history-acquisition path, not an inference from this chart cache.
- **The D1 trading-day boundary is resolved for FX and remains unresolved for the indices.** MT5 tick
  and bar epochs cannot be interpreted by comparing them with our wall clock. The observable route is
  D1 bar **open instants plus a price anchor**, which `scripts/fbs_depth_probe.py`
  (`boundary-pass-v5`) measures. This is the input SPEC §4.3 needs for a broker-priced instrument's
  daily bars and §6.3 needs for the financing accrual boundary.

  **V5 D1 pass, measured:** 1,038 bars; UTC opens `00:00 × 1038`; New York opens
  `19:00 × 353`, `20:00 × 685`; eight interior weeks not holding five bars, of which `2024-W43` held
  **six** because of a short Sunday `00:00` stub bar. That Sunday stub sits inside the autumn DST
  mismatch window (EU fell back 2024-10-27) and is exactly the anomaly SPEC §4.4 check 10(c) exists
  to catch.

  **The histogram alone remains ambiguous.** A uniform `00:00 UTC` open histogram could mean a
  genuine UTC-midnight roll or a non-zero-offset local midnight encoded as UTC. Both produce
  byte-identical timestamps, so the former `epochs_look_like_true_utc` inference was unsound and was
  removed. The price anchor below is what resolves the FX case.

  **What separates them is a price anchor**, and it has now been run. For a sample of D1 bars,
  compare the bar's open price against the first tick at or after each candidate boundary instant
  (`epoch + offset`). The offset whose first tick reproduces the bar's open price is the real
  boundary; offset zero means the reported epoch *is* the boundary.

  | Instrument | Anchor result | Reading |
  |---|---|---|
  | GBPUSD | offset 0, 10/12, unique | Genuine 00:00 UTC D1 boundary |
  | EURUSD | offset 0, 10/12, unique; offset -1 matched once | Same; the non-winning match does not change the strict majority |
  | UK100 | offsets -3 and +2 tied at 1 each; 11/12 usable | **Unresolved** — a tie is not a boundary |
  | GER40, US30, US500, US100 | no candidate reproduced a sampled bar open; 12/12 usable | **Unresolved** |

  The scoring rule has been tightened and the two FX rows still stand under it. A resolved
  verdict now additionally requires at least `MIN_ANCHOR_SAMPLES = 8` **usable** samples and a strict
  majority (`matches × 2 > usable_samples`). A bar is usable only after every candidate query has
  completed and at least one candidate owns a non-shared first tick; discarded shared ticks and
  incomplete queries no longer inflate the denominator. The FX rows clear both bars (10 of 12); the
  index rows remain unresolved. A tie is never broken by candidate order.

  **The FX conclusion, and it is a firm one.** The four-year histogram is `00:00 UTC`, i.e.
  19:00/20:00 New York, which is **not** the 17:00 New York internal FX day SPEC §3.4 mandates. The
  broker's daily bars are therefore a different object from ours. They remain **validation references
  only** (§4.3), and internal daily bars MUST be built from ticks on the 17:00 New York boundary.
  That makes §4.3's build-from-ticks rule load-bearing rather than stylistic, and it means the
  reconciliation rule joining on the UTC close instant — never the label date — is mandatory here.

  **Every mapped symbol reported Bid chart mode**, so the anchor's bid comparison is sound today. The
  probe records and enforces `chart_mode`: any symbol not declared Bid-built receives an unsupported,
  unresolved anchor rather than a Bid-based verdict.

  The session-week and DST-window logic is **production code** (SPEC §4.4 check 10(c)), not probe
  scaffolding: it lives in `src/tradebot/data/session_weeks.py`, the boundary analysis lives in
  `src/tradebot/data/boundary_probe.py`, and the probe imports both so there is one implementation
  under mypy and CI. Two bugs found in the first pass are fixed there: a wholly missing interior week
  was invisible because only observed week keys were counted, and a mismatch window must contain
  both the transition Sunday's close-labelled stub week **and** the fully affected weeks beginning the
  following Monday. The fingerprint now audits their union rather than dropping either side.

  **Neither verdict may report a pass, and both now say so in their own status.** The two questions
  are distinct — the weekly audit asks whether sessions are missing, check 10(c) asks whether the
  boundary is ours — so they carry separate statuses, and the evidence for each is asymmetric:

  - **Excess is structural and unconditional.** A holiday can only *remove* a session, never add one,
    so a close-labelled week holding more than five sessions is a defect no calendar and no sample
    size can legalise. It is reported before anything else, and an expected count above five is
    rejected as an invalid calendar rather than used to legalise a sixth session.
  - **Shortfall is calendar-dependent.** A four-session week is a dropped session or a public
    holiday, and nothing in the data distinguishes them. Without SPEC §2.4's expected-liquidity
    calendar the audit returns `INDETERMINATE` — it does **not** fail open, and an empty or
    too-short sample is `INDETERMINATE` too rather than silently clean.
  - **Misalignment evidence needs no coverage; a clean verdict needs both seasons.** One stub bar
    inside a mismatch window is direct evidence of a foreign boundary, so `MISALIGNED` is returned
    even from partial coverage of that window. The converse does not hold: quiet weeks in one season
    say nothing about the other, so `ALIGNED` requires full coverage of a spring **and** an autumn
    window plus a supplied calendar. A sample that is quiet across both seasons but has no calendar
    is `PROVISIONALLY_ALIGNED` — consistent with alignment, explicitly not gate-grade.
  - **Evidence collection is separate from verdict precedence.** Duplicate, structural excess,
    calendar excess and shortfall, uncovered weeks and ambiguous closes are all collected before a
    status is selected. An unrelated six-bar week can therefore never hide a mismatch-window
    shortfall. `ALIGNED` additionally requires the underlying weekly audit to be `PASSED`; a failed
    audit outside the mismatch windows makes the fingerprint `INDETERMINATE`, never clean.

  V5 therefore reads weekly audit `FAILED` and 10(c) `MISALIGNED` for both FX pairs on the
  `2024-W43` six-session week. That conclusion needs no calendar because excess is unconditional.
  Each index instead has a four-session `2024-W13`; without a calendar, both its weekly audit and
  fingerprint remain `INDETERMINATE`. **§2.4's expected-liquidity calendar remains the blocker for
  judging those index shortfalls and for any future `ALIGNED` claim**, and is an open P1 deliverable.

  Session-week keying no longer invents a close. An earlier version labelled each week by
  `open + 24h`, then another silently substituted `open + 25h` across a market gap. Consecutive opens
  no more than 25 hours apart still provide an exact close. Across a longer gap, `session_closes`
  retains the honest `[open + 23h, open + 25h]` interval and assigns a week only when both ends prove
  the same close-labelled ISO week; otherwise the audit reports the close and candidate weeks as
  ambiguous. Explicit final closes must be UTC, strictly after the open and within 25 hours.

  Probe-mechanics limits were also load-bearing enough to fix in the tested module. Bar-window
  planning applies a **calendar ceiling as well as** the `maxbars` bar-count cap — ~20 days for M1,
  one year for D1 — because a request for 365 days of M1 data is ~525,600 bars against a 100,000-bar
  cap and the terminal simply stops answering. The 240-step safety cap covers only ~13.1 of a
  requested 20 M1 years, so every result now exposes requested, planned and walked spans plus
  `plan_truncated`; it never calls that cap an exhausted full-depth walk. In v5 no safety cap was
  exhausted: the M1 walks stopped on terminal-visible out-of-range evidence after 120–140 days, while
  the D1 results followed the paths recorded above.

  Every MT5 function call is bounded by the 90-second call limit, a five-minute symbol deadline and a
  30-minute run deadline. The result and `last_error()` snapshot are captured together on the worker
  thread. A blocking C call cannot be cancelled, so the **first timeout poisons the session**: the
  probe makes no later MT5 call, does not call `shutdown()`, atomically writes and fsyncs a `PARTIAL`
  sidecar, then exits non-zero with `os._exit()`. A partial or otherwise incomplete run never replaces
  the requested canonical evidence file. Connected/demo/FBS identity is required before measurement,
  and a safe complete run shuts down normally. **A timeout remains absent evidence, not a negative
  result.** End-to-end stub tests pin the poisoned-session, canonical-preservation, chart-mode and
  identity paths in addition to report shape.
- **Session hours are unmeasured.** `SYMBOL_SESSION_OPEN` / `SYMBOL_SESSION_CLOSE` are session
  open/close **prices**, not times (`GBPUSD` reports 1.35246 / 1.35224), so they do not answer the
  question. Session times require the session-quote/session-trade calls.
- The data-licence answer for any third-party second source.

## Decision (partial; licence and calendar work remain open)

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

**Measurement before decision.** The bounded demo probe is now complete. It disproved the assumption
that broker FX tick history is necessarily too shallow and quantified the index shortfall before a
source decision was made.

**Measured source composition.** FBS ticks are the primary deep-history **candidate** and calibration
source for GBP/USD and EUR/USD: their ~14.7-year earliest-tick reach provisionally clears §13's date
threshold on the venue that §6.4 requires before Gate 3. Acceptance still requires full acquisition
and density, bid/ask-continuity and reproducibility checks. Internal D1 bars are built from accepted
ticks on the mandated 17:00 New York boundary; the broker's midnight D1 bars are validation references
only. A written-licence third-party FX archive is fallback depth if FBS acquisition fails and optional
independent evidence for §4.4 #7 otherwise. If used, the two venues will genuinely disagree at the
same instant, so venue remains part of the instrument key (§4.2) and no merged series is produced. No
deep index-strategy source is selected in v1 while indices remain `data_only` and their measured start
dates exclude parts of the mandatory stress set.

**Discharging §6.4.** The FBS demo measurement establishes the data path, but Gate 3 must still rerun
the accepted strategy evidence on the actual trading-broker dataset as §6.4 requires. Naming FBS for
P1 data does not by itself commit the P4 broker decision.

## Consequences

- P1 can proceed with bounded FBS tick acquisition and continuity validation for GBP/USD and EUR/USD
  without waiting for a third-party archive response.
- Broker D1 FX bars cannot be the research bars; the 17:00 New York tick-built path and UTC-close
  reconciliation are mandatory.
- Index data may be retained, but index strategies remain outside v1 until the mandatory stress
  history, expected-liquidity calendar and a conclusive boundary method are available.
- A third-party source, if retained for cross-source evidence, stays separately keyed and remains
  behind the written licence gate.

## Verification

Gate 1 = §4.6. Every acceptance test must be evaluable against whatever source is chosen, and where
one is structurally not evaluable (a single-venue CFD has no cross-source check and no third-party
known-answer chart), the evidence file records that explicitly rather than leaving a check that
silently never runs.
