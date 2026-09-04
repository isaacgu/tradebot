# MASTER BUILD SPECIFICATION — Systematic Trading System for FX Majors & Equity Indices
## Primary instrument: GBP/USD ("Cable"). Secondary: EUR/USD, US500, UK100, GER40.

**Document type:** Agent-executable build specification / master prompt
**Version:** 1.0 — 2026-09-03
**Owner:** Isaac (Principal / Product Owner)
**Status:** DRAFT FOR REVIEW → to be frozen at v1.0 before agents begin Phase 0

---

## 0. HOW TO USE THIS DOCUMENT (READ FIRST — ALL AGENTS)

This file is the single source of truth for the build. It is written to be handed, in whole or in part, to autonomous or semi-autonomous coding/research agents. Every agent that receives it MUST:

1. **Read Sections 0–3 in full** before doing anything else. They define the philosophy, non-negotiables and architecture that all other work must respect.
2. **Read the section(s) for its assigned role and phase** (Section 14 maps roles → sections).
3. **State assumptions explicitly before writing code.** If the spec is ambiguous, name the ambiguity, propose 2–3 interpretations, pick none silently. Escalate to the Principal.
4. **Produce an ADR (Architecture Decision Record)** for any non-trivial design decision (template in Appendix D).
5. **Define verifiable success criteria before implementing** ("write the test that fails, then make it pass"). "Make it work" is not a success criterion.
6. **Write the minimum code that solves the assigned task.** No speculative abstraction, no unrequested configurability, no error handling for impossible scenarios. If 200 lines could be 50, rewrite.
7. **Make surgical changes** to existing code. Touch only what the task requires. Match existing style. Report unrelated dead code; do not delete it.
8. **Never bypass a phase gate** (Section 13). A strategy that has not passed Gate 3 does not get a broker adapter. A system that has not passed Gate 5 does not trade real money.
9. **Treat all numeric thresholds in this document as defaults that must be justified or changed via ADR**, not as magic numbers to be copied.
10. **Never claim profitability.** Claim only what tests and out-of-sample evidence show.

### 0.1 Interpretation notes from the Principal's brief
- "usd/gbs" is interpreted as **GBP/USD** (Cable), quoted as USD per 1 GBP. If USD/ZAR or another pair was intended, change `configs/instruments.yaml` only — nothing else in this spec depends on the specific pair.
- "Indices" is interpreted as **equity index CFDs and/or futures** (S&P 500, FTSE 100, DAX 40, Nasdaq 100, Dow 30). Design must support both CFD and futures representations of the same underlying.
- "Best bot" is interpreted as **best-in-class process and engineering**, not a promise of returns. See Section 1.3.

### 0.2 Document conventions
- `MUST` / `MUST NOT` = hard requirement, violation fails the phase gate.
- `SHOULD` = strong default; deviation requires an ADR.
- `MAY` = optional.
- `[VERIFY]` = a factual claim that the implementing agent must independently confirm against a primary source (broker docs, exchange specs, regulator) before relying on it. This spec was written without live access to broker documentation; contract specifications, API details and regulatory facts change.
- All times are **UTC** unless stated. The Principal operates from South Africa (SAST = UTC+2, no DST). Session tables give exchange-local time; agents MUST implement DST-aware conversion (Section 3.4).

---

## 1. MISSION, SCOPE, AND OPERATING PHILOSOPHY

### 1.1 Mission
Build a production-grade, research-to-live systematic trading platform that:
- Ingests, validates and stores high-quality tick and bar data for FX majors and equity indices.
- Enables rigorous, bias-controlled strategy research with a backtester that shares code paths with live execution.
- Runs a **portfolio of uncorrelated strategies** under a centralised risk manager with hard kill switches.
- Executes on one or more retail/institutional brokers via robust, reconciled, idempotent order management.
- Is observable, recoverable, auditable, and safe to leave unattended overnight.

### 1.2 Scope
**In scope (v1.0):**
- Instruments: GBP/USD (primary), EUR/USD, US500, UK100, GER40. Architecture MUST be instrument-agnostic.
- Timeframes: 1-minute to daily bars; tick data stored for fill simulation and bar construction. No sub-second/HFT ambitions.
- Strategy families: trend/momentum, mean reversion, breakout/session, volatility, cross-instrument relative value, event-aware overlays, ML meta-labeling.
- Backtesting with walk-forward and combinatorial purged cross-validation; Monte Carlo; deflated performance statistics.
- Paper trading and live trading via pluggable broker adapters.
- Full observability stack, alerting, runbooks.

**Out of scope (v1.0):**
- High-frequency / latency-arbitrage strategies (edge requires colocation and infrastructure the Principal does not have).
- Options, crypto, single equities (architecture MUST NOT preclude them later).
- Managing third-party capital (regulatory implications — Section 11).
- A GUI beyond Grafana dashboards and a Telegram/Slack bot.

### 1.3 Reality check — this section is binding on every agent
Agents are instructed to build with the following facts as ground truth, because ignoring them is the single largest cause of trading-system failure:

1. **Most retail algorithmic trading strategies lose money after costs.** Spread, commission, slippage and financing routinely exceed the gross edge of short-horizon strategies. Cost modelling is therefore a first-class engineering concern, not an afterthought.
2. **Overfitting is the default outcome of strategy research**, not the exception. Any process that tests many parameter sets or strategy variants on the same data and reports the best one is producing a biased estimate. Section 6 mandates the statistical corrections (deflated Sharpe ratio, probability of backtest overfitting, purged CV, embargo).
3. **Realistic performance for a well-built retail FX/index system is modest.** A robust after-cost Sharpe ratio of 0.5–1.0 with 15–25% maximum drawdown over multi-year out-of-sample periods is a good result. Backtests showing Sharpe > 2 on liquid majors at bar timeframes SHOULD be treated as evidence of a bug or bias until proven otherwise.
4. **Regimes change.** GBP/USD in 2016 (Brexit vote, October flash crash), 2020 (COVID), 2022 (gilt crisis / mini-budget) behaved unlike calmer periods. Strategies MUST be evaluated across regimes and MUST have regime-aware kill criteria.
5. **The edge, if any, lives in the portfolio, the risk management and the execution quality** far more than in any single signal. A mediocre signal with excellent risk control survives; a great signal with poor risk control blows up.
6. **Live results will be worse than backtest.** Budget for it: paper and live-micro phases exist to measure the gap and to set the slippage/cost parameters in the backtester from reality.
7. **"Best bot" is a process property:** best data hygiene, best bias control, best risk discipline, best operational reliability. Those are achievable. Guaranteed profit is not, and no agent may imply otherwise in code comments, docs, dashboards or reports.

### 1.4 Non-negotiables (violations fail any phase gate)
- **NN-1 Backtest/live parity:** strategy, portfolio, risk and OMS code MUST be identical in backtest, paper and live. Only `DataFeed`, `Broker`, and `Clock` implementations are swapped (Section 3.2).
- **NN-2 No look-ahead:** no component may access data timestamped after the current simulated/real time. Enforced by the event bus, tested by replay tests (Section 10.4).
- **NN-3 Every order is idempotent and attributable:** unique client order ID, strategy tag, config hash and git commit on every order and fill record.
- **NN-4 Hard kill switches exist and are tested** at daily-loss, drawdown, data-staleness, spread-blowout and broker-reconciliation-mismatch levels (Section 7.5). They cannot be disabled by a strategy.
- **NN-5 Secrets never in code, logs, or repos.** API keys have trade-only permissions (no withdrawals) [VERIFY per broker].
- **NN-6 Point-in-time correctness for all external data** (economic calendar, rates, dividends): stored with `available_at` timestamps, not just `event_time`.
- **NN-7 Costs are never optional in a backtest.** A backtest run without spread + commission + slippage + financing is a smoke test, not evidence, and MUST be labelled as such in any report.
- **NN-8 No strategy goes live without passing every gate in Section 13** including a minimum paper-trading duration.
- **NN-9 Reconciliation:** internal state MUST be reconciled against the broker at startup, on reconnect, and on a fixed interval. Mismatch → halt new orders + alert.
- **NN-10 Everything is reproducible:** a backtest report MUST record data snapshot ID, code commit, config hash, and random seeds so another agent can reproduce it bit-for-bit.

### 1.5 Definitions of done (global)
A task is done when: (a) tests exist and pass; (b) type checks (`mypy --strict`) and lints (`ruff`) pass; (c) an ADR exists if a design decision was made; (d) documentation in `docs/` is updated; (e) the change is demonstrable via a command listed in the README; (f) the agent has written a short "what I assumed / what I'm unsure about" note in the PR description.

---

## 2. INSTRUMENTS AND MARKET STRUCTURE

Agents building data, strategy or execution components MUST internalise this section. Errors here (wrong pip value, wrong session, wrong rollover) silently corrupt every downstream result.

### 2.1 GBP/USD (primary)
| Property | Value / Rule |
|---|---|
| Quote convention | USD per 1 GBP. Price 1.2750 = 1 GBP costs 1.2750 USD. |
| Pip | 0.0001. Fractional pip (pipette) = 0.00001. Store prices as `Decimal` or scaled integers (1/100000), never raw float, at the persistence and OMS boundaries. Floats MAY be used inside vectorised research code. |
| Pip value | Standard lot 100,000 GBP → USD 10.00 per pip. Mini 10,000 → USD 1.00. Micro 1,000 → USD 0.10. Because the quote currency is USD, pip value in USD is constant; convert to account currency (e.g. ZAR) at the current rate for risk maths. |
| Market hours | Continuous from Sunday 17:00 New York time to Friday 17:00 New York time (DST-agnostic definition). Liquidity is highly time-of-day dependent. |
| Daily rollover / swap | Applied at 17:00 New York. Wednesday rollover typically charges 3 days of swap (weekend settlement) [VERIFY per broker]. |
| Highest liquidity | London session, especially the London/New York overlap (approx. 13:00–16:30 London time). |
| Lowest liquidity | Late New York into early Asia (approx. 21:00–01:00 London). Spreads widen; stop-hunts and gap-like moves are common. |
| Typical spread (ECN/raw) | ~0.3–1.5 pips in liquid hours; 3–10+ pips around tier-1 news and the daily rollover; can exceed 20 pips at Sunday open and in flash events [VERIFY per broker; MUST be measured from stored bid/ask, not assumed]. |
| Tier-1 scheduled drivers | US: NFP (first Friday, 08:30 ET), CPI, FOMC decision + presser, PCE, retail sales, ISM. UK: BoE MPC decision + minutes (12:00 London, 8×/yr), UK CPI (07:00 London), UK labour market, UK GDP, retail sales, PMIs, gilt auctions when stressed. Political/fiscal events (budgets, elections) are tier-1 for GBP specifically. |
| Structural drivers | BoE–Fed policy rate differential and expectations; UK–US 2y yield spread; risk sentiment (GBP is a "risk-on" currency vs USD safe-haven); EUR/USD co-movement (correlation frequently 0.6–0.9 on daily returns; regime dependent); DXY. |
| Known tail events (for regime tests) | 2016-06-24 Brexit referendum result (~8% intraday range); 2016-10-07 flash crash (~6% drop in minutes during Asian session); 2020-03 COVID liquidity crunch; 2022-09-26 mini-budget/gilt crisis (multi-decade low, near parity). Backtests MUST include these dates; strategies MUST document behaviour through them. |

