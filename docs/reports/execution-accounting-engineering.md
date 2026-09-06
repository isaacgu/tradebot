# Execution and accounting engineering increment

Date: 2026-09-05. Evidence class: **ENGINEERING_ONLY**.

## What is runnable

The new `tradebot.backtest` package provides a synthetic MARKET/GTC tick matcher,
exact supplied-cost round-trip accounting, and a registered offline known-answer
runner. It uses the existing immutable order/fill/tick contracts and engineering
registry. No broker adapter, live/paper order path or collected data is used.

The existing Engineering Replay dashboard remains a decision-replay viewer. It
does not automatically display this separate execution/accounting artifact, and
this runner never changes its latest pointer.

After additive source publication, run from the repository in the pinned Linux
environment (use a fresh attempt ID for every retry):

```sh
.venv/bin/python -m tradebot.backtest.execution_demo \
  --output-root build/execution-smoke-local \
  --attempt-id first \
  --git-sha e8149638373f08f38c8adcc356ae81be0fc404b9
```

`git_sha` names the recorded base commit; exact module hashes additionally bind
the new source bytes, including modules not yet committed at staging time. Use
the corresponding current commit marker for a later source version. The default
`UNCOMMITTED` is an honest development marker, not a release identity.

The CLI accepts no market-data path, strategy parameters or trading switch. It
prints the registry audit and report digest; the report is saved at
`<output-root>/artifacts/<report-sha256>/report.json`. Inspect the caveats and verify
the digest before relying on it. A repeated identical run has identical report
bytes, but still records a separate attempt. A reused attempt ID is rejected.

## Known answers, not investment results

All figures below are deliberately invented unit-test cashflows. Each case uses
10,000 synthetic EUR units, observed fixture bid/ask spreads, 0.00002 adverse
slippage per fill, USD 0.35 commission per fill and explicit zero intraday financing.
The direct USD/ZAR conversion quote is invented at bid 18.00 / ask 18.02. These are
neither FBS terms nor proposed position sizing or account settings.

| Scripted case | Midpoint gross USD | Net after supplied costs USD | Executable net ZAR |
|---|---:|---:|---:|
| Long, favorable move | 10.50 | 6.90 | 124.200 |
| Long, adverse move | -9.50 | -13.10 | -236.062 |
| Short, favorable move | 9.50 | 5.90 | 106.200 |
| Short, adverse move | -10.50 | -14.10 | -254.082 |

Each quote-currency round trip has spread 2.50, slippage 0.40 and commission 0.70.
The report exposes those components plus financing and conversion costs. Money
is not rounded prematurely to minor units; the only rounded field is cost ratio.
Matching rejects the tick exactly at the 150 ms boundary and uses the first tick
strictly after it. Its later receipt/availability stamp is preserved for delivery.

## Verification

- 272 new tests: 88 matching, 170 accounting and 14 integration checks.
- 534 tests passed when combined with 262 existing causal-replay/research controls;
  warnings are treated as errors. New package branch-aware coverage: 98.23%.
- Ruff lint/format, strict mypy for all 7 new source/test files, and Bandit passed.
- Independent review found no actionable blocker within this explicit scope. It
  additionally checked 2,000 seeded long/short cases against exact rational
  arithmetic under hostile Decimal settings and reviewed extreme supported inputs.
- Negative tests cover unsupported orders before feed access, malformed/flagged
  trailing data, backwards time, early submission, missing or invalid costs,
  incorrect fill/accounting closure, fixture mismatch, source drift, no fill,
  corrupt existing artifacts and retained START/FAILED evidence.
- The first successful staged run and sequential repeat produced the same report;
  a deliberate provenance mismatch produced a retained FAILED attempt, no artifact
  and no false completion. There were 2 completed, 1 failed, 0 incomplete attempts
  in that ledger. No broker orders were sent.

Staged proof: `build/execution-engine-verification-20260905/proof.json`, SHA256
`b64700b5c942183f28ae6232734c1c79325c540ad3fe0b3eeacb079cf97184f2`.
An initial strict-mypy test-import failure is retained in its checks log and was
corrected before the passing run; it was not a trading/strategy experiment.

The full verification was repeated with a dedicated coverage database to isolate
concurrent tasks: `build/execution-engine-verification-isolated-20260905/proof.json`,
SHA256 `8da291a3f521f60535f6b38f1930ad1aa0c885c963b2d5951a6fa23c8dd150a9`.
That isolated run passed all 534 tests in 71.15 seconds at the same 98.23% coverage,
and its separate ledger also retained 2 completed and 1 deliberately failed attempt.
The repeated report hashes matched both within and across the two proof roots.

Experiment ID:
`b8ce813ed6a5b57fa593fa19d2ba5f86b6bf945addb8ec0478c717cabcd092e7`.
Report SHA256:
`43ab461daa31954727fe7f2b014f53e51be3a38baad55288545899ec94d0120a`.
Fixture SHA256:
`4c26803a5d0c458c5ef4fa148669c92df5bc77f94c6d233588bd26ac9d450ed3`.

These are local staged evidence, not an assertion that an external CI/release or
canonical-source verification has passed. Additive publication is coordinated
separately with the platform task. See ADR 0012 for the numerical envelope and
failure/durability boundaries.

## What remains before a complete engine

This implements one execution/accounting foundation, not all of Phase 2. Full
SimBroker and strategy/portfolio/risk/OMS integration, limits/stops/brackets,
sessions/margin/rejects, financing calendars, calibrated execution/cost models,
admitted-data enforcement, zero-edge and arrival-parity tests, reconciliation,
statistical validation and the existing trading gates remain required.

The report marks full `costs_modelled=false`, economic evaluation `NOT_PERFORMED`,
execution disabled and no gates approved. Synthetic PnL is itemized only to test
arithmetic. It must not be interpreted as strategy profitability or readiness to
risk real money.
