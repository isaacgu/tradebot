# Data-quality engineering resolution — 2026-10-15

## Outcome and scope

The retained FBS-Demo/EURUSD October 2024 reference calculation still measures
**16 / 4,410 expected liquid minute bins = 0.362811791%**, above the unchanged
strict **<0.1%** criterion. Its formal result is also `INDETERMINATE` because
final artifact-bound approval evidence is absent. These are separate issues:
binding approvals would not turn the measured numerical failure into a pass.

This review found and fixed one software reporting defect: a measured
`SPREAD_OUTLIER` breach was omitted from `TickQualityPipeline.summary()`'s failed
checks. Such a stream could report `INDETERMINATE` because of warm-up/calendar
uncertainty instead of reporting the already measured failure. The summary now
reports `FAILED`; it still reports calendar uncertainty separately. Spread flags,
quote eligibility, timestamps, thresholds and the reference evaluator are
unchanged. **This fix neither resolves nor reduces the 16 flagged minutes.**

No raw or clean corpus was rewritten, no historical detector or acceptance run
was repeated, and no source-clock policy or data authorization was adopted.
Training and order execution are not released by this note.

## Dataset and evidence checked

The reference producer covers 5,905,147 ticks, 35,569 one-minute bars and 25 daily
bars including context. The acceptance unit is an expected liquid one-minute
bin, not a tick, day or acquisition chunk. Its denominator is 4,410 minutes;
zero expected minutes are missing. The counted union contains 13 PRICE minutes,
two GAP minutes and one SPREAD minute without overlap.

The earlier bounded context analysis covers ten newly relevant minutes containing
21 tick/condition pairs: 14 PRICE and seven SPREAD. It corroborates all 21 rule
predicates. The companion lineage check resolves 147 neighbour references to 117
unique original source rows across seven standard chunks, with no mismatches in
the eight raw fields, corrected timestamps, Decimal prices or stable sequences.
That lineage result is bounded, not a full-corpus or independent-market audit.

The following retained artifacts were read and their current SHA-256 values
checked during this review. Historical outputs remain intact:

| Artifact | SHA-256 |
|---|---|
| `build/gate1/source-clock-reference-acceptance-20260906-v1/report.json` | `9b0ca7ccda33d5c09f669733d48d67fa81f730561a3f22e49f5451ab81ec898f` |
| `build/gate1/source-clock-reference-rebuild-20260906-v1/report.json` | `ce0af1e24de335d869e887a3c6911fd07b5252b3ea6d3a4433760c3ac56f41ba` |
| `build/gate1/corrected-new-minute-context-20260906-v1/summary.json` | `9723c6f9961463d6d8318dda6a2f764beb40176d39b732d537f99e230fcedeca` |
| `build/gate1/corrected-new-minute-lineage-20260906-v1/summary.json` | `8ab4e8cf2cb8f81c5ba109908b7dd5b0fa2c845bf2576f35d80242f7731f2a83` |
| `configs/source_clocks/fbs_eurusd_october_2024_experimental.json` | `a5c194fd999d24fda9b424bf84cd6ffce8ac51cf287074c8342faaa8da5b3816` |

## Findings and analytical risk

1. **High severity; high confidence — the measured reference rate fails.** The
   counted minutes exceed the maximum four allowed by a strict 0.1% boundary for
   this denominator. Existing arithmetic and retained-source lineage provide no
   demonstrated ingestion defect whose correction would clear these flags.
   An unusual market quote is not automatically an economically bad quote.

2. **High severity; unresolved source interpretation — historical timestamps.**
   The minus-three/minus-two-hour mapping remains `EXPERIMENTAL_ONLY`, bounded to
   the existing October experiment; the repeated fallback hour is excluded and
   encountering a row there must stop the experiment. Isaac confirmed that no
   FBS technical answer has been received. General personal data-use permission
   is already recorded and is not the missing item.

3. **High severity; semantic ambiguity — retrospective price reversion.** The
   current detector treats entry into the frozen ±20-sigma pre-jump band within
   five source rows as reversion. It does not require an exact baseline return
   or full retracement. In the bounded review, five of 14 PRICE candidates reach
   or cross the baseline; nine only partially retrace, and none equals it. The
   implementation and independent arithmetic agree, but agreement does not
   establish the intended economic meaning of SPEC §4.4's word “reverts.”
   Changing the band/horizon after observing this month would be a policy change,
   not a demonstrated software correction; no such change was made.

4. **Medium severity; high confidence — summary failure precedence, fixed.**
   The streaming summary omitted a measured spread-rule failure. Regression
   tests now require that known failure to dominate incomplete context without
   inventing calendar evidence or excluding the flagged quote from bars.

## Temporal evidence and public documentation

This is a review of the retained September 6 diagnostic run, not a fresh broker
capture or a newly validated market month. It does not claim a new time-series
trend or that the data became suitable merely through elapsed time.

Primary documentation was rechecked on October 15. MetaQuotes still describes
the Python `copy_ticks_range` timestamps as UTC and requires UTC-aware request
times. This is not a broker-specific historical exception.
[MetaQuotes API reference](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py)

FBS's dated October 25, 2024 notice describes the October 27 and November 3 winter
schedule adjustments for listed instruments; it does not identify EURUSD/GBPUSD
historical Python tick serialization or adjudicate the flagged quotes. Its
current hours page describes EET platform display and seasonal offsets, not a
historical `time_msc` override. The unresolved distinction therefore remains.
[Dated FBS notice](https://fbs.com/news/trading-schedule-changes-due-to-the-winter-time-shift),
[FBS trading hours](https://fbs.com/trading/trading-hours?lang=en)

## Verification and smallest remaining remediation

The new synthetic regressions cover a spread breach versus missing context,
the exact 10-times non-breach boundary, upward/downward partial-band re-entry,
late reversion outside the source-row horizon, and locked/crossed/regressed
future quotes that must not confirm a candidate. Partial-retracement tests freeze
current behavior for review; they do not approve its economic interpretation.

Targeted verification command:

```sh
uv run --no-sync ruff check src/tradebot/data/quality.py tests/unit/data/test_quality_failure_reporting.py
uv run --no-sync pytest tests/unit/data/test_quality.py tests/unit/data/test_quality_calendar.py tests/unit/data/test_quality_failure_reporting.py -q --no-cov
```

Result: Ruff passed; **33 tests passed**, including the eight new regression
cases. These are software checks, not a new real-data acceptance result.

The remaining work is source-backed adjudication of historical timestamp/session
facts and flagged quote conditions, plus explicit review of the reversion
definition. Preserve the existing unsuccessful run. If evidence establishes a
real correction, implement it with known-answer tests, create a separately
versioned deterministic rebuild, and evaluate the same frozen acceptance scope.
Do not remove flags, choose a more favourable month, relax thresholds, or label
QA-only observations as training data. Current missing approval bindings and
later research gates remain separate requirements even after data-quality fixes.
