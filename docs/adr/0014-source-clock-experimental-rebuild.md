# ADR-0014: Explicit source-label interpretation and corrected reference rebuild

Date: 2026-09-06

Status: Experimental implementation; not a timestamp-contract certification,
source admission, gate approval or trading authorization.

## Context

The retained October 2024 FBS-Demo EURUSD values do not support blindly treating
their numeric timestamp labels as absolute UTC. Four independent release windows
support label minus three hours on October 4/10 and label minus two hours on
October 30/31. The separate seven-date HistData comparison supports the relative
alignment. This evidence does not establish every vendor's historical timezone,
the exact clock-change instant, or validity on other symbols and months.

Evidence is pinned by the experimental policy in
`configs/source_clocks/fbs_eurusd_october_2024_experimental.json`. Raw source labels,
prices and original acquisition artifacts must remain unchanged.

## Decision

Add an optional immutable `SourceClockPolicy`, with an explicit source, symbol,
label scope, version, half-open intervals, whole-second offsets and evidence
hashes. No policy is installed by default. Unknown and ambiguous labels fail
closed; the October 27 repeated wall-clock hour is excluded rather than assigned
an arbitrary fold. Source/date-specific interpretation occurs before quality,
receipt-time imputation, sorting, session assignment and bar construction.

The normalized policy identity contributes to the derived corpus identity and
clean-tick metadata. The original eight source fields and sequence remain the
raw lineage and duplicate identity. Existing callers without a policy retain
their prior behaviour and corpus identity. A generic event normalizer must not
become a fitted clock-offset correction mechanism.

Use a separate producer mode for the clock experiment. Select source artifacts
by their mapped interval coverage of the true-UTC reference month, prehistory and
lookahead. Original acquisition date labels are not corrected canonical session
labels. Report mapped request coverage separately from observed tick coverage.

The original acquisition plan requested only Sunday-through-Thursday open-date
intervals under the old clock assumption. After correction, four October Friday
tails are acquisition-unknown: 18:00–21:00 UTC on October 4, 11, 18 and 25.
The November 1 lookahead also needs 19:00–21:00 UTC. Any supplemental retrieval
must retain its own exact request labels, native responses, canonical bytes,
hashes and repeated-response comparison. It must not replace the original raw
archive, imply a closure, fabricate quotes or mix in HistData ticks.

## Verification and rollout

Test strict policy parsing, source/scope rejection, transition exclusion,
monotone conversion, identity changes, immutable raw lineage, corrected bars,
coverage holes and independent rebuild equality. Pin policy, evidence,
implementation, calendar and counting-policy inputs before a long run, and
verify them again after it.

Rebuild the full selected source inventory twice into new directories and rerun
the unchanged reference acceptance evaluator on that exact output inventory.
Preserve all original failed reports. A producer reproducibility pass is not a
quality pass, and an acceptance calculation cannot invent human review or gate
approval. No risk threshold or counted-flag rule is changed by this ADR.

The current model code is an engineering foundation, not a genuine model-fitting
or live-execution implementation. After corrected-source acceptance, the next
separate deliverable is a phase-appropriate offline backtest/zero-edge proof and
training consumer. Live orders remain disabled; this work does not implement or
approve an OMS, broker order adapter or production risk controls.
