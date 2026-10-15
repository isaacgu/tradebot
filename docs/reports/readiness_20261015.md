# System readiness — October 15, 2026

## Outcome

The September data jobs finished; they are not still training or acquiring data.
Gate 0 is approved. Gate 1 is not approved. No market-data model has been fitted,
no strategy has passed financial evaluation, and order execution remains disabled.
Isaac's and Delsa's previously recorded approvals are preserved; another general
approval does not resolve a failed measurement.

This is a status and implementation plan, not a gate receipt, fitted-model artifact,
data-release authorization, or retrospective preregistration.

## Completed work and remaining evidence

| Area | Evidence-backed position |
|---|---|
| Foundations | Gate 0 approved, including Principal and independent human review. |
| Historical acquisition | The bounded probe recorded 190/190 checkpoints and 42,796,598 ticks. This is not continuous deep history or permission to train. |
| Latest corrected-clock rebuild | Completed September 6 at 13:48:40 UTC / 15:48:40 SAST. Two byte-identical builds: 5,905,147 selected ticks, 35,569 one-minute bars, 25 daily bars including context, from 32 raw files. |
| Reference quality | 16 / 4,410 expected liquid minutes flagged: 0.362811791%. The strict requirement is less than 0.1%, allowing at most four flagged minutes for this denominator. Zero expected minutes are missing. The numerical result fails. |
| Final binding | Formal INDETERMINATE identifies missing exact final bindings. It does not cancel the numerical failure. The producer audit also retains its 366/367 result and launch-inventory omission. |
| Engineering replay | Current saved synthetic replay has 320 bars and 62 forecasts. It contains no fitting and is not economic evidence. |
| Backtesting | Synthetic market-fill and supplied-cost accounting exist. Complete Gate-2 financial validation does not. |
| GUI | Existing local Grafana views cover system, acquisition, quality, broker observation and engineering replay. They are observers, not trading controls. |

Authoritative retained references:

- [Gate-1 evidence](gate1_evidence.md), including exact producer, acceptance and
  wrapper hashes and the unchanged strict threshold.
- [Completed corrected-clock run](source_clock_rebuild_20260906.md).
- [Acceptance statements and their scope](source_clock_acceptance_receipt_20260906.md).
- [Completed source-row/context investigation](corrected_new_minute_context_20260906.md).
- [Purpose-scoped research contract](purpose-scoped-research-authorization.md).

The ten-minute follow-up corroborated all 21 PRICE/SPREAD predicates and 117 unique
original-capture rows. It did not establish an ingestion defect or independently
verify economic quote correctness. Repeating the same immutable inputs is not a
route to a different accepted result.

## What blocks real training

1. **Data/source adjudication.** The corrected clock policy remains experimental.
   The measured quality failure remains. On October 15 Isaac confirmed that no FBS
   technical answer has been received. Personal data-use permission is already
   recorded and is not the missing item.
2. **A complete accepted dataset and release.** Required Gate-1 evidence, exact
   approved snapshot, purpose-scoped release and independently pinned registry do
   not exist as a usable training package. The bounded probe is not the required
   GBP/USD and EUR/USD deep-history/stress/tick-fidelity package.
3. **Backtester acceptance.** Full fill paths, costs, metrics, real-data zero-edge
   validation, arrival-time parity and statistical validation remain incomplete.
   The current real-data authorization requires Gate 2 for both defined research
   purposes. A separately reviewed, narrow backtester-validation contract is needed
   to run the Gate-2 zero-edge test after Gate 1, without pretending Gate 2 already
   passed or allowing fitting/strategy selection.
4. **Actual fitting.** The current strategy emits fixed momentum forecasts.
   Synthetic experiment partitions explicitly perform no fitting. A calibration
   producer and persisted fitted artifact must be implemented and tested before a
   genuine training run can be claimed.
5. **Historical provenance semantics.** The current strategy suppresses every
   quality flag, including TS_RECV_IMPUTED. Historical FBS data carries that flag.
   Any future policy must retain conservative availability and report caveats,
   distinguish provenance from fatal quote defects, and be reviewed. Do not strip
   flags or weaken admission to obtain forecasts.

## Smallest first-fit proposal, not an approved experiment

Follow SPEC S1 before introducing ML meta-labeling: GBP/USD time-series momentum,
then the same rules on EUR/USD. Use fixed lookbacks 8/16/32/64 with equal weights.
Daily bars closing at 17:00 New York are a proposed first baseline; the timeframe
has not been adopted by this report.

Fit only a positive forecast-scale parameter on the training partition, with the
declared mean-absolute-forecast target of 10 and cap of 20. Freeze it before OOS
access and retain sample counts, exclusions, dataset/config/code hashes and the
fit result. A target that cannot be estimated is a failure, not a silent default.
This is calibration of a simple baseline, not evidence of profitability.

Before financial use, preregister the full hypothesis, sizing, broker costs,
explicit UTC train/OOS/lockbox boundaries, purge/embargo and trial accounting.
Exact dates must come from an accepted history inventory and prior-access audit;
an already inspected period must not be called untouched. Include the mandated
2016, 2020 and 2022 stress windows. Daily holding requires real broker swap and
rollover assumptions; absent financing cannot be silently zero.

## Next evidence-producing action

Seek FBS's historical time/time_msc convention, dated October 2024 session/transition
facts, and explanation or corrected archive for implicated quotes. The existing
[technical inquiry](fbs-historical-calendar-inquiry.md) is prepared but unsent by
the assistant. No message was sent in this assessment and no raw data was published.
An expressly licensed independent-source comparison is another possible diagnostic,
not automatic replacement of venue-specific evidence.

There is also a detector-semantics question for review: the implementation counts
re-entry into a pre-jump plus/minus 20-sigma band within five rows as reversion.
The retained investigation shows this can count a 17.65% retracement. SPEC says
"reverts" without specifying the tolerance. This is an ambiguity to resolve
prospectively, not demonstrated permission to clear existing price flags. Preserve
the old result, explain any reviewed semantic change and test genuine moves and
bad ticks independently. Do not tune it against the observed pass/fail rate.

Until this evidence is resolved, useful work is confined to engineering, synthetic
tests and transparent monitoring. Do not label those activities market-data training,
change the threshold or calendar to obtain a pass, or fabricate release approvals.
