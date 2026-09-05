# ADR 0012 — Synthetic market execution and exact supplied-cost accounting

Date: 2026-09-05. Status: engineering decision; additive publication coordinated
with the platform task. This does not approve a phase gate or enable trading.

## Context and acceptance criteria

The causal replay and engineering research registry do not simulate executions.
SPEC 6.2–6.3 require realistic quote sides, execution latency separate from data
availability, and itemized costs. Implement a bounded, testable foundation without
admitting collected market data or implying that incomplete backtests are evidence.
The plan and known-answer tests were written before implementation.

Success means strict post-latency venue-time fills, complete input validation,
deterministic attribution, long/short cashflow closure, correct profit/loss FX
conversion, exact money independent of ambient Decimal context, retained failures,
and byte-identical repeat reports. Unsupported capabilities must fail explicitly.

## Decision

- `backtest/market.py`: pure MARKET/GTC matching for a finite, single-instrument
  `Synthetic/<six-letter FX pair>` tick stream. Submission cannot precede the
  supplied decision-availability stamp. Select the first venue tick strictly
  after submission plus latency; buy ask plus adverse slippage, sell bid minus it.
  Never add receipt delay or apply the bus admission gate inside the matcher.
  Preserve quote timestamps on the resulting immutable `Fill`; future callers
  must admit it by availability before delivery to a strategy.
- Validate the entire stream before returning, including the tail after a fill
  candidate. Reject wrong instruments, flags, malformed entries and backwards
  venue time. Equal timestamps use input ordinal order, never implicit sorting.
  Reject non-MARKET, non-GTC, attached SL/TP and quantities above the explicit
  synthetic full-fill bound before acquiring input. No partial fill is invented.
- Bind full order attribution, submission/decision stamps, model, matched quote
  and ordinal into a deterministic fill ID. Decimal coefficient/exponent encoding
  is independent of ambient formatting. This is repeatability, not durable OMS
  duplicate prevention, reservation of liquidity, or a Broker implementation.
- `backtest/costs.py`: explicit two-fill, one-quantity input. Midpoint gross less
  spread, adverse slippage and commissions plus signed financing gives quote net.
  Separately calculate actual side-correct fill prices and price PnL. Spread and
  slippage are already in price PnL and must never be charged a second time.
- All charges, including zero financing, must be supplied. For a direct foreign
  currency/account currency quote, translate attribution lines at mid, convert
  the terminal signed net at bid for profit or ask for loss, and expose the
  conversion difference as a separate cost. `None` explicitly declares the
  same currency. Gross minus all cost lines reconciles exactly to account net.
- Cash arithmetic is exact or raises. Inputs are bounded to 1,000 coefficient
  digits and exponents ±1,000 for implementation resource control, not risk policy.
  Market prices use integer coefficients; accounting uses an isolated 16,384-digit
  context with rounding traps. Only cost ratio rounds to 28 significant digits,
  half-even. Zero gross yields `None`; negative-gross ratios are arithmetic, not
  a pass of the positive-gross cost-fragility rule.
- `backtest/execution_demo.py`: four fixed invented long/short gain/loss cases,
  eight scripted orders/fills, no strategy selection or broker calls. Reuse the
  engineering registry to bind the exact fixture, parameters, SPEC, source hashes,
  commit marker and runtime; persist START before calculation and retain failure.
  Assert executed fill prices equal accounted prices. Publish a content-addressed
  report, verify any existing artifact before reuse, and leave no latest pointer.

## Explicit exclusions and consequences

This is an `ENGINEERING_ONLY` execution/accounting smoke test, not a full
backtester or trading-ready engine. It is not integrated with strategy/portfolio/
risk/OMS, nor does it establish event-driven code parity or paper/live behavior.
The same immutable core messages are reused, but that alone is not parity.

Latency, maximum full-fill quantity, slippage, commissions and conversion rates
are invented, uncalibrated fixture assumptions. The slippage helper is not the
full spread/size/ATR/jump model. Supplied financing is not a broker rollover rate
table, calendar, dated accrual, or claim that real overnight financing is zero.
No limit/stop/stop-limit/bracket, session/margin rejection, broker liquidity,
multiday settlement, inverse/triangulated FX, dividends or futures rolls exist.
The terminal FX conversion is not a broker multicurrency cash ledger.

Reports itemize synthetic cashflow but set full `costs_modelled=false`, declare
economic evaluation not performed, data acceptance not asserted, and claim no
gate approvals. They cannot be used as profitability evidence. No data snapshot
is consumed; no collection state, risk threshold or trading authorization changes.

The existing registry's local chain is not externally timestamped or resistant
to coordinated rewrite. Initialization should be sequential. Artifact publication
and registry completion are separate resources: interruption may leave an orphan
artifact and incomplete START, and power-loss transactional durability is not
claimed. Verify artifact hashes after recovery before relying on completion.

## Verification and next boundary

See `docs/reports/execution-accounting-engineering.md` for concrete checks and
artifact identities. Next integration requires separately reviewed broker facts,
admitted input selection, complete fill types/costs, risk/OMS and reconciliation,
arrival-driven parity and the standing statistical and phase-gate criteria.
