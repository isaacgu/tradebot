# ADR-0015: Conservative synthetic order matching

Status: engineering implementation candidate, 2026-10-15. No Principal or
independent-reviewer approval is asserted; no phase gate is released.

SPEC v1.0 remains unchanged, SHA-256
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`.

## Context

The Principal requested resolving data quality, finishing backtesting and fitting,
and training only once the checks pass. Existing ADR-0012 implemented MARKET/GTC
synthetic matching and exact supplied-cashflow attribution. The observed data gate
still fails; that does not prevent testing additional execution semantics on
explicitly invented paths. It does not authorize using unapproved real data.

## Decision and executable interface

Keep `match_market_order` and its existing report unchanged. Add
`backtest.orders.match_synthetic_order` for one parent GTC order and optional
server-resident stop-loss/take-profit. `OrderModel` requires an explicit
`MarketModel`, fixture price tick and touch-sensitivity boolean. Results contain
the entry/exit fills, source quote and ordinal, trigger ordinal, actual count of
ticks traversed and optimistic assumptions. There are no implicit financial
defaults, persistent broker state or external services.

The public boundary rejects every venue except `Synthetic/<FX pair>`, equal
currency pairs, non-GTC instructions, excessive size, every quality flag and
invalid numerical representation. It validates every quote through true EOF,
including after a completed bracket. Invalid tails and iterator failures cannot
be hidden by an earlier candidate fill. Prices use the existing independent exact
Decimal arithmetic; ambient precision cannot change cash or fill prices.

### Venue time and ordering

Parent orders become active only at a venue tick strictly later than
`submitted_at + model.latency`; submission must not precede decision availability.
Ticks are validated in nondecreasing venue time, retaining input ordinal to break
ties. Receipt and availability are copied to resulting fills. They neither
trigger execution nor add a second latency. The caller remains responsible for
bus admission by availability and preventing duplicate accounting.

### Limit orders

The conservative buy criterion requires bid <= limit minus one explicit tick;
sell requires ask >= limit plus one tick. Additionally the executable ask plus
slippage must be <= the buy limit, or executable bid minus slippage >= the sell
limit. The extra condition prevents a wide spread from manufacturing executable
liquidity at a price never quoted on the required side. Fill price is the limit:
no price improvement is assumed and no fill exceeds the client's limit.

Touch sensitivity replaces the trade-through increment with zero, never removes
the executable-side/slippage check, and always records `LIMIT_TOUCH_FILLS` in the
result, even when the fixture produces no fill. A quote-to-fill adverse price
difference is retained, including foregone improvement, so existing supplied-cost
accounting reconciles to the exact fill rather than a separately invented price.

### Stop and stop-limit orders

Buy stops trigger when ask >= stop; sell stops when bid <= stop, including
equality. This is the explicit opposite-side/executable-quote convention for this
engineering model, not a claim about a chosen broker's contract. A stop is already
resident after parent activation; trigger does not incur another transport delay.
It executes at that first triggering quote plus adverse slippage. Gaps never get
the missing stop price.

Stop-limit activation is sticky. Once triggered, the limit rule applies on that
quote and later quotes; a later retreat below/above the stop does not disarm it.
The order may remain triggered but unfilled, including after a gap.

### Brackets

Bracket legs are assumed server-resident and activate atomically with entry.
Evaluate them after entry on its quote and every later ordinal, including later
quotes with equal timestamps. Thus a gapped parent fill whose executable exit
quote already breaches its SL exits immediately; delaying protection to a
convenient subsequent quote would introduce optimism. SL is a stop on the exit
side. TP is a limit on the exit side, with the same conservative eligibility.
The first exit cancels its sibling; deterministic child IDs append the exit role
to the parent client ID. This stateless helper does not provide durable OCO/OMS
reconciliation or idempotent booking.

Long SL must be below TP and short SL above TP when both are supplied. A single
leg remains allowed. This ordering is checked before touching the input feed.

### Bar-resolution sensitivity

`backtest.bracket_bars.match_synthetic_bracket_bar` evaluates a bracket already
active strictly before an entire invented bar. Its input contains separate bid
and ask OHLC, not midpoint bars with an invented spread. Every corresponding ask
OHLC component must exceed its bid component. Brackets installed within the bar
are rejected because their path cannot be established from those extrema.

When both SL and TP are eligible, SL is selected and `both_legs_reached` records
the ambiguity. A gap stop uses the worse opening executable quote; a non-gap stop
uses its level plus adverse slippage. TP requires trade-through on the executable
extreme itself (bid high for a long exit, ask low for a short exit), as well as
the slippage check, and fills no better than its limit. This is deliberately
stricter than the tick rule: separate bid/ask extrema may occur at different
instants and cannot be combined to invent simultaneous executable liquidity.
Output labels
`BAR_PATH_ADVERSE_FIRST` and `INTRABAR_EXECUTION_TIME_UNKNOWN` and is available only
at the supplied bar availability. It is not a Fill with a fabricated intrabar
timestamp. Tick paths, when supplied separately, can resolve the true order.

## Verification and limitations

Unit tests cover both sides, strict activation, gaps, sticky stop-limits, bracket
OCO, same-quote and equal-timestamp ordering, limit price protection, touch labels,
exact cent-level supplied-cost known answers for all four parent types,
availability retention, deterministic report bytes, adverse-first bar ambiguity,
low Decimal precision and failure after an apparent fill. They are invented
fixtures, not economic strategy results.

This is not completion or acceptance of SPEC P2. Remaining requirements include
multi-instrument portfolio/Risk/OMS integration, complete bar-mode entry matching,
session/margin/size-band rejects and configured partial fills, broker-evidenced
commission/financing/rollover/dividend schedules, stored point-in-time FX rates,
the size/ATR/spread/jump slippage model and calibration, full metrics/statistical
reports, arrival-driven intent parity, and the approved-real-data zero-edge test.
The constant slippage and full-fill bounds here remain invented uncalibrated
engineering assumptions. Neither this ADR candidate nor a passing unit test
waives Gate 1, Gate 2, data authorization, model promotion or live-execution gates.
