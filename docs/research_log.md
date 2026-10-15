# Research log

Status: deferred to Phase 3.

No strategy research has begun and no hypothesis, trial, result, or performance claim is
recorded for Phase 0. Research attempts, including failures, will be appended here when the
Phase-3 workflow is authorised.

## Core-engine engineering preparation — 2026-09-04

The Principal authorized beginning engine implementation while P1 evidence is in
collection. One fixed momentum engineering candidate now exercises causal features,
checkpoint recovery and decision reporting on synthetic paths. Its prospective
formula and settings are recorded in [ADR-0010](adr/0010-causal-decision-replay.md).

Financial research trials evaluated: **0**. Parameter searches or fitted models: **0**.
No in-sample, out-of-sample or lockbox market-data period has been evaluated by this
increment. Software test fixtures, including generated Parquet inputs, are invented
data. No return, cost, profitability or Gate-3 claim is made. This entry records the
implementation's provenance; it is not a baseline performance result or an input to
a deflated-Sharpe calculation. The SPEC 5.1 hypothesis, economic rules, data splits
and trial ledger must be registered before financial evaluation begins.

## Forecast-calibration engineering preparation — 2026-10-15

The Principal requested resolving data quality and completing the backtester and
fitting pipeline, with training conditional on passing checks. This increment
implements the fixed mean-absolute-raw scalar estimator in [ADR-0016](
adr/0016-training-only-forecast-calibration.md). One predetermined engineering
variant is exercised on the existing invented EURUSD/GBPUSD fixture. Each
instrument gets its own training-only fit and fresh validation warmup. There is
no parameter search, financial return objective, market-data fit or lockbox run.

The CLI stores every started attempt, failure and completion in its append-only
local registry. Its report names the saved model hashes and measured sample
counts. These synthetic calibrations are software verification, not economic
trials for DSR. Financial research trials and market-data models remain **0**.

The failed FBS reference month is preserved; no reversion threshold, calendar,
source-clock policy or quality flag was altered to obtain a pass. Real training
still requires genuine data and phase-gate release. This entry does not approve
an economic experiment or retrospectively declare an observed period untouched.