### 2.2 EUR/USD (secondary / relative-value leg)
Same structure as 2.1. Deeper liquidity, tighter spreads (~0.1–1.0 pip raw). Drivers: ECB, Eurozone CPI/PMIs, German IFO/ZEW. Used for: cross-pair confirmation, GBP/USD–EUR/USD spread (synthetic EUR/GBP) relative-value ideas, and correlation-aware portfolio risk.

### 2.3 Equity indices
Two representations exist and MUST be modelled separately in `configs/instruments.yaml`:

**(a) Futures** (CME ES/NQ/YM, ICE FTSE 100 "Z", Eurex FDAX/FDXM): exchange-traded, transparent volume, quarterly expiries (H/M/U/Z), rollover ~1 week before expiry, tick size and multiplier fixed by exchange (e.g. ES 0.25 index pts × USD 50/pt; MES ×USD 5) [VERIFY current specs]. Continuous back-adjusted series MUST be built for backtests with the adjustment method recorded (ratio or difference; difference is standard for indices).

**(b) CFDs** (US500, UK100, GER40 etc.): OTC contract priced by the broker from the underlying future (fair-value adjusted to a "cash" price on many brokers). No expiry on cash CFDs; overnight financing charged daily; **dividend adjustments** credited/debited on ex-dividend dates of constituents [VERIFY per broker]; broker-defined tick and minimum size; quoted nearly 24/5 but with much wider spreads outside cash hours. CFD prices are NOT exchange prices — backtests on CFD history are broker-specific.

Cash-session hours (exchange-local, DST applies):
| Index | Exchange cash session | Highest activity |
|---|---|---|
| S&P 500 / Nasdaq 100 / Dow | 09:30–16:00 ET (US DST) | First and last 60 min; 10:00 ET data releases; 14:00 ET FOMC |
| FTSE 100 | 08:00–16:30 London (UK DST) | Open; 14:30 London (US cash open) |
| DAX 40 | 09:00–17:30 CET (Xetra) (EU DST) | Open; 15:30 CET (US cash open) |

Index-specific behaviours the strategy layer MUST have features for: overnight gap (close→open); opening range; US cash open impact on European indices; quarterly futures expiry ("triple witching"); month-end/quarter-end rebalancing flows; VIX regime for US indices.

### 2.4 Cross-cutting structural facts
- **US and UK/EU DST change on different dates** (US: 2nd Sunday March / 1st Sunday November; UK/EU: last Sunday March / last Sunday October). For 2–3 weeks per year, session overlaps shift by an hour. This is a classic silent bug. Section 3.4 mandates tests for it.
- **Bank holidays** (US, UK, DE) thin liquidity and alter behaviour. Store an exchange holiday calendar; strategies MAY suppress trading on them.
- **Weekend gaps** exist for all instruments. Stop-loss orders do not protect against gaps unless "guaranteed" (premium cost) [VERIFY]. Position-sizing MUST assume stop slippage on weekend-held positions (Section 7.3).
- **Negative balance protection** is mandated for retail clients under some regulators (FCA, ESMA) but not universally [VERIFY for the chosen broker/entity]. The risk layer MUST NOT rely on it.
- **Leverage caps** differ by regulator and client classification (e.g. FCA retail: 30:1 majors, 20:1 major indices) [VERIFY]. The FSCA (South Africa) framework differs. The risk layer computes margin from the broker's live instrument specification, never from a hard-coded number.

---

## 3. SYSTEM ARCHITECTURE

### 3.1 Architectural principles
1. **Event-driven, single-writer core.** All market data, signals, orders, fills and timer events flow through one ordered event bus per process. Determinism in backtest = same event order every run.
2. **Parity by construction (NN-1).** The `Strategy`, `Portfolio`, `RiskManager` and `OMS` objects have no idea whether they are in a backtest or live. They receive events and emit orders. Three swappable ports: `DataFeed`, `Broker`, `Clock`.
3. **Immutable, typed messages.** Every event is a frozen dataclass / pydantic model with an explicit `ts_event` (when it happened in the market) and `ts_recv` (when we observed it). Look-ahead prevention is enforced by the bus refusing to deliver events with `ts_event > clock.now()`.
4. **Persistence is append-only where possible.** Orders, fills, decisions, risk checks, and config snapshots are written as an audit log. State can be rebuilt by replay.
5. **Fail closed.** Any unhandled exception in the trading path → cancel working orders (configurable: flatten or hold), stop emitting new orders, alert. Never "log and continue" in the order path.
6. **Boring technology.** Python 3.12, PostgreSQL, Redis, Docker, Prometheus/Grafana on Ubuntu LTS. No microservice sprawl. One process per concern, communicating through Redis streams or a well-defined in-process bus. Rust/C++ is out of scope unless profiling proves Python is the bottleneck at the target timeframes (it will not be at ≥1-minute bars).

