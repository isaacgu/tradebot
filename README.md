# Tradebot

A phased, research-to-live systematic trading platform for FX majors and equity indices.
The deterministic foundation is approved and Phase 1 data engineering is underway. The current
FBS demo connection measures and acquires historical data; it does not place orders. There is no
bot-generated strategy-performance, PnL, or live-trading evidence yet. A read-only broker view
can show existing account activity without attributing that activity to this bot.

## Current gate status

September 5 delivery status: the local dashboard and read-only broker observer are
running; the decision-replay engine is runnable with the synthetic command below.
Data admission and the reference-month evaluator are implemented, but do not approve
training data or trading. The next engine increment is an offline simulated-fill and
cost-accounting harness. It is not a connected paper/live broker. See the
[current delivery record](docs/reports/platform_candidate_20260905.md) for verification,
publication status and the remaining acceptance decisions.

Gate 0 is **APPROVED**. The specification is frozen at **v1.0** (SHA-256
`dccdcbd9…1689f37`, errata enumerated in [ADR-0004](docs/adr/0004-freeze-errata.md), supplied draft
preserved unmodified at `docs/SPEC-supplied-2026-09-03.md`), and the eight Phase-0 interpretation
questions are answered and adopted. Isaac Gumbi recorded Principal approval on 2026-09-04, and
[CI run 33852037018](https://github.com/isaacgu/tradebot/actions/runs/33852037018) passed both
required jobs on committed candidate `4de5f7a540ed216b3568141bd83392af3189c3cf`. At the Principal's
direction, the repository is temporarily public so GitHub can enforce the `master-release-gate`
ruleset without a Pro subscription. Delsa Mashiki completed the independent human review and
approved on 2026-09-04. All five Gate-0 evidence categories are now `PROVIDED`; the P0 foundation is
certified. Phase 1 has since begun under its separate assignment; Gate 1 remains pending. Gate-0
approval does not approve later phases or enable trading. See the
[Gate-0 evidence pack](docs/reports/gate0_evidence.md) for the row-by-row status.

## How the whole bot fits together

The intended system follows one path from observations to decisions, with evidence required
before each new capability becomes eligible for use:

```text
Source observations → immutable raw data → quality checks / clean data → closed bars
                                                                    ↓
Point-in-time calendar → causal features → strategy research → backtests and validation
                                                                    ↓
                         Portfolio targets → risk checks → order management → broker
                                                                    ↓
                         Reconciliation / costs / recovery ← fills and account state

Every stage → structured logs, Prometheus metrics, reproducible artifacts → Grafana
```

The same strategy, portfolio, risk and order-management code is intended for backtest, paper and
live modes. Only the feed, broker and clock change. Those later trading components are not yet
implemented. The Gate-0 `HelloStrategy` demonstrates wiring; the separate
[core-engine engineering preview](#core-engine-engineering-preview) now exercises
causal features and auditable FX forecasts, without financial evaluation or execution.

| Area | Current position | What becomes visible as it is implemented |
|---|---|---|
| Foundations | Gate 0 approved | Event processing, rejection/failure counters, config/code identity |
| Acquisition and data | Phase 1 underway | Runner health, checkpoint progress, source coverage, raw integrity, gaps, flags and rebuild evidence |
| Broker observation | Read-only local integration; separately verified broker delta | Actual account state, open positions, pending orders and quotes; no bot attribution or trading controls |
| Calendars | Storage/query infrastructure; authoritative calendars still required | Field vintages, knowledge cutoffs, dated liquidity expectations, unknown/expired coverage |
| Backtesting | Phase 2 pending | Reproducible runs, cost assumptions, fills, after-cost results and validation |
| Research and strategies | Phase 3 pending; replay and synthetic research-control previews implemented | Replay provenance, engineering attempt ledger and chronological split controls now; economic trials, statistical validation and strategy gates later |
| Portfolio, risk and execution | Phase 4 pending | Allocations, exposure, limits, halt state, order lifecycle and reconciliation |
| Paper and live operation | Later gates pending | Actual uptime, fills, measured costs, PnL, alerts and recovery drills |

Index CFDs remain `data_only` with trading disabled. Each venue has its own series; data from
different brokers is never silently merged. Historical economic revisions retain separate
availability timestamps per field and vintage, so a past query cannot see a later revision.

## Dashboard: the whole system, with data and broker detail

Start the local observability stack from PowerShell:

```powershell
.\scripts\start_dashboard.ps1 -Gate1Report 'build/gate1/30day-stable-b102ecdd/report.json'
```

Open the [system overview](http://localhost:3000/d/tradebot-system), then use the
[acquisition drilldown](http://localhost:3000/d/tradebot-acquisition) for the current data run,
[data-quality view](http://localhost:3000/d/tradebot-data-quality) for corpus evidence, or
[Broker & Trades](http://localhost:3000/d/tradebot-broker) for read-only terminal observations.
[Engineering Replay](http://localhost:3000/d/tradebot-research) shows a verified completed
decision replay: artifact state, source class, bars processed and decision-status counts.
The overview is the entry point for evaluating the whole bot: health, data, research/backtests,
strategies/portfolio, execution/risk, evidence and operations. Sections for future modules must
say **not implemented** or **no evidence**. A missing metric is not a healthy system, a zero
position, or zero PnL.

The current acquisition panels read saved checkpoints and local process state. They distinguish
a connected terminal from an active acquisition worker and show whether a report is older than
the checkpoints. A downloaded chunk is progress, not proof of clean data or successful trading.
The initial corpus is a bounded source-viability probe; it is not Gate-1 acceptance evidence by
itself, and it is not the full strategy-research history.

Acquisition resumed on 2026-09-05 at 05:31 UTC. At 06:50:41 UTC (08:50 SAST), the
local exporter reported retrieval complete: 190/190 checkpoints and 42,796,598 ticks,
with zero invalid checkpoints or fetch errors. The worker had stopped; this is a
retrieval-status observation, not final source-viability or Gate-1 acceptance. The
acquisition task owns final report/sidecar verification. The earlier stop's
cause remains unconfirmed. The separate first 30-day rebuild was stopped after a
dependency-hash mismatch; its ineligible artifacts remain preserved at `build/gate1/30day`.

The replacement at `build/gate1/30day-stable-b102ecdd` finished at 23:30 UTC on
September 4 (01:30 SAST September 5). Two rebuilds of 30 sampled EURUSD dates,
6,646,477 ticks and 41,701 one-minute bars are byte-identical; all 31 raw files and
27 declared implementation files passed independent hash checks. This proves
reproducibility, not clean or tradable data: quality is **FAILED** because of 34
retrospective price-outlier annotations, and expected-liquidity coverage remains
unknown. Gate 1 is still not approved. See the [evidence pack](docs/reports/gate1_evidence.md)
for the exact report, independent audit and remaining acceptance criteria.
The later pre-use verification passed 962 warnings-as-errors tests, 87.20% overall
coverage and the 96%/86% core/non-core coverage tiers. Its exact scope and retained
evidence are in [pre-use preparation](docs/reports/preuse_preparation_20260905.md).
Local checks are not committed-candidate CI or Gate-1 acceptance.

On September 5 the Principal authorized pursuing FBS clarification and continuing
provisional offline diagnostics in parallel. Work need not wait for a broker reply,
but no calendar, timestamp correction, quality threshold or Gate-1 requirement is
waived. The [prepared FBS inquiry](docs/reports/fbs-historical-calendar-inquiry.md)
has **not been submitted by the assistant**. Isaac separately reports that FBS permits
his account-holder data use for this personal project; see the
[permission record](docs/reports/fbs_data_permission.md). No duplicate permission
request is needed. A historical session/timezone answer remains unavailable.
The new offline diagnostic
is complete and separately verified: 48 preserved source windows, 11,128,900 ticks,
and a true October session-close-date view for both FX symbols. Its advertised-session
comparison is **not** approved liquid-hours coverage or a timestamp correction; it
does not change the FAILED quality result or approve Gate 1. The new 735-test run
includes this helper's tests; earlier 704-test evidence remains separately retained.

The launcher allows up to 300 seconds for cold-start readiness by default; use
`-StartupTimeoutSeconds` for an explicit 15–600 second limit. A timeout leaves
recorded processes running. Check their health before retrying; the launcher reuses
identity-checked services. The [runbook](docs/runbooks/local-dashboard.md) explains
selective restarts and report-path changes.

The Broker & Trades integration has passed scoped local observer and query checks. It observes the MT5 terminal in the
background: actual account kind, account state, open positions, pending orders and quotes. These
can include manual or external activity and remain **not bot-certified**. Broker-reported profit
is not an after-cost strategy backtest or proof of this bot's performance. No trade can be opened,
modified or closed from the dashboard. Check snapshot age and connection/error state before
interpreting the values; a stale or unavailable snapshot is not an empty account. Account
identifiers, names, comments and credentials are not published. After intentionally switching
accounts in MT5, restart only the broker observer; its prior snapshot stays stale until then. See the
[local dashboard runbook](docs/runbooks/local-dashboard.md) for setup and recovery.

The Engineering Replay view verifies a published report without starting a replay or reading
its detailed trace. Missing or rejected reports are unknown, not zero activity. Its initial
320-bar sample is explicitly synthetic engineering evidence; forecast counts are not live calls,
trade orders, calibrated probabilities or performance. Immutable-clean-snapshot replays also
remain engineering-only until the relevant data, financial and strategy gates are satisfied.

Later modules connect to the same observability path: publish metrics with stable, non-sensitive
labels; emit structured logs tied to a run; write report artifacts with code/config/data identity;
and add the relevant Grafana panels and links. Prometheus exposition is the machine-readable
evidence; screenshots supplement it. Grafana is a viewing surface, not an order-entry interface.

## What is still needed for Gate 1

Start with the [Gate-1 approval guide](docs/reports/gate1_approval_guide.md). The actual
human decision documents are the [independent reviewer sign-off](docs/reports/gate1_independent_review.md#final-reviewer-decision)
and [Principal sign-off](docs/reports/gate1_principal_approval.md#final-principal-decision).
Isaac's approval and his report of Delsa Mashiki's approval are preserved in the
[approval receipt](docs/reports/gate1_approval_receipt_20260905.md). The final
evidence-bound records are incomplete: technical evidence, current committed-SHA CI
and five documented human bar checks remain outstanding. An agent cannot complete
those personal checks or invent either signature.

- Review the completed 30-day reproducibility evidence and investigate the quality findings
  without altering raw observations or silently changing the flag policy.
- Supply authoritative, dated expected-liquidity calendars and a reference month sufficient to
  measure the frozen liquid-hours flagged-bar criterion. Unknown coverage remains indeterminate.
- Hand-verify the five prepared venue-matched reference bars and supply final DST/quality-test
  evidence. The automated checks are not human signatures.
- Resolve the reference-month counted-flag definition and denominator explicitly. The live
  dashboard and inherited screenshot/checker obligations are delivered, not quantitative acceptance.
- Obtain committed-SHA CI evidence and separate independent human and Principal sign-offs.

The evidence checker validates document structure, frozen SPEC identity, local artifact hashes
and carried obligations. It cannot authenticate human signatures or approve a gate:

```bash
uv run --no-sync python scripts/check_evidence.py docs/reports/gate1_evidence.md
```

An incomplete pack returns nonzero with explicit reasons. Historical CI artifacts must be restored
at their recorded paths before their hashes can be verified locally. Installing the checker or
viewing a dashboard alone does not discharge all Gate-1 criteria. The deeper multi-year history
and mandatory stress windows are a separate entry requirement for each Phase-3 strategy.

The calendar integration example imports four fields from the
[Federal Reserve's November 7, 2024 release](https://www.federalreserve.gov/newsevents/pressreleases/monetary20241107a.htm),
retains the source bytes and retrieval hash, reopens the store, and verifies queries immediately
before and after each field becomes available. Run it with:

```bash
uv run --no-sync python scripts/validate_gate1_calendar.py
uv run --no-sync python scripts/validate_gate1_calendar.py --replay-report docs/reports/gate1_calendar.json --output build/gate1/calendar-replay.json
```

The [calendar report](docs/reports/gate1_calendar.json) separates actual imported observations from
synthetic revision tests. The source's declared release time does not prove historical vintages:
these newly retrieved fields remain unavailable before retrieval. This example supplies neither
a complete economic calendar nor approved historical FX liquidity dates.

## Clean-machine setup

Prerequisites: GNU Make, Git, and uv 0.12.9. Install that exact uv release with the official
versioned installer (PowerShell example), then let uv install the pinned managed Python build:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/0.12.9/install.ps1 | iex"
```

Then run in PowerShell, bash, or another shell where `uv` and `make` are available:

```bash
uv python install 3.12.14
uv lock --check
uv sync --locked --extra dev
uv run --no-sync make check
```

The lockfile is authoritative. CI uses the same frozen resolution. A pip-compatible developer
fallback is available when diagnosing a local uv installation, but it is not gate evidence:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pytest
```

## Gate-0 demo

```bash
uv run --no-sync make demo
```

`make demo` runs the same non-tradable `HelloStrategy` and event pipeline twice:

- backtest wiring: fixed pull feed + `SimClock`;
- paper wiring: fixed arrival-order feed + guarded `WallClock`.

Both consume three already-closed synthetic bars. The command exits non-zero unless their
canonical forecast traces match, writes `build/gate0/demo-manifest.json`, prints structured run
logs, and labels the artifact as smoke-test evidence only. It performs no real sleeping or
network access.

Individual verification commands:

```bash
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync mypy
uv run --no-sync python -W error -m pytest
uv run --no-sync coverage report --include="src/tradebot/core/*" --fail-under=90
uv run --no-sync coverage report --omit="src/tradebot/core/*" --fail-under=80
uv run --no-sync bandit -q -r src
uv run --no-sync pip-audit
```

## Design constraints already enforced

- Platform-defined events are frozen, slotted, UTC-only objects. Prices and quantities at
  boundaries are `Decimal` values.
- The bus rejects an event when either its market timestamp or observation timestamp is later
  than the injected clock, and revalidates structurally typed events immediately before use.
- Handlers run by registration order; re-entrant events append FIFO; a handler exception halts
  dispatch and propagates.
- `SimClock` cannot regress. `WallClock` compares elapsed wall and monotonic time and raises on
  backward or excessive step changes.
- YAML is loaded safely, duplicate and unknown keys fail startup, and canonical resolved config
  is hashed with SHA-256.
- Strategy context exposes only a read-only clock—not a feed, broker, wall clock, or mode flag.

See [interfaces](docs/interfaces.md), [ADRs](docs/adr/), and the
[approved Gate-0 evidence pack](docs/reports/gate0_evidence.md).

## Core engine engineering preview

The separate decision-replay engine now provides causal multi-horizon features,
independent FX strategy state, checkpoint recovery and a complete decision audit.
Run its fixed synthetic demonstration with:

```bash
uv run --no-sync python -m tradebot.research --synthetic
```

Outputs live under `build/research/decision-replay/`: a versioned summary, hashed
decision log and latest-run pointer for the UI. This is engineering preparation
while Gate-1 evidence is collected; forecast scaling is uncalibrated, costs/fills
and PnL are absent, and no trading or gate approval is enabled. The
[core-engine runbook](docs/runbooks/core-engine.md) documents snapshot inputs,
decision reasons, verification and the remaining implementation stages.

The September 5 additive P3 preparation increment adds an immutable engineering
experiment declaration, failed/interrupted attempt accounting, chronological
training/validation windows, embargo and boundary purging, and denied lockbox
execution. Its fixed synthetic runner is separate from the existing dashboard
report and does not accept market data or create orders:

```bash
uv run --no-sync python -m tradebot.research.experiment_demo --output-root build/research-controls-demo --attempt-prefix review-001
```

Use a new attempt prefix on retries and run initial ledger creation sequentially.
This is engineering scaffolding, not an untouched market-data lockbox, a financial
backtest or P3 completion. See the [research-control engineering report](docs/reports/p3-research-controls-engineering.md)
and [ADR 0011](docs/adr/0011-engineering-research-controls.md) for its limitations.
After publication, the canonical local suite passed **872 warnings-as-errors tests**
(88.40% coverage), Ruff formatting/lint, strict mypy, Bandit and the 96%/87%
core/non-core coverage tiers. The log is `build/p3-root-verification-20260905/checks.log`.
These are uncommitted local results, not CI or phase-gate approval; the existing
Gate-1 evidence pack and dashboard replay pointer remain unchanged.
