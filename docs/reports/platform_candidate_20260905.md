# Platform delivery — September 5, 2026

The Principal requested that verified changes be committed and that engine development
proceed toward a runnable system today. This authorizes development and a normal feature
PR; it does not replace technical acceptance or authorize live trading. The USD 1,000
demo profile continues to have execution disabled.

## Deliverable and operating state

- Whole-system Grafana views: system, acquisition, data quality, engineering replay,
  and actual broker positions/orders. Broker observation is read-only; manual or external
  trades are not attributed to the bot.
- Immutable data pipeline, source admission/quarantine, point-in-time calendar storage,
  reference-month evaluator, and separately preserved human approval statements.
- Runnable causal decision replay and engineering research controls. The staged next
  increment adds synthetic execution/cost-accounting tests, not a production broker,
  portfolio/risk/OMS stack or accepted strategy.
- All four localhost health endpoints returned HTTP 200 during this delivery check.
  Exporter health proves the HTTP service is up, not that an account snapshot is fresh.

Run the existing decision engine from the repository root in the pinned environment:

```bash
uv run --no-sync python -m tradebot.research --synthetic
```

This is a finite, invented-data engineering run, not an always-on trading loop.
The [engine runbook](../runbooks/core-engine.md) documents the Windows/WSL command,
artifacts and explicit clean-snapshot input contract. The [dashboard runbook](../runbooks/local-dashboard.md)
documents service startup and recovery. MT5 can remain open and minimized for observation.

## Verification and publication

The previous pre-use snapshot passed 962 tests, static checks, coverage tiers and
dependency checks; see [the exact pre-use evidence](preuse_preparation_20260905.md).
The fresh candidate rerun also passed **962 warnings-as-errors tests**, 87.20% total
coverage, the 96% core / 86% non-core tiers, Ruff formatting/lint, strict mypy,
Bandit and pip-audit. Two Gate-0 demo manifests and canonical metric digests matched.
The tested source/configuration inventory did not change during these checks.
Evidence: `build/preuse/local-checks-20260905T095614Z/result.json`, SHA-256
`f45c24325f552ba5606af8d7aa571eaf1871e98ff5c18a79e32b8e77f7036c10`.
Expected documentation-only edits were recorded separately in
`build/preuse/candidate-audit-20260905T095602989839Z/`.

Publication hygiene checks passed for the initial 108 changed/untracked files
(13,294,039 bytes) and 14 reachable commits: checksum-pinned Gitleaks 8.30.1 found
no secrets, and structural privacy checks found no account/credential identifiers
or raw/binary payloads. The added/updated delivery documents receive a separate
final delta check before staging. Generated metadata reports are not raw tick
files; raw data and runtime artifacts remain Git-ignored.

No historical CI result is treated as covering uncommitted source changes. A feature
PR is not a merge or gate approval; its actual URL/SHA and results are supplied in the
delivery message and PR checks, without claiming this pre-commit record is a signature.

The earlier broader security review is **incomplete**, not a clean security scan.
It raised unvalidated resource-exhaustion hypotheses for deliberately malformed
operator-selected gzip, decimal and Parquet inputs, plus a diagnostic calendar-size
hypothesis. Discovery did not establish final findings or severity, and no mitigation
is claimed here. Only the project's existing pinned, locally retained inputs are in
scope for present QA; do not accept arbitrary downloaded checkpoint/snapshot bundles.
The local dashboard must remain loopback-only. These operational limitations do not
substitute for completing validation of the hypotheses.

## Gate 1 and the alternative evidence route

Broker permission, broker trading hours, expected liquidity and source timestamp
semantics are separate questions. Preserve original source ticks and failed evidence.
Do not choose a timestamp offset or exclude flags merely to obtain a passing result.

Engineering can proceed on invented fixtures while the following acceptance work is
resolved: dated expected-liquidity coverage, a reviewed flag/denominator policy, the
complete reference-month clean corpus and unchanged strict `<0.1%` evaluation, five
human bar checks, committed-candidate CI and final evidence-bound human decisions.
The [approval guide](gate1_approval_guide.md) remains the authoritative route.

A documented, human-reviewed expected-liquidity policy based on cited primary
sources can be proposed as an alternative to waiting indefinitely for FBS support.
It must identify its scope, effective dates and limitations, and must not pretend to
be an FBS guarantee. It remains unapproved until the required explicit review; the
existing draft policy and calendar-unknown status are not silently activated.

The September 5 primary-source search found FBS's
[October 25, 2024 winter-time notice](https://fbs.com/news/trading-schedule-changes-due-to-the-winter-time-shift).
It identifies the October 27 Europe/UK and November 3 US transitions, but its named
affected instruments do not include EURUSD or GBPUSD. It is historical context,
not their complete trading-hours calendar. The current
[MetaQuotes Python tick API documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py)
specifies UTC returned timestamps, supporting the unchanged-epoch baseline, not a
best-fit offset. Publication dates on pages retrieved now are not independent
immutable archive captures or proof of historical broker correctness.

One concrete calendar proposal for review is the SPEC's documented high-liquidity
London/New York overlap, approximately **13:00–16:30 Europe/London**, applied through
date-aware timezone conversion. This is a proposed project QA window, not an FBS
session fact. Before use, humans must settle instrument-specific holiday/date rows,
exact interval definitions, missing-minute treatment and every counted flag class.
Record the actual 2026 adoption/availability date and retrospective October 2024
scope; never backdate knowledge. Do not evaluate multiple windows and select the
passing one. No proposal is activated by this document.

No deadline changes the requirement for cost validation, risk controls, order
management, reconciliation, kill switches and later paper/live gates. The achievable
current delivery is a running observation platform and tested offline engine increments.
