# October reference definition — proposed, not approved

Prepared September 5, 2026. This defines a concrete review choice, not a passing
quality result or permission to train. The existing SPEC, raw data, historical
reports and original draft remain unchanged. Isaac's request to implement repairs
is not recorded as Delsa's signature or as approval of an unseen final package.

## Exact proposed scope

FBS venue, FBS-Demo source, unsuffixed EURUSD, one-minute bars, October 2024 by
canonical New York session **close** date. Calendar identity is
`FBS-Demo/EURUSD`. GBPUSD remains an optional separate evaluation, not an added
Gate-1 condition. The already selected October month is not replaced after seeing
its diagnostics.

The project QA interval is **13:00–16:30 Europe/London** on ordinary weekdays.
SPEC 2.1 identifies approximately this overlap as highly liquid; 2.2 applies the
same structure to EURUSD. Adopting exact endpoints is a policy decision, not a
fact learned from the tick sample. Use date-aware timezone conversion: before
the October UK transition the interval is 12:00–15:30 UTC, afterwards
13:00–16:30 UTC. Do not shift any source epoch or change any bar boundary.

Proposed holiday rule: the QA window is not expected to be fully liquid on a
national German or Federal Reserve holiday, or an England/Wales bank holiday.
Within October this excludes October 3 and October 14, as well as weekends.
This yields 21 proposed windows and 4,410 expected minute bins, calculated from
definitions alone. No price observations or flagged-rate calculations are inputs
to this proposal. Regional German holidays do not automatically close the entire
London/New York QA window; that is a disclosed policy choice for human review.

`FULL` means the complete proposed 3.5-hour QA window, not 24-hour liquidity.
`CLOSED` means no expected minutes **under this project QA rule**, not that FBS or
the FX market was closed. Actual quotes and defects outside the window must still
be reported separately. This calendar must not be used as a broker tradability,
settlement or daily-bar-count calendar. If reviewers require different semantics,
revise the definition before the acceptance run; do not relabel broker facts.

## Sources and what they do not establish

- [GOV.UK bank holidays](https://www.gov.uk/bank-holidays) lists the 2024
  England/Wales dates, none in October.
- [North Carolina Treasury's 2024 Federal Reserve bank-holiday schedule](https://www.nctreasurer.gov/documents/files/fod/2024-bank-holidays/open)
  identifies October 14. Its separate state holidays are not used here.
- [German Unification Treaty, Article 2](https://www.gesetze-im-internet.de/einigvtr/art_2.html)
  establishes October 3 as German Unity Day and a statutory holiday.
- [MetaQuotes tick API contract](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py)
  specifies UTC timestamps. Keep that documented baseline; this is not independent
  proof of every historical broker record's correctness.
- [FBS's October 25, 2024 notice](https://fbs.com/news/trading-schedule-changes-due-to-the-winter-time-shift)
  supplies DST context but does not list EURUSD or GBPUSD among affected symbols.
  It does not establish their complete historical sessions.

Holiday facts do not guarantee liquidity, and trading hours do not guarantee
continuous quoting. The narrower expectation is explicitly project-owned. Source
snapshots retain current retrieval times and hashes; their stated historical dates
are not backdated `available_at` values. Calendar availability starts at actual
preparation in 2026 and the draft expires after 30 days. It cannot be delivered as
information known to a strategy in 2024.

## Proposed counting contract

| Category | Treatment inside expected minute bins |
|---|---|
| Clock/time regression, invalid bid/ask, crossed/locked, spread outlier, confirmed gap | Count as flagged |
| Out-of-session evidence conflicting with an expected bin | Count; disclose the contradiction |
| Retrospective reverting price-spike annotation | Count for ex-post QA only; never alter causal features |
| Missing expected bar | Count; keep its minute in the denominator |
| Historical receipt imputation and backfill provenance | Exclude from this numerator only; retain all provenance and freshness limitations |
| Unknown-calendar gap or insufficient detector warmup | Indeterminate if present in expected bins |

Each minute enters the numerator once, regardless of overlapping flags or excluded
constituent quotes. The denominator is the fixed set of fully contained expected
one-minute intervals. Acceptance is unchanged: `numerator * 1000 < denominator`;
equality fails. No calendar, missing dates, missing files or unresolved definitions
may default to a clean result. All 14 flag dispositions are materialized explicitly.

## Reproducible preparation and decision order

`scripts/prepare_reference_definition.py` emits a new immutable directory containing
the draft calendar, draft policy, date-by-date CSV, source snapshots when requested,
and a hash manifest. It reads no tick/bar data. The original policy and SPEC are
independently pinned before preparation. Outputs never overwrite older evidence.

1. Delsa reviews the exact proposed files, sources, holiday rule, exclusions and
   timestamp limitations; Isaac then records the definition decision. These are
   pre-evaluation decisions, not final Gate-1 approval.
2. Build the complete 23 target sessions, using September 29-open prehistory and
   October 31-open lookahead only as context. Context never enters October's rate;
   future confirmation remains retrospective QA only. Include empty target sessions
   as evidence rather than silently removing them.
3. Evaluate the complete clean inventory against the reviewed definitions. Preserve
   the result whether it passes, fails or remains indeterminate.
4. Bind final code/CI, SPEC, dataset, calendar, policy, reports and five human bar
   checks into one unsigned package. Delsa signs that package; Isaac signs the same
   package and Delsa's record hash. Existing statements are preserved, not transplanted.

Earlier data and diagnostics have already been inspected. Freezing this proposal
before the final acceptance computation is not statistically pristine preregistration.
It must not be tuned by trying windows or exclusions and selecting whichever passes.
Gate 1 only permits the next backtester phase; Gate 2, strategy-specific deep/stress
history, reviewed purpose-scoped admission and later gates remain separate.