### 3.2 Component map
```
                     ┌──────────────────────────────────────────────────────────┐
                     │                    CONFIG (pydantic, YAML)                │
                     └──────────────────────────────────────────────────────────┘
                                                 │
 ┌───────────┐   ticks/bars   ┌────────────┐  events  ┌──────────────┐ signals ┌───────────┐
 │ DataFeed  │───────────────▶│  Event Bus │─────────▶│  Strategies  │────────▶│ Portfolio │
 │(live/hist)│                │ + Clock    │          │ (N plugins)  │         │ Allocator │
 └───────────┘                └────────────┘          └──────────────┘         └─────┬─────┘
       │                             ▲                                                │ target positions
       │ raw store                   │ fills/status                                   ▼
       ▼                             │                                         ┌───────────┐
 ┌───────────┐                ┌────────────┐   orders   ┌──────────────┐  ok   │   Risk    │
 │ Storage   │                │   Broker   │◀───────────│     OMS      │◀──────│  Manager  │
 │Parquet/PG │                │ (adapter)  │───────────▶│ state machine│       │ + kill sw │
 └───────────┘                └────────────┘   fills    └──────────────┘       └───────────┘
       │                             │                          │
       ▼                             ▼                          ▼
 ┌────────────────────────────────────────────────────────────────────────────────────────┐
 │  Audit log (append-only) · Metrics (Prometheus) · Logs (structured JSON) · Alerts       │
 └────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Technology stack (defaults; deviations via ADR)
| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.12+, `asyncio` | Ecosystem, team familiarity, adequate for bar-level trading. |
| Typing/validation | `pydantic` v2 models at boundaries; `dataclasses(frozen=True)` internally; `mypy --strict` | Catch schema drift at load time. |
| Numerics | `numpy`, `pandas` (research), `polars` (large tick processing), `numba` where hot | Vectorised research; polars for multi-GB tick sets. |
| Columnar storage | Parquet (partitioned by instrument/date) + DuckDB for ad-hoc queries | Cheap, fast, reproducible snapshots. |
| Relational/state | PostgreSQL 16 (TimescaleDB extension for bars is acceptable) | Orders, fills, positions, runs, config snapshots, audit. |
| Cache / streams | Redis 7 (streams for inter-process events, hashes for hot state) | Familiar, fast, simple. |
| Scheduling | In-process `asyncio` timers + `systemd` timers for batch jobs | Avoid Airflow-scale complexity. |
| Broker APIs | See Section 8.6 | |
| Metrics | Prometheus client → Prometheus → Grafana; Alertmanager | Standard. |
| Logs | `structlog` JSON → Loki (or files + `journald`) | Queryable, correlated by `run_id`/`order_id`. |
| Alerts | Telegram bot (primary), email (secondary), PagerDuty/phone for kill-switch events (optional) | |
| Packaging | `uv` or `poetry`; Docker multi-stage; Compose for local; `systemd` units on the VPS | |
| CI | GitHub Actions: ruff, mypy, pytest (unit+integration), replay golden tests, coverage gate | |
| Secrets | `sops` + age, or Vault; env injection at runtime only | NN-5 |
| Research | Jupyter in `notebooks/` — MUST NOT be imported by `src/`. Research code graduates by being rewritten into `src/` with tests. | |

### 3.4 Time handling (mandatory rules)
- All internal timestamps are timezone-aware UTC (`datetime` with `tzinfo=UTC`, or int64 nanoseconds UTC).
- Exchange-local logic (sessions, holidays) uses `zoneinfo` (`America/New_York`, `Europe/London`, `Europe/Berlin`) — never fixed offsets.
- The "trading day" for FX is defined as 17:00 New York → 17:00 New York; daily bars MUST be built on that boundary (configurable), not midnight UTC.
- A `Clock` port exposes `now()`, `sleep_until()`, and `schedule()`. `SimClock` advances only on events; `WallClock` uses the OS. Strategies MUST obtain time only from the clock.
- Tests MUST cover: US-DST-only weeks, UK-DST-only weeks, leap day, Sunday open, Friday close, year boundary, NTP step-adjustment (WallClock monotonic guard).
- Host time sync: `chrony` with ≥3 sources; alert if offset > 250 ms.

### 3.5 Repository layout
```
tradebot/
├── pyproject.toml            # uv/poetry, ruff, mypy, pytest config
├── README.md                 # how to run every phase from a clean machine
├── docs/
│   ├── SPEC.md               # this document (frozen copy per version)
│   ├── adr/                  # 0001-*.md ...
│   ├── runbooks/             # incident playbooks (Section 9.7)
│   └── reports/              # generated backtest/paper/live reports (git-lfs or external)
├── configs/
│   ├── instruments.yaml      # contract specs, sessions, tick sizes, pip values
│   ├── brokers/*.yaml        # endpoint, account type, symbol mapping (NO secrets)
│   ├── strategies/*.yaml     # versioned parameter sets
│   ├── risk.yaml             # limits, kill switches
│   └── env/{backtest,paper,live}.yaml
├── src/tradebot/
│   ├── core/       # events, bus, clock, ids, money/Decimal helpers, errors
│   ├── data/       # ingest, quality checks, bar builders, calendar, storage
│   ├── features/   # pure functions: bars → features (no I/O)
│   ├── strategies/ # base Protocol + one module per strategy
│   ├── portfolio/  # forecast combination, vol targeting, allocator
│   ├── risk/       # pre-trade checks, limits, circuit breakers, kill switch
│   ├── execution/  # OMS state machine, router, TCA
│   ├── brokers/    # base Protocol; oanda/, ig/, ibkr/, ctrader/, mt5/, paper/, sim/
│   ├── backtest/   # engine, fill models, cost models, walk-forward, CPCV, metrics, reports
│   ├── live/       # runner, supervisor hooks, reconciler, heartbeat
│   └── monitoring/ # prometheus exporters, alert dispatch
├── notebooks/      # research only; never imported by src
├── tests/
│   ├── unit/  integration/  replay/  chaos/  fixtures/golden/
├── scripts/        # data downloads, one-off ops (each idempotent, each documented)
└── deploy/         # Dockerfile, compose.yaml, systemd/, grafana/dashboards/, prometheus/
```

### 3.6 Core interfaces (contracts agents MUST implement against)
```python
# src/tradebot/core/types.py  (sketch — final version via ADR-0002)
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import Protocol, Sequence

class Side(Enum): BUY = 1; SELL = -1

@dataclass(frozen=True, slots=True)
class Tick:
    instrument: str; ts_event: datetime; ts_recv: datetime
    bid: Decimal; ask: Decimal; bid_size: int | None; ask_size: int | None

@dataclass(frozen=True, slots=True)
class Bar:
    instrument: str; ts_open: datetime; ts_close: datetime   # ts_close is the delivery time
    open: Decimal; high: Decimal; low: Decimal; close: Decimal
    volume: int | None            # FX: tick count, NOT real volume — flag it
    spread_mean: Decimal | None   # mean (ask-bid) inside bar, from ticks
    n_ticks: int | None

@dataclass(frozen=True, slots=True)
class Forecast:
    """Strategy output. Scaled forecast in [-20, +20] (Carver convention),
    +10 == 'average-strength long'. Position sizing happens in Portfolio, never here."""
    strategy_id: str; instrument: str; ts: datetime; value: float
    confidence: float | None = None; meta: dict | None = None

@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str; instrument: str; side: Side; qty: Decimal
    order_type: str                 # MARKET | LIMIT | STOP | STOP_LIMIT
    limit_price: Decimal | None = None; stop_price: Decimal | None = None
    time_in_force: str = "GTC"      # GTC | IOC | FOK | DAY
    stop_loss: Decimal | None = None; take_profit: Decimal | None = None   # bracket
    strategy_id: str = ""; run_id: str = ""; config_hash: str = ""; git_sha: str = ""

class Strategy(Protocol):
    id: str
    instruments: Sequence[str]
    warmup_bars: int
    def on_bar(self, bar: Bar, ctx: "Context") -> Sequence[Forecast]: ...
    def on_tick(self, tick: Tick, ctx: "Context") -> Sequence[Forecast]: ...   # optional
    def on_fill(self, fill: "Fill", ctx: "Context") -> None: ...
    def state(self) -> dict: ...                # for checkpoint/restore
    def restore(self, state: dict) -> None: ...

class Broker(Protocol):
    async def connect(self) -> None: ...
    async def submit(self, req: OrderRequest) -> "OrderAck": ...
    async def cancel(self, client_order_id: str) -> "OrderAck": ...
    async def replace(self, client_order_id: str, **changes) -> "OrderAck": ...
    async def positions(self) -> Sequence["Position"]: ...
    async def open_orders(self) -> Sequence["OrderState"]: ...
    async def account(self) -> "AccountSnapshot": ...
    async def instrument_spec(self, instrument: str) -> "InstrumentSpec": ...
    def stream_events(self) -> "AsyncIterator[BrokerEvent]": ...   # fills, rejects, status, prices

class Clock(Protocol):
    def now(self) -> datetime: ...
    async def sleep_until(self, ts: datetime) -> None: ...
```
Rules: `Strategy` never touches the broker, never sizes positions, never reads the wall clock. `Portfolio` turns forecasts into target positions. `RiskManager.check(order, state) -> Allow | Reduce(qty) | Reject(reason)` runs on every order. `OMS` is the only writer to the broker.

### 3.7 Configuration
- YAML files validated into pydantic models at startup; any unknown key is an error (`extra="forbid"`).
- Every running process logs `config_hash = sha256(canonical_json(config))` and `git_sha` at start and stamps them on every order (NN-3, NN-10).
- Live config changes require restart (no hot reload in v1 — simpler, safer). Exception: the kill switch and "pause new entries" flags are runtime toggles via Redis with audit trail.
- Strategy parameter files are versioned (`strategies/lonbreak_v3.yaml`); the backtest report that justified a version is referenced in the file header.

---

## 4. DATA LAYER

Bad data produces confident, wrong strategies. Data engineering is the largest single chunk of Phase 1 and MUST be treated as a first-class product with its own tests, SLAs and quality dashboards.

### 4.1 Data sources (candidates — agent MUST evaluate and record choice in ADR)
| Type | Candidates | Notes |
|---|---|---|
| FX tick history (bid/ask) | Dukascopy (free historical tick, bid/ask, tick volume); TrueFX (majors, tick); broker's own history (OANDA v20, IG, cTrader, MT5) | Prefer bid/ask tick from the **same broker you will trade with** for the final calibration; use Dukascopy for depth of history (2003+ for majors) [VERIFY availability/ToS]. |
| Index futures history | Databento (CME/Eurex/ICE, tick + MBO), FirstRate Data, Kibot, broker (IBKR) | Paid. Needed if trading futures or wanting exchange-quality index data. |
| Index CFD history | Chosen broker only | Broker-specific pricing; store per broker. |
| Economic calendar (point-in-time) | Trading Economics API, Econoday, FXStreet/Investing.com/ForexFactory (scraping — check ToS), central bank sites for official times | MUST store `scheduled_at`, `actual_release_at` (if available), `consensus`, `previous`, `actual`, `revised`, plus `available_at` = when our system could have known each field. |
| Rates / macro | FRED (Fed funds, treasuries), BoE Bank Rate & gilt yields, ECB | Daily; point-in-time vintages where relevant (FRED ALFRED for revisions). |
| Volatility | VIX (CBOE), implied vols for FX (paid — e.g. Bloomberg/Refinitiv; likely out of scope) | VIX for index regime; realised vol as the FX proxy. |
| Holidays | Exchange calendars (`exchange_calendars` / `pandas_market_calendars` libraries) | Verify against exchange notices annually. |

### 4.2 Storage model
- **Raw layer** (`data/raw/{source}/{instrument}/{yyyy}/{mm}/…parquet`): exactly what was received, plus `ts_recv`, source, and ingest `run_id`. Never modified.
- **Clean layer** (`data/clean/ticks/…`, `data/clean/bars/{tf}/…`): after quality pipeline (4.4), with `quality_flags` column. Deterministically derived from raw; rebuildable.
- **Snapshots**: a `dataset_id = sha256(list of clean file hashes)` per backtest run. Reports reference `dataset_id` (NN-10).
- Tick schema: `ts_event(ns UTC), ts_recv, bid, ask, bid_size?, ask_size?, source, seq`.
- Bar schema: `ts_open, ts_close, open, high, low, close, volume?, n_ticks, spread_mean, spread_max, bid_close, ask_close, quality_flags`. Store **bid-side and ask-side bars** or mid-bars + spread stats — never mid-only without spread (fills need both sides).
- Retention: ticks for all traded instruments indefinitely (compressed Parquet is small: ~1–3 GB/yr/major pair). Postgres holds trading state, not tick history.

### 4.3 Bar construction
- Time bars (1m, 5m, 15m, 1h, 4h, 1d[17:00 NY]) are the baseline. Bars are **closed** when delivered; `ts_close` is the event timestamp. A bar with zero ticks is not emitted (configurable: emit with `n_ticks=0` and carried-forward close, flagged).
- Alternative bars (tick bars, dollar/volume bars, range/Renko) MAY be implemented behind the same `BarBuilder` interface for research. Note: FX "volume" is tick count; do not label it as traded volume anywhere.
- Higher timeframes MUST be built from lower-timeframe clean bars or ticks by the same code, never downloaded separately (prevents mismatched sources).
- Daily FX bars use the 17:00 NY boundary; daily index bars use exchange session boundaries; both configurable per instrument.

### 4.4 Data quality pipeline (each check produces a flag; thresholds in `configs/data_quality.yaml`)
1. **Monotonic time** per instrument/source; duplicates dropped with count logged.
2. **Crossed/locked quotes** (`ask <= bid`) flagged; excluded from mid but kept in raw.
3. **Spread outliers**: `spread > k × rolling median(spread, 1h)` flagged (default k=10) — useful later for the live spread circuit breaker calibration.
4. **Price outliers**: return between consecutive ticks > N × rolling tick-vol (default N=20) AND reverts within M ticks → flagged as bad tick; if it does not revert it is a real move (e.g., 2016-10-07) and MUST be kept.
5. **Gaps**: no ticks for > G seconds during a session where median inter-tick time is < 1 s → flagged `gap`. Weekend/holiday gaps expected and whitelisted by the calendar.
6. **Session sanity**: ticks outside market hours flagged (FX Saturday ticks are almost always bad).
7. **Cross-source agreement** (when ≥2 sources): mid divergence > X pips for > Y seconds flagged.
8. **Completeness report** per day: expected vs actual tick/bar counts, % flagged, published to a Grafana data-quality dashboard.
9. **DST validation**: for each DST transition date, assert that the London-open volatility spike lands at the expected UTC hour after conversion.
Clean bars carry the OR of flags of their constituent ticks. Backtests MUST report how many bars with flags were consumed.

### 4.5 Live data feed
- Streaming bid/ask from the broker adapter; also poll REST every N seconds as a heartbeat cross-check.
- Staleness watchdog: no tick for > S seconds during expected-liquid hours (instrument-specific, default 10 s for GBP/USD in London hours) → `DATA_STALE` event → risk manager pauses entries; > S2 (default 60 s) → circuit breaker (Section 7.5).
- Live bars are built by the same `BarBuilder` as historical (parity); at startup the feed backfills the warm-up window from broker history and marks those bars `backfilled=true`.

### 4.6 Data acceptance tests (Gate 1)
- Rebuild a random sample of 30 days of 1m bars from ticks twice → byte-identical.
- Known-answer test: hand-verified OHLC for 5 randomly chosen bars vs a third-party chart.
- DST tests (3.4) pass.
- Quality dashboard live; < 0.1% of liquid-hours bars flagged for a reference month; every flag category has at least one unit test with a synthetic example.
- Point-in-time calendar: query "what did we know at T" returns only fields with `available_at <= T` (tested).

---

## 5. STRATEGY RESEARCH FRAMEWORK

### 5.1 Research process (mandatory, per strategy)
```
1. HYPOTHESIS   Written before any data is touched: economic/behavioural rationale,
                who is on the other side, why the edge persists, expected regime behaviour,
                expected holding period, expected trade frequency, expected cost ratio.
2. SPEC         Entry/exit/sizing rules as pseudo-code; parameter list with a-priori
                plausible ranges (narrow!); explicit list of data used and its latency.
3. DATA SPLIT   Decide in advance: in-sample window(s), embargo, out-of-sample, and a
                final "lockbox" period never touched until Gate 3.
4. BASELINE     Implement on 1 parameter set at the centre of the a-priori range. Run with
                full costs. If it is not at least marginally positive, STOP: do not go
                parameter hunting. Revisit hypothesis or abandon.
5. ROBUSTNESS   Parameter-neighbourhood heatmaps (performance must be a smooth plateau,
                not a spike); regime breakdown; instrument breadth (does it also work on
                EUR/USD? If a "GBP/USD-specific" edge cannot be explained, suspect noise).
6. STATISTICS   Walk-forward; CPCV; deflated Sharpe; PBO; Monte Carlo (Section 6).
7. LOCKBOX      Single run on the untouched period. Record the result whatever it is.
8. REVIEW       Red-team agent reviews for look-ahead, leakage, survivorship, data errors.
9. PAPER        Section 13 gates.
```
Every attempt — including failures — is logged in `docs/research_log.md` with the number of variants tried. This count feeds the deflated Sharpe ratio (Section 6.5). Hiding failed variants is the definition of data snooping.

### 5.2 Feature library (pure functions in `src/tradebot/features/`, all causal, all unit-tested)
**Price/return:** log returns at horizons {1,5,20,60} bars; rolling mean/std; z-score of price vs SMA/EMA (20/50/200); Donchian channel position; distance to session high/low; Hurst exponent (rolling); fractional differentiation (López de Prado) for stationarity with memory.
**Volatility:** ATR(14, 50); realised vol (close-to-close, Parkinson, Garman–Klass, Yang–Zhang); vol-of-vol; vol ratio (short/long); rolling spread-to-ATR ratio (cost pressure).
**Momentum/oscillators:** RSI, MACD histogram, rate of change, TSMOM sign at multiple lookbacks, breakout age.
**Session/calendar:** session flags (Asia/London/NY/overlap), minutes since London open, minutes to NY close, day of week, month-end flag, holiday flag, minutes-to-next-tier-1-event, minutes-since-last-tier-1-event, surprise of last release (actual − consensus, standardised).
**Cross-asset:** EUR/USD return correlation (rolling), GBP/USD − EUR/USD spread z-score (synthetic EUR/GBP), DXY proxy, UK–US 2y spread (daily), VIX level/change (indices), index futures basis, ES–NQ relative strength.
**Microstructure proxies (from tick data):** tick imbalance (up-ticks − down-ticks), spread regime, tick intensity (ticks/min vs same-hour median), Roll spread estimate, Amihud-style illiquidity proxy.
**Regime:** HMM (2–3 states on returns+vol), rolling ADF stat, trend-strength (ADX), realised-vol quantile.
Each feature declares `lookback` so the engine can compute `warmup_bars` automatically.

### 5.3 Strategy catalogue (v1 candidates — implement in the order listed; each is its own module + YAML + research log entry)
| # | Strategy | Hypothesis | Mechanics (baseline) | Horizon | Known failure modes |
|---|---|---|---|---|---|
| S1 | **Time-series momentum (TSMOM), multi-lookback** | Slow information diffusion; institutional flows persist | Forecast = vol-scaled sign/strength of trailing return at lookbacks {8,16,32,64} × bar unit (e.g. 4h/1d bars). Combine via equal weights. Vol-target. | Days–weeks | Chops in range regimes; 2012–2013 style low-vol drift; costs if lookbacks too short |
| S2 | **Breakout (Donchian/EWMAC style, Carver)** | Same as S1, different signal shape | EWMAC pairs (16/64, 32/128); Donchian breakout with vol-scaled forecast | Days–weeks | Whipsaw at channel edges |
| S3 | **London open range breakout** | Asian-session range compresses; London open resolves direction | Range = high/low of 00:00–07:00 London; enter on breakout with buffer = k×ATR; stop opposite side; time-exit before NY close; skip tier-1 UK data days or widen buffer | Intraday | Fakeouts; spread at open; DST misalignment |
| S4 | **Intraday mean reversion (session-bounded)** | Overextension vs VWAP/mean during low-info periods reverts | Z-score of price vs session VWAP or 1h EMA > 2 during Asia/late-NY → fade; exit at mean or time stop; disabled ±30 min around tier-1 events | Minutes–hours | Trends through the fade; news; wide spreads make the edge negative |
| S5 | **Overnight/gap strategies (indices)** | Structured flows at cash open/close (rebalancing, gap fill statistics) | Fade or follow the overnight gap conditioned on gap size vs ATR and VIX regime; first-hour range breakout | Intraday | Regime dependence; event days |
| S6 | **Volatility regime switching / vol targeting overlay** | Risk-adjusted returns improve when exposure scales inversely with vol | Not a signal — a portfolio rule: target constant annualised risk; reduce in vol spikes | Continuous | Late reaction to vol spikes; leverage in quiet periods |
| S7 | **Cross-pair relative value (GBP/USD vs EUR/USD)** | Common USD factor; idiosyncratic GBP/EUR shocks revert | Rolling hedge ratio (Kalman or OLS on log prices); z-score of spread; enter beyond ±2, exit at 0; hard stop at ±4 or structural-break test | Hours–days | Brexit-type structural breaks; cointegration breakdown |
| S8 | **Carry / rate-differential tilt** | Interest differential earns swap; persistence of policy paths | Daily tilt long the higher-yielding leg when trend agrees; size small; explicitly model swap | Weeks–months | Carry crashes in risk-off; near-zero differentials |
| S9 | **Event-aware overlays (not standalone)** | Volatility around tier-1 releases is predictable; direction is not | (a) Blackout: flatten/skip entries ±N min. (b) Post-event drift: enter in direction of the first 5–15 min move after a large surprise, tight time stop. (c) Straddle-like OCO (research only; spreads usually kill it) | Minutes–hours | Spread blowout; slippage; consensus data quality |
| S10 | **ML meta-labeling (López de Prado)** | ML is better at deciding *whether* to take a primary signal than at generating one | Primary signals from S1–S8 → triple-barrier labels → LightGBM classifier on features (5.2) → bet size = f(probability). Purged k-fold + embargo; walk-forward retraining | Inherits primary | Leakage; non-stationarity; overfit hyper-params |
Explicitly deprioritised for v1: LSTM/transformer price prediction (poor signal-to-noise at these horizons, hard to validate), order-book strategies (no L2 data), sentiment/NLP (data cost and latency), martingale/grid/"recovery" logic (**prohibited** — negative skew disguised as high win rate).

### 5.4 Portfolio construction (Carver-style)
- Each strategy emits forecasts scaled to an average absolute value of 10 (cap ±20). Scaling factors are estimated from in-sample data and frozen.
- Combined forecast per instrument = weighted sum of strategy forecasts × **forecast diversification multiplier** (accounts for sub-additivity of correlated forecasts).
- Target position (in instrument units) = combined forecast / 10 × (target daily cash vol per instrument) / (instrument daily price vol × point value), where target vol is the portfolio vol target × instrument weight × **instrument diversification multiplier**.
- Position changes are buffered (don't trade if |target − current| < buffer, e.g. 10% of average position) to cut turnover and costs.
- Correlation-aware weighting via shrunk covariance (Ledoit–Wolf) or hand-set weights + correlation caps; weights reviewed monthly, not daily.
- Portfolio vol target default: 12% annualised (ADR to change). This is a risk knob, not a return promise.

### 5.5 Machine-learning guardrails (binding if S10 or any ML is built)
- Labels: triple-barrier (profit-take, stop, time) with barrier widths from ATR; sample weights by label uniqueness/overlap.
- CV: purged k-fold with embargo ≥ max label horizon; report CPCV backtest path distribution, not one path.
- Features must be causal, stationary-ish (fractional diff where needed), and few (< 30). Feature importance via MDA/SFI; drop anything that only matters in one fold.
- Hyper-parameter search is counted as trials in the DSR computation.
- Models are versioned artifacts (`models/{strategy}/{version}.pkl` + training `dataset_id` + metrics JSON). Live inference uses the frozen artifact; retraining is a scheduled job with a promotion gate (new model must beat incumbent on the most recent OOS window AND not degrade CPCV distribution).
- Drift monitoring in live: feature distribution PSI vs training; prediction distribution; realised hit-rate vs predicted probability calibration. Breach → fall back to primary signal at reduced size + alert.

---

## 6. BACKTESTING AND VALIDATION ENGINE

### 6.1 Engine requirements
- Event-driven; consumes clean bars and (optionally) ticks; drives the identical Strategy/Portfolio/Risk/OMS stack via `SimClock`, `HistoricalDataFeed`, `SimBroker`.
- Multi-instrument, multi-strategy, single portfolio, single account currency (default USD in research; ZAR/GBP conversion supported via stored FX rates).
- Deterministic: same inputs + seed → identical output (tested in CI with a golden-file replay).
- Speed target: 10 years of 1m bars, 5 instruments, 10 strategies in < 10 minutes on a laptop (vectorised feature precompute + event loop). Profile before optimising.
- Two fidelity modes: **bar mode** (fills evaluated against next bar or intrabar path assumptions) and **tick mode** (orders matched against the historical bid/ask tick stream — the reference mode for intraday strategies S3, S4, S5, S9).

### 6.2 Fill model (SimBroker) — conservative by default
| Order type | Fill rule (default) |
|---|---|
| Market | Filled at the **ask** (buy) / **bid** (sell) of the first tick with `ts_event > order.ts` plus modelled latency (default 150 ms live-equivalent; ADR per broker) plus slippage (6.3). In bar mode: next bar open ± half the bar's mean spread ± slippage. |
| Limit | Filled only if price **trades through** the limit (bid ≤ limit − 1 tick for a buy limit) — not on touch. Configurable to "touch" for sensitivity analysis; touch-fills MUST be labelled optimistic in reports. |
| Stop | Triggered when the opposite-side quote reaches the stop; filled as a market order → includes slippage that scales with the size of the move in the triggering tick/bar. Gaps: filled at the first available price after the gap, not at the stop price. |
| Stop-limit | Trigger as stop, then behave as limit. |
| Bracket (SL/TP) | SL and TP evaluated on every tick; if both are touched in the same bar (bar mode), assume the **adverse** one hit first unless tick data disambiguates. |
| Partial fills | Modelled for sizes > configurable fraction of "typical size"; default off for retail sizes. |
| Rejects | Simulated: margin insufficient, outside trading hours, min/max size, price bands. |

### 6.3 Cost model (never off — NN-7)
- **Spread**: taken from stored bid/ask at fill time (tick mode) or per-bar spread stats (bar mode). Never a constant unless doing a sensitivity table.
- **Commission**: per-lot / per-contract, per broker config (e.g. USD 3.50 per 100k side on raw-spread accounts is a common shape [VERIFY]).
- **Slippage**: `slip = a + b × (spread) + c × (order_size / typical_size) × ATR + jump term when |move| in trigger bar > k×ATR`. Coefficients start at conservative defaults and are **re-estimated from paper/live TCA** (Section 8.5) before Gate 5.
- **Financing/swap**: FX swaps per broker rate table per day held over 17:00 NY, triple Wednesday [VERIFY]; index CFD financing = notional × (benchmark ± broker spread) / 365 per day; futures: no financing but roll cost/slippage at each roll.
- **Dividends** for index CFDs on ex-dates; **roll** cost for futures.
- **Currency conversion** of PnL to account currency at prevailing rates (matters for a ZAR account).
- Report MUST show gross PnL, each cost line, net PnL, and **cost ratio = total costs / gross PnL**. Strategies with cost ratio > 50% are fragile by construction.

### 6.4 Bias controls (each has a test in `tests/replay/`)
- Look-ahead: bus refuses future events; features computed only from bars with `ts_close <= now`.
- Repainting indicators prohibited (e.g. ZigZag, centred moving averages). Lint rule: features may not use `.shift(-n)`.
- Survivorship: n/a for FX; for index constituents-based features, use point-in-time constituents (out of scope v1).
- Data snooping: trial counting (5.1) → DSR (6.5).
- Storytelling bias: the lockbox period result is reported unedited.
- Broker-specific pricing: results on Dukascopy data MUST be re-run on the trading broker's data before Gate 3.

### 6.5 Statistical validation suite (all produced automatically in the report)
- **Walk-forward**: rolling or anchored; train window W, test window T, step T; parameters re-optimised only inside train; OOS stitched equity curve is the headline result.
- **Combinatorial Purged Cross-Validation (CPCV)** over N groups with purge + embargo → distribution of OOS Sharpe across backtest paths; report median, 5th percentile, and % of paths with Sharpe ≤ 0.
- **Probability of Backtest Overfitting (PBO)** via CSCV (Bailey, Borwein, López de Prado, Zhu). Target PBO < 0.20.
- **Deflated Sharpe Ratio (DSR)** using number of independent trials (estimated from trial count and their correlation) and non-normality (skew, kurtosis). Target DSR ≥ 0.95 at the chosen benchmark.
- **Probabilistic Sharpe Ratio (PSR)** vs SR* = 0 and vs SR* = 0.5.
- **Minimum track record length** required to be confident SR > 0 at 95%.
- **Monte Carlo**: (a) trade-order bootstrap → distribution of max drawdown and CAGR; (b) block bootstrap of returns; (c) randomised entry-timing perturbation (±1 bar) → sensitivity; (d) cost multiplier stress (×1.5, ×2).
- **Parameter stability**: 2-D heatmaps around chosen parameters; the chosen point must be inside a plateau (neighbours within 70% of its metric).
- **Regime table**: metrics per year, per vol tercile, per session, and for the tail-event windows in 2.1.
- **Instrument breadth**: same rules on EUR/USD (and other majors if data exists) — a signal that exists only on one pair with no mechanism is suspect.
- **Trade-count power check**: OOS trades ≥ 200 (or a documented justification for lower-frequency strategies) before any Sharpe is quoted.

### 6.6 Metrics (definitions fixed in `backtest/metrics.py` with tests against hand-computed examples)
CAGR; annualised vol; Sharpe (annualisation factor from actual bar frequency, risk-free configurable, default 0); Sortino; Calmar; max drawdown (depth, duration, recovery time); Ulcer index; profit factor; win rate; average win/loss; expectancy per trade and per unit risk; payoff ratio; skew and excess kurtosis of returns; tail ratio (95th/5th percentile); turnover (× notional / equity per year); average holding time; exposure (% time in market); cost ratio; t-statistic of mean return; SQN. Per-strategy and portfolio level; per-instrument breakdown.

### 6.7 Report format
Machine-readable JSON (all metrics + metadata: `dataset_id`, `git_sha`, `config_hash`, seeds, trial count) + human HTML/Markdown with: equity & drawdown curves (log scale), rolling 1y Sharpe, monthly returns heatmap, trade distribution, CPCV path fan chart, parameter heatmaps, regime table, cost breakdown, and a mandatory **"Caveats" section** auto-populated with every optimistic assumption in force (touch fills, constant spread, missing swap, flagged bars consumed, etc.).

### 6.8 Backtester acceptance tests (Gate 2)
- Known-answer tests: synthetic price paths (deterministic sine, step, random walk with seed) with hand-computed expected PnL for market/limit/stop/bracket orders including spread and commission — exact match to the cent.
- Zero-edge test: random signals on real data with full costs → mean net PnL negative and ≈ −(costs); Sharpe distribution centred ≤ 0. If a random strategy is profitable, the fill model is optimistic.
- Look-ahead canary: a deliberately cheating strategy (uses next bar's close) MUST be blocked by the bus and fail a test.
- Parity test: run a strategy through the backtester on day D's ticks, then replay the same ticks through the live stack with `PaperBroker` in accelerated `WallClock` → identical orders (same client_order_id sequence, sizes, prices within fill-model tolerance).
- Determinism: two runs → identical JSON report hash.

---

## 7. RISK MANAGEMENT

Risk is a separate component with veto power. Strategies cannot see or change risk limits. Limits live in `configs/risk.yaml` with an audit trail of changes.

### 7.1 Position sizing
- Portfolio vol targeting (5.4) is the primary sizing mechanism. Instrument vol estimate = EWMA of daily returns (span ≈ 32 days) blended with a longer-term floor to avoid over-sizing in unusually quiet periods (e.g. 30% weight on 1-year vol).
- Per-trade risk cap: the loss at the initial stop MUST NOT exceed `max_risk_per_trade` (default 0.5% of equity; hard ceiling 1%).
- Kelly is computed for information only; **position sizes are capped at ≤ 0.25 × Kelly** derived from OOS statistics with shrinkage. Full-Kelly sizing is prohibited.
- Leverage cap: gross notional / equity ≤ `max_gross_leverage` (default 5:1; hard ceiling below regulatory/broker max with a 30% buffer).
- Minimum size handling: if target < broker minimum, hold zero (don't round up).

### 7.2 Exposure and concentration limits
- Per-instrument net exposure cap (default 2× target vol-scaled position).
- Correlated-cluster cap: GBP/USD + EUR/USD treated as one USD cluster; US500 + UK100 + GER40 as one equity cluster. Cluster net exposure ≤ `max_cluster_exposure`.
- Per-strategy capital allocation cap so no single strategy can consume the portfolio's risk budget.
- Max number of open positions and max orders per minute (fat-finger/loop guard).

### 7.3 Drawdown and loss limits (soft → hard)
| Limit | Default | Action |
|---|---|---|
| Daily loss (realised + unrealised) | −2% equity | Pause new entries for the trading day; alert |
| Weekly loss | −4% | Pause for the week; require manual resume |
| Strategy drawdown vs its own backtest MC 95th-percentile DD | exceeded | Strategy paused; review required |
| Portfolio drawdown from HWM | −10% | Halve all position targets |
| Portfolio drawdown from HWM | −15% | **Kill switch**: flatten all, disable trading, require Principal re-enable with written review |
| Weekend/holiday holding | configurable per strategy | Reduce size or flatten before Friday close; size weekend-held positions assuming stop slippage of ≥ 3× normal |

### 7.4 Pre-trade checks (every order, in order; first failure rejects)
1. Kill switch / pause flags. 2. Instrument tradable now (session, holiday, broker status). 3. Data freshness (7.5). 4. Spread ≤ `max_spread_multiple` × rolling median (default 3×) — else reject/delay. 5. Size sanity: qty > 0, ≤ max order size, ≤ remaining exposure caps, not an obvious ×10/×100 error vs recent orders. 6. Margin: projected margin usage ≤ 60% of available (buffer for adverse moves). 7. Price sanity for limit/stop: within k×ATR of last price. 8. Duplicate check: no identical open order with same strategy/instrument/side within T seconds. 9. Event blackout window (if strategy opted in). 10. Rate limit.
Every decision (allow/reduce/reject + reason) is written to the audit log.

### 7.5 Circuit breakers (system-level, independent of strategies)
- **Data stale** (4.5): pause entries at S, flatten-or-hold (config) + alert at S2.
- **Spread blowout**: instrument spread > 5× median for > 60 s → pause entries on that instrument; existing stops remain.
- **Broker disconnect** > 30 s → pause entries; > 5 min → alert page; on reconnect → mandatory reconciliation before resuming.
- **Reconciliation mismatch** (positions/orders/balance differ from internal state beyond tolerance) → halt new orders, alert, require manual acknowledge (NN-9).
- **Exception in trading path** → fail closed (3.1 #5).
- **Clock skew** > 1 s vs broker server time → halt.
- **Runaway loop guard**: > N orders/min or > M rejects in a row → halt.
- **Market-wide shock** (e.g., 5-minute move > 8×ATR on any traded instrument) → pause entries for 30 min (configurable), alert.
All breakers are testable via a chaos test suite (Section 10.5) and have a Grafana panel showing state.

### 7.6 Strategy live kill criteria (evaluated daily by a job)
- Rolling 60-day live Sharpe < backtest OOS 5th percentile → pause.
- Live slippage per trade > 2× backtest assumption over 50+ trades → pause and re-calibrate cost model.
- Live drawdown > MC 95th percentile → pause.
- Feature/prediction drift breach (5.5) → fallback.
- Any structural change in the instrument (broker spec change, new regulation) → manual review.

---

## 8. EXECUTION AND ORDER MANAGEMENT (OMS)

### 8.1 Order lifecycle state machine
```
 NEW ──▶ RISK_CHECK ──reject──▶ REJECTED_INTERNAL
   │                 └─allow──▶ PENDING_SUBMIT ──▶ SUBMITTED ──▶ ACKED
                                    │                              │
                                    ├─timeout/err─▶ UNKNOWN ───────┼──(reconcile)──▶ resolved state
                                    │                              ▼
                                    │                     ┌── PARTIALLY_FILLED ──▶ FILLED
                                    │                     ├── CANCEL_PENDING ──▶ CANCELLED
                                    │                     ├── REPLACE_PENDING ─▶ ACKED (new params)
                                    │                     ├── REJECTED_BROKER
                                    │                     └── EXPIRED
```
Rules: transitions are explicit and logged; illegal transitions raise. `UNKNOWN` (we sent, no response) is a first-class state — the OMS MUST query the broker by `client_order_id` before doing anything else for that instrument. Never re-send an order in `UNKNOWN`.

### 8.2 Idempotency and identity
- `client_order_id = f"{env}-{strategy_id}-{instrument}-{ts_ms}-{uuid4[:8]}"` — passed to the broker where the API supports client tags (OANDA, IBKR, IG deal reference, cTrader `clientOrderId`) [VERIFY per broker]; where unsupported, a local mapping table binds broker IDs to client IDs before the request is considered submitted.
- Re-submission after a crash is impossible by design: on startup the OMS loads open orders from the broker and from its own store, reconciles, and only then accepts new intents.
- Target-position model: strategies/portfolio produce **target positions**; the OMS computes the delta vs current position and emits the minimal order set. Duplicate targets produce zero orders. This is the main defence against "double entry" bugs.

### 8.3 Execution algorithms (v1 scope)
- Market orders for signals with urgency (breakouts, stops).
- Passive limit at bid/ask (buy at bid) with a timeout → convert to market if unfilled after T seconds and signal still valid (captures half the spread on mean-reversion entries where the fill model in 6.2 validates the benefit).
- Bracket orders (native broker SL/TP) for every entry so that protection exists server-side even if our process dies. Server-side stops are mandatory in v1 for any position held while the bot is not in an active session.
- Rate-limited, jittered retries with exponential backoff on transient errors; never retry a non-idempotent call without first checking state.
- No TWAP/VWAP/iceberg in v1 (retail sizes don't need them); interface leaves room.

### 8.4 Reconciliation (NN-9)
- On start, on reconnect, and every 60 s (config): pull positions, open orders, balance from broker; compare with internal ledger; tolerance = 0 for position quantity, small tolerance for balance (fees/swap timing).
- Mismatch handling: log diff, set `RECON_MISMATCH` breaker, alert with the diff, hold. Manual "adopt broker state" command exists and is audited.
- Fills arriving out of order or duplicated (common on reconnect) are deduplicated by broker fill ID.

### 8.5 Transaction cost analysis (TCA)
For every fill record: decision price (mid at signal time), arrival price (mid at order send), fill price, spread at send, latency (send→ack, ack→fill), implementation shortfall in pips and bps. Daily TCA job aggregates by instrument/strategy/hour → feeds slippage model coefficients (6.3) → alerts if drift exceeds thresholds (7.6).

### 8.6 Broker adapters
| Broker / API | Assets | Integration notes [VERIFY all] | Priority |
|---|---|---|---|
| OANDA v20 (REST + streaming) | FX, some index CFDs | Clean JSON API, client extensions for tagging, practice accounts; good first adapter | 1 (FX) |
| Interactive Brokers (TWS API / IB Gateway; `ib_async`) | FX (IDEALPRO), index futures, CFDs in some regions | Most complete asset coverage; Gateway process must be supervised; pacing limits | 1 (indices/futures) |
| IG (REST + Lightstreamer) | FX & index CFDs | Widely available (incl. a South African entity); deal references; streaming via Lightstreamer client | 2 |
| cTrader Open API (protobuf/WebSocket) | FX/CFDs via cTrader brokers | Good for ECN-style FX; symbol IDs per broker | 3 |
| MetaTrader 5 (`MetaTrader5` Python pkg) | FX/CFDs via MT5 brokers | Windows-only terminal dependency; acceptable for a paper leg, poor fit for Linux prod | 4 |
| Paper broker (internal) | all | Uses live data feed, simulated fills via 6.2/6.3 — the paper-trading gate depends on it | 1 |
Adapter contract tests (`tests/integration/brokers/`) run against sandbox/practice accounts in CI nightly: connect, spec fetch, submit/cancel/replace, streaming fill receipt, reconnect + resubscribe, reconciliation, error mapping to the common `BrokerError` taxonomy (auth, rate-limit, rejected, transient, unknown).

Broker selection criteria (ADR-0005): regulatory status in the Principal's jurisdiction; API quality and tagging support; raw spreads + commission vs marked-up spreads (measure!); execution model (STP/ECN vs market-maker — matters for slippage and stop behaviour); server location (LD4/NY4 for latency and for the VPS placement decision); negative balance protection; practice account parity with live; historical tick data access; funding/withdrawal in ZAR and exchange-control paperwork.

---

## 9. LIVE OPERATIONS, DEPLOYMENT AND OBSERVABILITY

### 9.1 Environments
`backtest` (local/CI) → `paper` (VPS, live data, PaperBroker or broker demo) → `live-micro` (real money, minimum sizes) → `live`. Config overlays per env; the same container image runs all three live-ish envs.

### 9.2 Infrastructure
- VPS: Ubuntu 24.04 LTS, 2–4 vCPU, 8 GB RAM, NVMe, hosted in London (Equinix LD4 vicinity) for FX and UK/EU indices; secondary in New York (NY4 vicinity) only if US index latency proves material (it will not at ≥1m bars — ADR before spending). Latency is about reliability of fills at stops, not edge.
- Docker Compose services: `feed`, `trader` (strategies+portfolio+risk+OMS), `reconciler`, `postgres`, `redis`, `prometheus`, `grafana`, `loki`, `alertmanager`, `telegram-bot`. `trader` runs as a `systemd`-supervised container with `Restart=always`, but startup is gated on successful reconciliation.
- Time sync (3.4), unattended security upgrades, UFW allow-list, SSH keys only, fail2ban, non-root containers, read-only root FS where feasible.
- Daily Postgres backups (pgBackRest or `pg_dump` + off-site object storage); Parquet data mirrored off-site; restore drill quarterly.

### 9.3 Process supervision and recovery
- Heartbeat: `trader` pushes a heartbeat every 15 s to Redis and to an external dead-man's switch (e.g. healthchecks.io). Missed 3 → alert; missed 10 → external watchdog triggers "flatten via broker API from a separate minimal process" if configured.
- Crash recovery: state = broker truth + audit log replay. Strategies restore from their last checkpoint (`state()/restore()`); warm-up bars backfilled; nothing trades until reconciliation passes.
- Graceful shutdown: stop accepting bars → cancel non-protective working orders (keep SL/TP) → flush audit log → exit. Configurable "flatten on shutdown" for intraday strategies.
- Deploy procedure: blue/green not needed; deploy only in the Friday-close → Sunday-open window or with all strategies paused; run the parity replay test against yesterday's ticks on the new build before enabling.

### 9.4 Observability
Metrics (Prometheus): tick rate per instrument, feed staleness, bar build latency, strategy compute time, order counts by state, reject reasons, fill latency histogram, slippage (bps), equity, unrealised PnL, margin usage, open positions, drawdown from HWM, breaker states (0/1), reconciliation status, Redis/Postgres health, host CPU/mem/disk, clock offset.
Dashboards (Grafana, committed as JSON): Ops health; Trading (equity, DD, positions, exposures by cluster); Execution/TCA; Data quality; Risk (limit utilisation gauges); per-strategy pages.
Logs: structured JSON, correlation by `run_id`, `client_order_id`, `strategy_id`; no secrets, no full account numbers.

### 9.5 Alerting policy (Alertmanager → Telegram; page for P1)
- P1 (page): kill switch fired; reconciliation mismatch; trader down > 2 min during session; margin usage > 80%; broker auth failure.
- P2 (Telegram, 5 min): data stale; spread breaker; daily loss limit; drift breach; disk > 80%.
- P3 (daily digest): TCA summary, PnL by strategy, data-quality stats, strategy health.
Daily digest and P1/P2 alerts include the last 5 audit-log lines for context. Alert fatigue is a risk: every alert MUST have a runbook link and a documented owner action.

### 9.6 Telegram/Slack bot commands (auth by user-ID allow-list + per-command confirmation for destructive ones)
`/status`, `/positions`, `/pnl [today|week]`, `/pause <strategy|all>`, `/resume <strategy>`, `/flatten <instrument|all>` (confirm), `/kill` (confirm), `/recon`, `/limits`, `/logs <n>`. Every command is audited.

### 9.7 Runbooks (must exist before Gate 5; one file each in `docs/runbooks/`)
Broker disconnect; reconciliation mismatch; kill switch fired; data feed stale/gapped; VPS down; Postgres/Redis failure; DST week checklist; weekend rollover of futures; API key rotation; deploy & rollback; restoring from backup; "I think there's a bug in live — what now" (pause → snapshot state → replay locally → fix → parity test → resume).

---

## 10. TESTING AND QUALITY ASSURANCE

### 10.1 Test pyramid and gates
- Unit (fast, pure): features, metrics, bar builder, state machine, risk checks, money math (`Decimal`), time conversions. Property-based tests with `hypothesis` for bar builder invariants (high ≥ max(open,close), etc.), OMS transitions, and rounding.
- Integration: Postgres/Redis via Compose; broker sandbox contract tests (nightly).
- Replay/golden: deterministic backtests with stored expected reports; parity tests (6.8).
- Chaos (10.5).
- Coverage gate: ≥ 90% for `core/`, `risk/`, `execution/`; ≥ 80% elsewhere. Coverage is a floor, not a goal.
- Static: `ruff` (incl. security rules), `mypy --strict`, `bandit`, dependency audit (`pip-audit`), secret scanning (`gitleaks`) in CI.

### 10.2 Financial-correctness tests
- Pip value, PnL and margin computations vs hand-calculated tables for each instrument, long and short, including account-currency conversion.
- Swap accrual across the 17:00 NY boundary including Wednesday triple and holidays.
- Futures roll: back-adjusted series continuity; roll-date PnL attribution.
- Index CFD dividend adjustment sign and timing.
- Position netting/hedging semantics per broker (net vs hedged accounts) [VERIFY].

### 10.3 Time tests
As listed in 3.4, plus: bars around Sunday open have correct `ts_open`; daily bar boundary at 17:00 NY on both DST regimes; economic event scheduled at "08:30 ET" resolves to the correct UTC on both sides of US DST.

### 10.4 Anti-bias tests
Look-ahead canary strategy blocked; feature lint (no negative shifts); point-in-time calendar query; random-signal cost test; optimistic-assumption labelling appears in report caveats.

### 10.5 Chaos tests (run against paper env before Gate 4 and after every major release)
Kill `trader` mid-order → on restart no duplicate orders, positions reconciled. Kill Redis → trader halts safely. Broker stream drops → reconnect, resubscribe, reconcile, no missed fills. Inject stale ticks → breaker fires. Inject 10× spread → entries paused, stops intact. Inject crossed quotes → flagged, not traded. Clock step +2 s → halt. Fill arrives twice → deduped. Broker returns `UNKNOWN`/timeout → OMS queries, never resends. Config with unknown key → refuses to start. Secrets missing → refuses to start with a clear message.

### 10.6 Acceptance test evidence
Each gate (Section 13) requires a `docs/reports/gateN_evidence.md` linking CI runs, report JSON hashes, dashboard screenshots and the reviewer's sign-off.

---

## 11. SECURITY, COMPLIANCE AND RECORD-KEEPING

### 11.1 Security
- Secrets via `sops`/Vault → env at runtime; never in images, repos, logs, or Grafana. Rotation runbook; rotation tested.
- Broker API credentials scoped to trading only; withdrawals disabled at the broker; 2FA on the broker account itself; IP allow-listing where offered [VERIFY].
- Least-privilege service accounts for Postgres/Redis; network segmentation in Compose; no public ports except Grafana behind SSO/VPN.
- Dependency pinning with hashes; weekly `pip-audit`; SBOM generated in CI.
- Telegram bot: allow-listed user IDs, confirmation for destructive commands, rate limiting.
- Threat model doc (`docs/threat_model.md`): compromised VPS, leaked key, malicious dependency, broker API outage, fat-finger config. Each with mitigation and detection.

### 11.2 Regulatory and tax (informational — not legal advice; the Principal MUST confirm with a licensed adviser) [VERIFY all]
- Trading one's own capital generally does not require a financial-services licence. **Managing others' money, pooling funds, or selling signals/automation as a service** is regulated activity in most jurisdictions (South Africa: FAIS — an FSP licence, typically Category II for discretionary management; the FSCA is the regulator). The architecture MUST NOT be extended to third-party accounts without that legal step.
- South Africa specifics to confirm: OTC derivative (CFD) providers must be FSCA-licensed ODPs; exchange-control rules apply to funding offshore brokers (single discretionary allowance and foreign investment allowance limits, tax-clearance requirements); SARS may treat frequent trading profits as revenue (income tax) rather than capital gains — keep records that support either treatment and obtain a tax opinion.
- UK/EU brokers apply retail leverage caps and negative-balance protection; classification as "professional" removes protections — do not seek it to increase leverage.
- Market-abuse rules apply to algorithms (spoofing, layering, momentum ignition are illegal). Nothing in this spec permits placing orders without intent to trade; the OMS MUST NOT implement order-cancel-heavy tactics.

### 11.3 Record-keeping (built in, not bolted on)
Immutable audit log of every decision, order, fill, config change, kill-switch event, manual command; monthly PnL statements reconciled to broker statements; annual export pack for tax (trades, fees, swaps, FX conversions in account currency); retention ≥ 7 years [VERIFY local requirement].

---

## 12. ENGINEERING STANDARDS FOR AGENTS

### 12.1 Behavioural rules (apply to every code change)
1. **Think before coding.** State assumptions. If multiple interpretations exist, present them; do not pick silently. If a simpler approach exists, say so and push back.
2. **Simplicity first.** Minimum code that solves the problem. No speculative features, no single-use abstractions, no unrequested configurability, no handling of impossible scenarios. If it could be 50 lines instead of 200, rewrite.
3. **Surgical changes.** Touch only what the task needs. Match existing style. Do not "improve" adjacent code. Remove only orphans created by your own change. Report (don't delete) pre-existing dead code.
4. **Goal-driven execution.** Convert every task into verifiable criteria first ("write a failing test that reproduces the bug, then make it pass"). Present a plan as `step → verify` pairs before executing multi-step work.
5. **Financial code is different.** Use `Decimal` for money and prices at boundaries; never compare floats for equality; round only at display/broker boundaries with the broker's tick rule; write the hand-calculated expected value in the test before the code.
6. **Never weaken a safety mechanism to make a test pass.** If a kill switch blocks your test, the test is wrong or the switch found a bug.
7. **No silent exception swallowing** in `risk/`, `execution/`, `brokers/`. Catch → log with context → re-raise or transition to a fail-closed state.
8. **Logs and comments must not claim outcomes** ("this strategy is profitable") — only mechanisms and measurements.

### 12.2 Code conventions
`ruff` (line length 100, isort, pyupgrade, bugbear, security), `mypy --strict`, docstrings on public functions stating units (pips, bps, fraction, annualised), `slots=True` frozen dataclasses for events, async only at I/O edges, no global mutable state, dependency injection via constructor args (no service locators), one module per strategy, tests mirror `src/` layout.

### 12.3 Git and review
Trunk-based with short-lived branches; conventional commits; every PR: description with "Assumptions / Uncertain about / How to verify"; CI green; one reviewer agent (Red Team role for anything in `risk/`, `execution/`, `backtest/fills`). ADRs for: storage choice, broker choice, bar boundary rules, fill model defaults, vol-target level, any threshold change in `risk.yaml`.

---

## 13. PHASED DELIVERY PLAN WITH GATES

Each gate is a hard stop. Evidence pack per gate (10.6). The Principal signs off; agents may not self-certify a gate.

| Phase | Deliverables | Gate criteria (all required) |
|---|---|---|
| **P0 Foundations** (1–2 wks) | Repo skeleton (3.5); CI; config loader; event bus; `Clock`s; core types (3.6); logging/metrics scaffolding; ADR-0001 (stack), 0002 (types), 0003 (time rules) | CI green; `make demo` runs a hello-world strategy on synthetic bars in backtest and paper modes; time tests (3.4) pass |
| **P1 Data** (2–4 wks) | Ingest for ≥1 FX tick source + ≥1 index source; raw/clean layers; bar builder; quality pipeline + dashboard; point-in-time calendar; ≥ 8 yrs GBP/USD & EUR/USD ticks, ≥ 5 yrs indices | Gate 1 = 4.6 |
| **P2 Backtester** (3–5 wks) | Engine (bar + tick modes); SimBroker fills; cost model; metrics; WF/CPCV/PBO/DSR/MC; report generator; caveat auto-labelling; feature library v1 | Gate 2 = 6.8 |
| **P3 Strategy research** (4–8 wks, iterative) | S1–S4 + S6 + S9(a) minimum, each with research log, robustness suite, lockbox result, red-team review; portfolio combination; strategy YAMLs v1 | **Gate 3** per strategy: OOS (walk-forward stitched) after-cost Sharpe ≥ 0.6 and positive in ≥ 70% of yearly windows; CPCV median Sharpe > 0 with ≤ 20% of paths ≤ 0; PBO < 0.20; DSR ≥ 0.95; plateau stability; cost ratio < 50%; OOS trades ≥ 200; lockbox not materially worse than OOS (documented); red-team sign-off. **Portfolio**: combined OOS Sharpe ≥ 0.8, max DD ≤ 20%, MC 95th-pct DD ≤ 25% |
| **P4 Risk + OMS + Paper** (3–4 wks) | Risk manager & breakers; OMS state machine; PaperBroker; first real broker adapter (practice account); reconciler; TCA; Telegram bot; dashboards; runbooks draft; chaos suite | **Gate 4**: all pre-trade checks and breakers demonstrated (chaos tests pass); parity test passes; broker contract tests pass nightly for 2 weeks |
| **P5 Paper trading** (≥ 8 wks calendar, incl. ≥ 1 DST transition and ≥ 2 tier-1 event weeks) | Full stack on VPS; daily digests; TCA-calibrated cost model; runbooks final; restore drill | **Gate 5**: zero unexplained reconciliation mismatches; uptime ≥ 99.5% in sessions; paper PnL vs backtest-on-same-period within MC bands; measured slippage/spread fed back into 6.3 and Gate-3 metrics re-computed and still passing; every runbook exercised once; security checklist (Appendix B) complete |
| **P6 Live-micro** (≥ 12 wks) | Real account, minimum sizes, capital at risk the Principal can lose entirely without consequence | **Gate 6**: live TCA within 1.5× paper; no P1 incidents unexplained; kill criteria (7.6) never breached, or breached and handled per runbook; Principal review of every incident |
| **P7 Scale** (ongoing) | Increase risk budget in steps (×1.5–2 per step, ≥ 8 weeks each) while all gates hold; add strategies S5, S7, S8, S10 through P3→P6 individually | Each step requires the P6 criteria to hold at the previous size |

Schedule expectations are for planning only; **gates are met by evidence, not by dates**. Skipping P5's minimum duration is prohibited even if results look excellent — that is exactly when overconfidence is most dangerous.

---

## 14. AGENT ROLES, PROMPTS AND HAND-OFFS

Each agent is instantiated with: this document; the role prompt below; the current phase brief; read access to the repo; and the ADR index. Each agent returns: code + tests + ADR(s) + a `HANDOFF.md` containing assumptions, open questions, evidence links, and what the next role needs to know.

| Role | Owns sections | Primary outputs | Must not |
|---|---|---|---|
| **Architect** | 3, 12 | Skeleton, core types, ADR-0001..0003, interface docs | Write strategies; choose thresholds in `risk.yaml` |
| **Data Engineer** | 4 | Ingest, storage, bar builder, quality pipeline, calendar, Gate-1 evidence | Modify strategy or risk code |
| **Backtest Engineer** | 6 | Engine, fills, costs, validation suite, reports, Gate-2 evidence | Loosen fill assumptions without an ADR labelled "optimistic" |
| **Quant Researcher** | 5 | Hypotheses, features, strategy modules, research logs, Gate-3 packs | Touch the lockbox before step 7; hide failed variants; edit metric definitions |
| **Risk Officer** | 7 | Risk manager, breakers, kill criteria, `risk.yaml` with rationale | Accept a strategy's request to raise limits without Principal sign-off |
| **Execution Engineer** | 8 | OMS, adapters, reconciler, TCA, contract tests | Implement order tactics that lack intent to trade |
| **SRE / DevOps** | 9 | VPS, containers, monitoring, alerting, backups, runbooks, bot | Expose ports/dashboards publicly; store secrets in images |
| **QA** | 10 | Test suites, chaos harness, coverage gates, gate evidence templates | Mark a gate passed |
| **Red Team** | cross-cutting | Adversarial review: look-ahead, leakage, optimistic fills, silent failures, security holes; writes findings as failing tests where possible | Approve anything it authored |
| **Compliance Reviewer** | 11 | Verification of [VERIFY] items in 2 and 11 against primary sources; record-keeping checks | Provide legal/tax opinions (flags for the Principal's advisers instead) |

### 14.1 Role prompt template (fill per role)
```
You are the {ROLE} on the systematic trading platform described in SPEC.md v{X}.
Read SPEC.md §0–3 and §{OWNED_SECTIONS} in full before acting.
Your phase brief: {PHASE_BRIEF}.
Operate under SPEC §12: state assumptions first; prefer the simplest design; make surgical
changes; define verify-able success criteria before coding; never weaken a safety mechanism.
Deliver: code + tests + ADRs + HANDOFF.md (assumptions, open questions, evidence links).
Hard stops: you may not {MUST_NOT_LIST}. If blocked or uncertain, stop and ask the Principal.
Every numeric threshold you introduce needs a one-line justification in the config file.
Mark any fact you could not verify against a primary source with [VERIFY].
```

### 14.2 Red-team review checklist (applied to every strategy and every OMS change)
Does any feature see the current bar's close before the bar is closed? Any `.shift(-n)`, centred windows, or future-indexed joins? Are calendar fields used before `available_at`? Are fills on touch? Is spread constant? Is swap/financing modelled? Are flagged bars consumed? How many variants were tried and is that number in the DSR? Does the lockbox result appear unedited? Does the strategy trade during the 2016/2020/2022 windows and what happened? Would a random-signal strategy with the same turnover be profitable under this fill model? What happens on reconnect mid-fill? On duplicate fill? On `UNKNOWN`? Can any strategy raise its own limits? Are there secrets in logs? What does the system do at 21:59 UTC on the Sunday of a US-only DST week?

---

## 15. FAILURE-MODE CATALOGUE AND PROHIBITED PATTERNS

### 15.1 Failure modes (each maps to at least one test or breaker)
| Failure | Typical cause | Control |
|---|---|---|
| Great backtest, losing live | Look-ahead, optimistic fills, no costs, overfit | 6.2–6.5, red team, paper gate |
| Blow-up on a gap | Stops assumed to fill at price; weekend exposure unsized | 6.2 stop rule, 7.3 weekend sizing, server-side brackets |
| Duplicate orders after restart | No idempotency; resend on timeout | 8.1 UNKNOWN state, 8.2, chaos tests |
| Runaway loop | Bug emitting orders every tick | 7.4 rate limit, 7.5 loop guard |
| Wrong size by 10–100× | Lot/unit confusion; float rounding | Decimal, 7.4 size sanity, unit tests per instrument |
| Trading on stale prices | Feed stalled silently | 4.5 staleness watchdog, 7.5 breaker |
| DST misfire | Fixed offsets; London-open strategy fires an hour off | 3.4 zoneinfo + tests |
| Strategy drift | Regime change; broker spec change | 7.6 kill criteria, drift monitors |
| Death by costs | Short horizon vs spread | Cost ratio gate, spread filter, passive entries |
| Correlated blow-up | Same USD bet in several strategies/instruments | 7.2 cluster caps |
| Data-snooped "edge" | Many variants, best reported | Research log, DSR, PBO, lockbox |
| Silent exceptions | try/except pass in order path | 12.1 #7, fail-closed |
| Secret leak | Key in repo/log | gitleaks, sops, log scrubbing |
| Reconciliation drift | Missed fills, swap credits, manual trades | 8.4 |

### 15.2 Prohibited patterns
Martingale / grid / "averaging down to recover"; removing or widening stops on losing trades; hidden or hard-coded credentials; disabling breakers "temporarily"; parameter optimisation on the lockbox; touch-fill or zero-cost backtests presented as evidence; reporting Sharpe without trade count and period; float money math at boundaries; fixed UTC offsets for sessions; mid-only price storage; strategies that read the wall clock or the broker directly; any order-cancel tactic without intent to trade; claims of guaranteed or expected profit anywhere in the codebase or UI.

---

## APPENDIX A — Session and event reference (verify annually)
- FX week: Sunday 17:00 NY → Friday 17:00 NY. Daily rollover 17:00 NY.
- Session approximations (London local): Asia 00:00–08:00; London 08:00–16:30; New York 13:00–22:00; Overlap 13:00–16:30.
- US data 08:30 ET (NFP, CPI, retail sales, PCE); ISM 10:00 ET; FOMC 14:00 ET + presser 14:30 ET.
- UK data 07:00 London (CPI, GDP, labour market, retail sales); BoE MPC decision 12:00 London.
- DST: US 2nd Sun Mar → 1st Sun Nov; UK/EU last Sun Mar → last Sun Oct. Mismatch weeks: ~2–3 weeks in spring and 1 week in autumn.
- Index futures expiries: 3rd Friday of Mar/Jun/Sep/Dec (CME, Eurex); roll typically the week before [VERIFY per contract].

## APPENDIX B — Pre-live security & readiness checklist (Gate 5)
☐ Secrets in sops/Vault only; scan clean ☐ Broker key trade-only, 2FA on, IP allow-list ☐ Non-root containers, UFW, SSH keys only ☐ Backups + restore drill done ☐ Dead-man's switch active ☐ Alert routes tested end-to-end (send a real P1 test) ☐ All runbooks exercised ☐ Kill switch tested from Telegram and CLI ☐ Reconciliation passes on cold start ☐ Parity test on last 5 trading days passes ☐ Cost model coefficients updated from paper TCA ☐ Gate-3 metrics recomputed with those coefficients and still pass ☐ Capital in account = amount Principal can lose entirely ☐ Principal written sign-off

## APPENDIX C — Daily operations checklist (5 minutes, weekdays)
Check digest: uptime, recon status, data quality %, PnL by strategy vs expectation bands, TCA drift, margin usage, breaker events, upcoming tier-1 events (next 24 h), disk/backup status. Weekly: review research log, limit utilisation, correlation matrix, strategy health vs kill criteria; Friday: confirm weekend exposure policy applied; DST weeks: run the DST runbook.

## APPENDIX D — ADR template
```
# ADR-000N: <title>
Status: proposed | accepted | superseded by ADR-XXXX
Context: <problem, constraints, alternatives considered (≥2)>
Decision: <what and why>
Consequences: <trade-offs, risks, what becomes harder>
Verification: <tests/metrics that show the decision is working>
```

## APPENDIX E — Glossary
**ATR** average true range · **bps** basis points (1/100 of 1%) · **CPCV** combinatorial purged cross-validation · **DSR** deflated Sharpe ratio · **EWMAC** exponentially weighted moving-average crossover · **HWM** high-water mark · **Lockbox** data period untouched until final evaluation · **OMS** order management system · **PBO** probability of backtest overfitting · **Pip** 0.0001 for GBP/USD · **PSR** probabilistic Sharpe ratio · **Purge/Embargo** removing training samples whose labels overlap the test window, plus a gap after it · **TCA** transaction cost analysis · **Triple-barrier** labelling by first touch of profit, stop or time barrier · **TSMOM** time-series momentum · **Vol targeting** sizing positions to a constant expected volatility · **Walk-forward** sequential train/test re-optimisation.

## APPENDIX F — Reference reading (agents must verify editions/availability; citations below are from memory and may be imperfect)
- López de Prado, M. — *Advances in Financial Machine Learning* (bars, labelling, purged CV, DSR, PBO, bet sizing).
- Bailey, D. & López de Prado, M. — "The Deflated Sharpe Ratio"; Bailey, Borwein, López de Prado, Zhu — "The Probability of Backtest Overfitting".
- Carver, R. — *Systematic Trading* and *Advanced Futures Trading Strategies* (forecast scaling, vol targeting, diversification multipliers, position buffering).
- Chan, E. — *Quantitative Trading* and *Algorithmic Trading* (mean reversion, momentum, Kalman pairs, practical pitfalls).
- Jansen, S. — *Machine Learning for Algorithmic Trading* (pipeline patterns).
- Aronson, D. — *Evidence-Based Technical Analysis* (data-mining bias).
- Harris, L. — *Trading and Exchanges* (microstructure fundamentals).
- Grinold & Kahn — *Active Portfolio Management* (information ratio, breadth).
- Broker API docs: OANDA v20, Interactive Brokers TWS API, IG REST/Lightstreamer, cTrader Open API — primary sources for all [VERIFY] items in §8.6.

---
*End of specification. Freeze as v1.0 after Principal review; all subsequent changes via ADR and version bump.*