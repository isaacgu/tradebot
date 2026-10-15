# Backtester and fitting engineering — October 15, 2026

## Outcome

This increment fixes a data-quality reporting defect and implements conservative
order matching, arithmetic metrics, train-only forecast calibration and a frozen
validation runner. It is **partial engineering delivery, not completion of P2 or
permission to begin market-data training**. No broker order is placed.

The retained FBS-Demo/EURUSD reference still has **16 / 4,410 flagged expected
liquid minutes (0.362811791%)**, failing strict **<0.1%**. The source-clock mapping
remains experimental and no FBS technical response has been received. No raw data,
flag, threshold, calendar or approval was rewritten to obtain a pass. See the
[quality investigation](data_quality_resolution_20261015.md) for hashes and the
separate final-binding shortfall.

## Implemented and independently reviewed

| Component | Delivered capability | Scope that remains absent |
|---|---|---|
| Quality summary | A measured SPREAD_OUTLIER breach now dominates indeterminate context; eight regressions preserve the existing numerical boundaries and partial-reversion semantics. | No demonstrated quote/clock correction, no cleared reference flags. |
| Tick matching | Synthetic MARKET, LIMIT, STOP, STOP_LIMIT and attached SL/TP; bid/ask sides, strict activation latency, gap stops, conservative limits, exact Decimal prices and complete-tail validation. | Real-data SimBroker, portfolio/order lifecycle, broker sessions, margin/reject/partial-fill models and calibrated slippage. |
| Bar matching | Already-active brackets using separate bid/ask OHLC; adverse-first ambiguous exits and explicit unknown intrabar time. | General bar-mode entry matching and tick-disambiguated integration. |
| Metrics | Returns, actual-duration CAGR, sample annualized volatility, power-guarded Sharpe, deepest/longest/censored drawdowns, trade ratios and supplied signed cost ratio. | Remaining metrics and the full WF/CPCV/PBO/DSR/PSR/Monte-Carlo statistical suite; these are named in output rather than silently omitted. |
| Fitting | One positive scale per instrument from training-only raw momentum strengths, canonical saved artifacts, exact config/split/data/code identity. | Economic strategy selection, portfolio sizing, sufficient history, model promotion. |
| Frozen validation | Saved artifact loaded before validation; changed split/config denied before warmup; unselected intervals and true EOF checked. | Untouched-market-lockbox proof, financial returns or Gate-3 evaluation. |

The data-quality skill guided the distinction between an actual reporting bug and
unresolved source interpretation. Regression tests and retained source artifacts
provide inspectable evidence for this code-focused delivery.

Independent agent review found and prompted fixes for three correctness/provenance
issues: combining unrelated bid/ask bar extrema, changed-split warmup access, and
unselected overlapping tail bars. Registry implementation identity was also added
to the calibration producer binding. These reviews are engineering review, not a
new independent human gate signature.

## How to verify

Use the locked Python 3.12.14 / uv 0.12.9 environment:

```bash
uv run --no-sync make check
uv run --no-sync python -m tradebot.research.calibration_demo --output-root build/calibration-smoke-local --attempt-id first --git-sha UNCOMMITTED
```

For committed evidence supply the actual full tested commit instead of
`UNCOMMITTED`. Use a fresh attempt ID for retries. Failed or interrupted attempts
remain in the registry; models and reports cannot overwrite differing bytes.
CI repeats the fixed synthetic run, compares the report bytes, and uploads the
models/report/ledger under `synthetic-calibration-<tested-SHA>`. It does not upload
raw broker files or turn that artifact into a data-release receipt.

The complete local `make check` passed with **1,999 warnings-as-errors tests**
(239 added), **89.01%** aggregate branch coverage, **96%** core and **88%** non-core.
Both separate coverage floors passed. Ruff formatting/lint, strict mypy on 138
files, Bandit, pip-audit and the Gate-0 smoke also passed. The local smoke is
explicitly `UNCOMMITTED`, not a new gate receipt. Matching and arithmetic-metrics
modules have complete measured statement/branch coverage; this does not imply
the absent full backtester or statistical suite is tested.

The candidate's committed-SHA CI and downloadable engineering artifacts are
available from [PR 4](https://github.com/isaacgu/tradebot/pull/4). CI must be green
on the current tested SHA before treating its build as verified; neither CI nor
the synthetic calibration artifact certifies FBS data or approves a phase gate.

### Completed local synthetic demonstration

The first and second runs under
`build/calibration-engineering-20261015-v1/{first,second}/` completed and their
report bytes matched with `cmp`. Both carry `git_sha=UNCOMMITTED` and source-file
hashes. Report SHA-256:
`4c1a50735cc57197c3c2c3625dbb2e7acceea62fd03da3982017f0162af5ba8f`.

Each invented instrument produced **nine training samples and three frozen
validation forecasts**. There were **two synthetic fitted scalars, zero
market-data models**, no release/registry authorization claim, no PnL and no
execution. These very small samples test mechanics only.

| Artifact | SHA-256 |
|---|---|
| Synthetic/EURUSD calibration | `cdd7fc9354e4e33477b103486937a4f1d43f0abf12da01add35eab833631c0b5` |
| Synthetic/GBPUSD calibration | `a9d8bc575025863768e04fcbed0e2a54798716452789ea48e9732ef3af60a604` |

The report is stored at
`build/calibration-engineering-20261015-v1/first/reports/<report-SHA>/report.json`;
models are under that run's `models/<model-SHA>/calibration.json`. The second
run reproduces the same report and model identities. Later committed-SHA CI
artifacts intentionally have their own provenance and are not these local bytes.

## Conditions before actual training

1. Resolve the historical timestamp/session facts and flagged-quote/reversion
   interpretation with source-backed evidence. Preserve the unsuccessful run;
   any justified correction needs a separately versioned rebuild on the same
   frozen acceptance scope, followed by genuine Gate-1 acceptance and bindings.
2. Complete the remaining backtester and statistical integration, broker-backed
   costs and arrival-time order-intent parity. Define a separately reviewed,
   narrow Gate-2 validation release so the real-data zero-edge test does not
   depend circularly on an already-approved Gate 2. No bypass was added here.
3. Supply the required deep history, stress and tick-fidelity package, preregister
   the economic hypothesis/splits/trial accounting, and issue the exact
   purpose-scoped snapshot through the independently pinned trusted registry.
4. Review historical provenance semantics. The current strict strategy still
   suppresses TS_RECV_IMPUTED; all-suppressed data fails fitting with no default
   scale. This increment does not silently strip that flag or backdate decisions.

These are not discharged by a successful unit test or general approval. The
[prepared FBS inquiry](fbs-historical-calendar-inquiry.md) remains available; this
turn did not send a message, acquire replacement data, change a broker account,
enable execution, sign a gate or start a market-data training worker.

## Handoff

The pre-existing edits to `HANDOFF.md` and
`docs/reports/corrected_new_minute_context_20260906.md` are preserved and excluded
from this increment's commit. Raw data and build artifacts remain local. The
candidate is published to the existing feature branch/PR, not directly to master.
See [ADR-0015](../adr/0015-conservative-synthetic-order-matching.md) and
[ADR-0016](../adr/0016-training-only-forecast-calibration.md) for explicit assumptions.
