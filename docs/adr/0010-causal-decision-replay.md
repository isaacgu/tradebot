# ADR-0010: Causal decision replay and observable research inputs

Status: implemented engineering preparation; no phase-gate acceptance implied.

Built against SPEC v1.0 SHA-256
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`.

## Context and authorization

The Principal requested beginning the core engine while the Gate-1 data evidence
remains in collection, and explicitly authorized coordination with the UI task.
The existing demo processes three synthetic candles. Collected information needs a
causal, inspectable route into decisions before financial simulation can evaluate it.
This increment prepares that route; it does not approve Gate 1, evaluate a strategy
for Gate 3, select a broker, or alter a Principal-reserved threshold.

Alternatives considered: extend the Gate-0 fixture; build a complete cost/fill and
strategy-evaluation stack in one change; or add a separate, bounded decision replay
that reuses the established event bus, clock and BarStrategy interface. The third
option preserves the Gate-0 evidence and permits focused anti-leakage verification.

## Decision

Add pure feature functions, a checkpointable momentum engineering candidate, a
snapshot reader and a streaming offline runner. Each venue-qualified FX instrument
has separate state. The runner exposes only a timestamp view to the strategy and
admits bars through the existing EventBus at their availability time. Forecast time
is the actual decision time; delayed historical bars never backdate that decision.

An input manifest enumerates already frozen clean-bar files, exact hashes, venue,
timeframe and a dataset ID computed using the existing storage convention. This ID
identifies the selected bar-file manifest, which may differ from the full corpus ID.
No discovery glob follows the active acquisition directory. The reader validates
paths, schema, identity metadata, hashes and timestamps before delivery, then checks
hashes again at end of input before the result can be published. Python's Bar uses
microsecond timestamps: sub-microsecond values are rejected, never silently rounded.
The stored availability must equal the Bar's receipt-based availability contract.

Primary ordering is `(available_at, source, seq)`. Because sequence uniqueness is
scoped to `(source, instrument)`, equal primary keys on different instruments have
the explicit final tie-break `instrument`. An identical full key is rejected.
Sequences must increase within each source/instrument, and sources cannot change
inside one instrument's state. Overlapping or regressing market intervals fail.
Index series are outside this candidate's scope and cannot produce forecasts.

The feature window contains only admitted, clean observations. Horizon lengths
count observed bars; missing intervals are not forward-filled. Elapsed window time
and gap seconds expose this distinction. Each input bar's elapsed duration must
match the selected timeframe. Calendar-derived expectations remain owned by P1.
All quality flags, including unknown ones and `TS_RECV_IMPUTED`, suppress the current
forecast and reset history. Missing spread has the same effect. A subsequent clean
bar begins a new warmup. This strict policy can yield no forecasts on incomplete
history; a successful replay is still only a software result.

## Prospective feature and candidate definitions

These settings were selected before any financial evaluation. No search or fit
is performed. The executable fixture contains invented prices only.

| Quantity | Definition and justification |
|---|---|
| Momentum horizons | 8, 16, 32, 64 observed bars; follows SPEC S1's candidate horizons |
| Log return | `log(close[t] / close[t-h])`; dimensionless |
| Volatility | Sample standard deviation of the last 32 one-bar log returns; one S1 horizon supplies a transparent initial scale |
| ATR | Arithmetic mean of the last 14 true ranges, including previous close; SPEC's short ATR period, in quote-price units |
| Spread pressure | Current mean spread / ATR; dimensionless diagnostic, no cost or risk threshold implied |
| Warmup | `max(momentum horizons, volatility period, ATR period) + 1` bars, enough preceding closes for every calculation |
| Candidate component | Horizon return / `(one-bar volatility * sqrt(horizon))` |
| Candidate forecast | Equal mean of horizon components, multiplied by 10 and capped to the existing Forecast type's ±20 scale |
| Zero volatility | Explicit abstention, with no epsilon or invented denominator |

The multiplier 10 is an **uncalibrated engineering scale**, not the fitted
average-absolute-forecast scale required for strategy evaluation by SPEC 5.4.
Forecast values are neither probabilities nor position quantities. S1's economic
hypothesis and eventual holding horizon remain subject to SPEC 5.1; a one-minute
synthetic software fixture supplies no evidence for S1 at an intraday horizon.
Tick counts and spread pressure are recorded diagnostics; they do not influence
the momentum formula. Calendar, macro, cross-asset and tick microstructure features
remain explicitly unconnected. There is no model training or parameter selection.

State checkpoints retain bounded history, exact prices and timestamps, config and
instrument identity, and the last observed ordering anchor even after suppression.
Restoration validates the complete checkpoint before mutating state.

## Publication and UI contract

Write every decision to `decisions.jsonl` while retaining only summary counts and
the most recent decision per instrument in memory. Each trace row records source
sequence, market/availability/decision timestamps, quality flags, feature values,
forecast and an abstention reason. Publish complete artifacts in
`build/research/decision-replay/<content-derived-run-id>/` only after input and
implementation identity checks finish. The summary and trace have SHA-256 hashes
in `manifest.json`. An atomically replaced `latest.json` is discovery metadata;
the UI must verify its referenced report hash before presenting it.

The summary records SPEC, code-file hashes, commit or UNCOMMITTED, input manifest,
config, runtime identity, absent financial stages and explicit evidence class
`engineering-decision-replay-only`. `COMPLETED` means replay finished, not research,
data or trading acceptance. The UI must retain that distinction. Itemized caveats
name imputed-timestamp sources. No position, PnL, confidence or gate approval is
inferred from a forecast. A changed immutable artifact fails on rerun.

## Success criteria and boundaries

- Hand-computed feature cases, clean warmup, quality reset and zero-volatility tests pass.
- Changing a future suffix leaves earlier decisions identical.
- Interleaving two pairs leaves each pair's decisions identical to its solo replay.
- A resumed strategy produces the same decisions as uninterrupted processing.
- Late, duplicate, malformed, mixed-source or future observations cannot contaminate state.
- Snapshot tampering or a failure at end of input cannot publish a completed report.
- Repeated synthetic runs produce identical trace, report and run identities.
- Generated artifacts explicitly contain no costs, PnL, orders or approvals.

The next increment after data acceptance is a conservative cost/fill harness with
hand-calculated financial tests. Only then can frozen hypotheses, declared data
splits, trial counting, walk-forward and lockbox evaluation measure decision value.
This ADR changes no gate criterion, risk limit, circuit breaker or live setting.
