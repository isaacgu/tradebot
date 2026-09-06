# Corrected reference month — exact flagged-minute attribution

## Result

The completed September 6 run does not meet the unchanged quality criterion:
**16 / 4,410 expected liquid minutes = 0.362811791%**, not strictly below 0.1%.
The bounded attribution reproduced the saved result exactly without another
broker request, full rebuild, detector run or acceptance evaluation.

| Annotation | Expected-liquid minutes | All canonical reference-session minutes |
|---|---:|---:|
| Retrospective price outlier | 13 | 20 |
| Tick gap | 2 | 2 |
| Spread outlier | 1 | 3 |
| Distinct minute union | **16** | **24** |

There is no overlap between the three groups inside the expected-liquid bins.
Outside that subset, one minute carries both spread and price annotations; this
is why the full canonical category counts sum to 25 but their union is 24.
No expected liquid minute bar is missing. An observed detector annotation is
not, by itself, proof of an economically erroneous broker quote.

## Scope and reconciliation

The exact FBS-Demo/EURUSD October 2024 canonical session-close month contains
4,410 expected minute bins: 13:00–16:30 Europe/London, weekdays excluding October
3 and 14. Independent local-time enumeration of those 21 × 210 bins equals the
production calendar resolver's complete set, including the UK clock transition.
The original policy, clock interpretation, source files and detector thresholds
were unchanged.

The extractor hashed the pinned acceptance/producer reports and exact six-file
M1-bar/tick inventory, checked clean schemas, metadata, corpus IDs and footer row
totals, and scanned flag columns across 5,905,147 selected ticks including context.
Only 176 rows carrying non-provenance flags were decoded for detail. Known causal
provenance exclusions were checked against the policy before filtering; unknown
causal flags and all retrospective flags were retained. Bar and causal-tick
flags were unioned by minute; retrospective flags were kept separately until
their policy treatment was applied. All 24 counted canonical minutes have bars;
the expected subset spans 11 dates.

This custom diagnostic relies on the completed acceptance run for full per-row
scope and flag-list validation of unselected provenance-only rows. It is not a
replacement for that full reader or a new gate evaluation. The independent agent
review found no slicing or double-counting defect after the extractor's initial
assertion/import fixes. A three-cell notebook recomputed the entire attribution
object, verified exact equality and completed without cell errors.

## What the previous review covers

Cross-referencing stable source sequence plus condition, with exact bid/ask checks,
finds six reviewed tick/condition pairs in six of the current 16 minutes. The
other ten minutes have no matching condition review in the earlier file. This
means they were outside that review's scope, not that their ticks are missing or
newly created.

The two matched gap events are now labelled **October 16, 12:16 UTC** and
**October 25, 12:27 UTC**. Their earlier condition contexts measured gaps of
10.854 and 11.775 seconds respectively. The sole counted spread minute is
**October 4, 12:30 UTC**, containing seven spread-flagged ticks.

The existing price detector marks a sufficiently large return followed by entry
into its frozen pre-jump band within five source rows. It does not require exact
return to the original price or independent-feed corroboration. A legitimate
fast market with partial retracement can satisfy that rule. Therefore the next
useful work is reviewing the newly relevant contexts, preserving original raw
identities and distinguishing rule firing from bad-quote adjudication. No
annotation was cleared and no threshold or counting treatment was changed here.

Follow-up: the [ten-minute context review](corrected_new_minute_context_20260906.md)
now corroborates all 21 newly relevant tick/condition predicates, with a separate
executed notebook. It distinguishes five full/crossing price retracements from
nine band-only retracements; no quote is thereby adjudicated erroneous or cleared.

## Provenance and remaining limitations

- Attribution: `build/gate1/corrected-minute-attribution-20260906-v1/attribution.json`,
  SHA-256 `4456e41b96c1323e81d41dd4c5e1a20b3c8afbda15fe10748e8f8d3f3439a76f`.
- Exact 24-minute CSV, with the 16 in-scope rows explicitly marked:
  `build/gate1/corrected-minute-attribution-20260906-v1/minutes.csv`,
  SHA-256 `9c1e7d668794f4a88864accc47d28af9ba39030a05c9f0a8af5cf2cbf470a65a`.
- Extractor: `build/preuse/attribute_corrected_minutes_v1.py`,
  SHA-256 `1dd5233c02c6ce06fb2ab28c6753c60d93bff20a065c504dfc147ee0eef5ffb7`.
- Stable-sequence cross-reference:
  `build/gate1/corrected-minute-attribution-crossref-20260906-v1/crossreference.json`,
  SHA-256 `072a61b0d2ba42a74fb89be2c61369d31e46b36b80266d4b06e5d16985233809`.
- Executed notebook:
  `build/gate1/corrected-minute-attribution-notebook-20260906-v1/attribution.executed.ipynb`,
  SHA-256 `4e408dd6a4f9441e4ce7f4155834c9613564ffb93d0dee62a3122b62e74f4d3a`.
  Its `receipt.json` binds the extractor, result, CSV and cross-reference hashes.

The independent producer audit retains **FAILED, 366 / 367 checks passed**. Its
sole failure is that `src/tradebot/__init__.py` is pinned by the producer but was
omitted from the outer launch's 82-file list. Its actual bytes match the producer
pin; this is an outer-record completeness gap, not an observed content mismatch.
Neither the launch nor the failed audit was rewritten. Audit:
`build/gate1/source-clock-producer-root-audit-20260906-v1/audit.json`, SHA-256
`b06639056c5e024c0a2d02c1a6ec19ac38cf77a1cfa0b794b5ef59132a04c4e4`.
Additive `assessment.json` SHA-256:
`66758089467f73e87ae9700bf904fd621868a72fd7574c7056dc809823667be2`.
Future launchers should include all producer implementation pins; metadata alone
does not justify repeating this completed corpus rebuild.

The formal acceptance remains `INDETERMINATE` because its frozen diagnostic has
no final hash-bound human decisions, alongside the explicit numerical failure.
Isaac's acceptance and his report of Delsa's acceptance are preserved separately;
neither is being requested again here. The scoped clock remains empirical, receipt
timestamps are imputed, and source validity is not certified by repeated broker
responses. Training and execution remain disabled.
