# Corrected reference month — ten-minute context review

## Result and scope

The ten newly relevant minutes contain **21 retained tick/condition pairs: 14
PRICE and seven SPREAD**. All 21 predicates are corroborated by targeted
arithmetic on the existing corrected ticks. The independent arithmetic review
found no discrepancy within this scope. This is evidence that the rules fired as
specified, **not proof that the broker's quotes were economically erroneous**.
All economic quote verdicts remain unresolved; no flag was cleared.

The completed acceptance remains **16 / 4,410 = 0.362811791%**, failing the
unchanged strict <0.1% criterion. No new capture, full rebuild, detector execution,
acceptance evaluation, source adoption or threshold change was performed here.
Training and execution remain disabled.

Targets are fixed by the source-sequence/condition cross-reference in
[the attribution report](corrected_minute_attribution_20260906.md): they are the
ten minutes not covered by the previous condition review, not a chosen favorable
sample. The six previously reviewed minutes retain their separate evidence.
Times below use the bounded experimental corrected-UTC interpretation.

| October 2024 UTC minute | Condition | Tick/condition pairs | Price events reaching/crossing baseline within five source rows |
|---|---|---:|---:|
| 01 12:35 | PRICE | 1 | 0 |
| 01 14:00 | PRICE | 1 | 0 |
| 04 12:30 | SPREAD | 7 | Not applicable |
| 10 12:30 | PRICE | 5 | 4 |
| 11 12:30 | PRICE | 2 | 1 |
| 11 13:36 | PRICE | 1 | 0 |
| 11 14:00 | PRICE | 1 | 0 |
| 22 13:34 | PRICE | 1 | 0 |
| 24 12:30 | PRICE | 1 | 0 |
| 25 12:37 | PRICE | 1 | 0 |

## Arithmetic and retracement

The helper read 16 of the October tick file's 83 row groups. It retained the
inclusive prior-hour history, the preceding quote needed for the first return,
and five actual following source rows for each candidate. All selected contexts
passed the valid-quote, nonregressed-timestamp and consecutive-source-sequence
guards. The candidate was excluded from its own history; the extra predecessor
was excluded from the spread median. Missing or incompatible context would leave
a predicate unresolved rather than silently shortening the proof.

For PRICE, the unchanged rule requires an absolute return strictly above 20
prior-hour population standard deviations and later entry into the frozen
pre-jump price band within five source rows. It does not require an exact return
to the original midpoint. All 14 candidates agree under fresh Decimal precision
28 and 50 calculations, including identical first-band-entry rows (row one or
two). Five events reached or crossed the baseline; nine did not fully retrace;
none exactly equalled the baseline. For example, the October 1 12:35 event's
maximum retracement was about 17.65%, yet its next source row entered the band.

The nearest price candidate is about 20.02056237 standard deviations. Its
candidate-price margin is approximately `1.69465322e-7`, versus a fresh 28/50
band difference of approximately `2.42125e-31`. These are sensitivity results,
**not a bound on accumulated production add/subtract rounding or a bit-identical
rolling-state replay**. The review does not claim to rule out every possible
implementation defect elsewhere.

The seven SPREAD events all occur at October 4 12:30. Their prior-hour median
spread is `0.000110000000000000`; observed spreads are approximately `0.001120`
through `0.001370`, or **10.1818–12.4545 times** the median. All exceed the strict
10-times predicate. These are seven direct-median checks, not seven additional
dual-precision price checks. Neither wide quotes nor partial retracement alone
establish bad source data.

## Reproduction and evidence bindings

A three-code-cell notebook verified input hashes, recomputed the saved condition
and summary objects exactly, and completed with zero cell errors. It uses the
existing analysis interpreter and performs no broker calls. Raw/original-capture
lineage is a separate check and is not asserted by this arithmetic notebook.

| Artifact | SHA-256 |
|---|---|
| `build/preuse/review_corrected_new_context_v1.py` | `9eab9cbc404ba64b206daf96aef788b6c1656530a3b792ae43c82c80724b3a47` |
| `build/gate1/corrected-new-minute-context-20260906-v1/conditions.json` | `2ae615bc2c2a01ae790a337cf6a8c04f1089d50fe5ea3b867c18ab8a625f3e04` |
| `build/gate1/corrected-new-minute-context-20260906-v1/summary.json` | `9723c6f9961463d6d8318dda6a2f764beb40176d39b732d537f99e230fcedeca` |
| `build/gate1/corrected-new-minute-context-notebook-20260906-v1/context.executed.ipynb` | `6e0a278998b85ce65e9b64b74875b1f5862bfcd3e2b6ab8d2729e70aa15d9778` |
| `build/gate1/source-clock-rebuild-run-20260906-v1/launch.json` | `ea7d16572cd319c92a8a12e1ff067f28ea3f7d9a2643c5337604d3735d5acf14` |

The notebook receipt binds the context helper and outputs. The launch pin above
is supplemental: neither it nor the helper is represented in the context
summary's own input map, and no sealed artifact was rewritten to imply otherwise.
The outer launch's previously documented missing producer-code pin remains a
separate historical completeness defect, not a newly discovered content mismatch.

## Presentation limitation

The Data Analytics build-report workflow produced a source-backed attribution
artifact, but both its validator and canonical HTML exporter rejected genuine
Python provenance because they require SQL query text. No SQL was fabricated and
no invalid report was displayed or published. The executed notebooks, JSON and
CSV remain available; this presentation limitation is separate from the actual
numerical acceptance failure.
