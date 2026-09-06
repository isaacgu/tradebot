# Data pre-use preparation — September 5, 2026

Status: **prepared for further QA, not approved for strategy training or trading**.
The completed collection is a bounded source-history sample, not continuous history
for every date between 2016 and 2026. No raw quote, timestamp, frozen criterion,
human decision, or acquisition result was rewritten to make it pass.

## Permission and account

Isaac reports FBS permits account-holder data use for his personal project without
further verification. This attributed statement is retained in
[the permission record](fbs_data_permission.md). No duplicate permission inquiry is
needed. The current work remains private and demo-only through training/refinement;
live capital is a planning assumption, not live-trading authorization.

The [account profile](../../configs/accounts/demo_usd_1000.json) records USD 1,000
initial demo capital and USD 1,000 planned live capital, with execution disabled.
The separate P0 smoke-demo configuration, leverage and risk thresholds are unchanged.
The profile does not change the broker account or substitute for future risk sizing.

The new read-only [baseline verifier](../../scripts/verify_demo_baseline.py) checks
the local broker observer for a fresh, unchanged, demo USD FBS account with the
requested balance/equity, zero used margin and no open positions or pending orders.
It does not import MT5, submit orders, change account state or authorize execution.
Its final-source run passed **14/14 checks**:
`build/preuse/demo-baseline-20260905-v2.json` SHA-256
`e15f10ad68600257dd2473bb610d7f78c50696ea54721e53a611a2a8c3be254b`.
This is a dated starting-account observation, not a continuous guarantee. Later
legitimate trading would change balances and therefore no longer match this baseline.

To repeat from Windows, choose a new output filename:

```powershell
& .\build\monitoring\venv\Scripts\python.exe scripts\verify_demo_baseline.py --profile configs\accounts\demo_usd_1000.json --output build\preuse\demo-baseline-new.json
```

## Source-data disposition

The independently pinned acquisition candidate remains
`docs/reports/fbs-tick-continuity-v1-candidate.json` SHA-256
`3bebefcb5a5d672f86808e9dc2c88fd023bd6a35860049ef12ea1e4eb5cb60cd`.
The additive admission audit checks all candidate/checkpoint identities and freshly
hashes the compressed raw files. It reconciles the original tick metrics rather than
claiming to have repeated the earlier full semantic scan.

Final manifest: `docs/reports/fbs-data-admission-v2.json` SHA-256
`e5beb620cfb17e2df7e1ee735debcf6432a328cf2d13e20e5dd38cbc8d3871a1`.
Its checksum sidecar matches. The complete input set is rechecked after the scan;
regression tests reject earlier-file changes and output-parent alias swaps during
verification/publication. The earlier v1 remains retained as historical evidence.
Final admission CLI: `scripts/prepare_data_admission.py` SHA-256
`f7859f25c12f8b8d757d4444f3076279a2c33c6ee1843c55b1fe7fa0e09696fc`.

| Disposition | Partitions | Primary ticks | Meaning |
|---|---:|---:|---|
| QUARANTINED | 10 | 1,497,274 | Six quote-defect partitions and four empty sessions |
| QA_ONLY | 180 | 41,299,324 | Available for data investigation; no acceptance or strategy eligibility |
| Total preserved | 190 | 42,796,598 | All original raw observations remain retained |

The affected quote partitions are EURUSD Brexit sessions dated June 26–30, 2016,
and GBPUSD June 28, 2016. They contain 379,467 primary crossed quotes and 69 locked
quotes. Empty partitions are June 19 and 20, 2016 for both symbols; their absence
has not been relabelled a legitimate closure or a proven outage. All 9,332 adjacent
repeated quotes remain retained; repeated quote values are not duplicate identities.

Quarantine is a manifest disposition, not a raw-file move/delete and not an operating-
system access restriction. The current engineering `SnapshotBarFeed` does not enforce
this additive manifest. No strategy-eligible snapshot is issued, and all admission
strategy/execution flags remain false. A future consumer must require a separately
reviewed immutable selection; this report is not a substitute for that integration.

## Dashboard QA

The flow checked was acquisition → Data & Quality → Broker & Trades → System
Overview in the Codex in-app browser at desktop size, using the live local Grafana
service. The frontend-testing checklist required visible states and link interactions
in addition to configuration checks; no browser or application dependencies were added.

| Check | Result |
|---|---|
| Correct routes and meaningful content | Passed on all four pages |
| Visible warning semantics | Four empty sessions orange; 379,467 crossed red; 69 locked orange; quality indeterminate |
| Reproducibility distinct from quality | Measured rebuild displayed alongside tick-quality issues and 30 missing calendar dates |
| Account view | Fresh demo USD, balance/equity 1,000, zero positions/orders and margin |
| Execution/phase overview | Execution disabled; P1 remains in development |
| Link interactions | Acquisition → quality → broker → overview reached their intended pages |
| Runtime errors | No framework error overlay; captured warning/error console log list empty |
| Visual evidence | Actual screenshots displayed in this task; no fabricated data or trading state |

Current-value panels now use instant queries and a current-value reduction, so absent
series cannot retain an older good value from the selected time range. The actual
corpus-history chart remains a range query. Source hashes and loaded provisioning
were compared; Grafana loaded the three edited dashboards without a service restart.
The independent query/frame audit matched **54/54** direct Prometheus/Grafana queries,
with no query or backend-frame errors:
`build/gate1/observability/dashboard-after-corpus-20260905T090919Z.json` SHA-256
`8ec86dca14dd6e95d64e679df16aa68ac704a9ee70cdad2b3c78e037e3c54fc1`.
Mobile layouts, other browsers and future nonempty trading states were not retested
in this pass. The broker dashboard remains read-only.

## Real reference-month readiness evaluation

The new [offline evaluator](../../scripts/evaluate_reference_month.py) and
`tradebot.data.reference_acceptance` were exercised against the preserved producer
corpus, independently pinned to its recorded SHA-256. It verified the full selected
inventory: seven bar files (41,701 rows) and seven tick files (6,646,477 rows).
The October 2024 EURUSD close-month view contains **16,608 bars**, all with tick-
minute evidence. This remains the October portion of a multi-year sample, not a
full-month rebuild or proof of complete market coverage.

Final actual-time artifact:
`build/gate1/reference-acceptance-readiness-20260905T094337Z/report.json` SHA-256
`62d75f4c43180e1dbe9dc65ebd77055c2466577eaa25dfe40f8b76fac0b139f4`.
Its sidecar matches. The evaluation known-at is `2026-09-05T09:43:37.557595Z`;
the CLI recorded its actual launch/generation timestamp as
`2026-09-05T09:44:09.736474Z`. The earlier `100000Z` directory is preserved but
superseded: it used a caller-supplied future timestamp and is not the primary record.

The result is **INDETERMINATE**, with `gate_approved=false` and
`training_enabled=false`. All 31 calendar dates are unresolved, the expected-liquid
denominator is absent, and both the flagged fraction and strict comparison are
null. The policy is draft with unresolved treatments, and exact policy/calendar
decision bindings are missing. The existing general approval statements remain
preserved separately; the evaluator does not authenticate people or issue approval.

Observed affected-bar counts are 1,866 calendar-unknown gaps, eight warmup, three
spread outliers, 16,608 imputed-receipt provenance flags and eight bars with
retrospective price annotations. These are overlapping diagnostic categories, not
an accepted numerator. Because the declared calendar set is empty in this run,
`missing_expected_minutes=0` does **not** prove no missing bars, and
`unexpected_actual_minutes=16608` does **not** mean 16,608 defective bars. Neither
set difference can classify market completeness without the calendar.

The evaluator's tests cover strict threshold equality, union-once counting, missing
calendar/policy evidence, canonical close dates, legitimate bars outside narrower
liquid intervals, excluded causal-tick flags, exact Parquet schemas and incomplete
tick evidence. A producer report/sidecar cannot authorize itself: an independently
supplied report hash and the complete matching file inventory are required. The
command creates new output evidence and never promotes or repairs source rows.

## Final local verification

The final isolated run passed **962 warnings-as-errors tests**, 87.20% overall
coverage, 96% core and 86% non-core coverage, Ruff formatting/lint, strict mypy on
101 files, Bandit and pip-audit (no known vulnerabilities reported). Two fresh
Gate-0 demos and their canonical metric digests are byte-identical. The source and
configuration inventory was unchanged throughout the checks; older demo evidence
was not overwritten. This is local verification, not a committed GitHub CI run.

`build/preuse/local-checks-20260905T093126Z/result.json` SHA-256
`a054b9cbda82e93a278a98d8399977a5bd3145e1f57921ed8df90dbcd56305fb`
records every command outcome, source/configuration hashes and preserved log hashes.
The helper is retained at `build/preuse/run_local_checks.py`; it writes a new
timestamped directory on every run. No change was committed or pushed by this work.

Additional replay, canonical session/time and reference-month regression runs passed
under **UTC, Africa/Johannesburg and America/New_York** host timezones, each forcing
the locked tzdata dependency. These are repeat runs of the same tests, not additional
unique tests to add to 962. Evidence:
`build/preuse/timezone-checks-20260905T094603Z/result.json` SHA-256
`d8c03d62ccc1d3f374780d6cbd03df6f9c195aebbc30ac3d567d2096b9c7baf6`.

## What is still needed before using data for strategy decisions

1. Resolve dated October 2024 instrument-specific liquidity intervals and historical
   timestamp/session discrepancies. The permission reply does not answer these
   technical questions. A generic trading timetable is not automatically a liquid-
   hours denominator.
2. Review the explicit [draft counting policy](../../configs/calendars/reference_month_policy_draft.json).
   It separates actual causal defects, calendar-unknown gaps, warmup, historical
   receipt provenance and retrospective annotations. Unresolved treatments remain
   unresolved; none is selected because it yields a passing rate.
3. Build/evaluate the complete reference month with those reviewed inputs. The
   existing random 30-day multi-year sample is not a full October clean-bar corpus.
   Missing expected minutes and overlapping flag classes must be accounted for once,
   under the unchanged strict **less than 0.1%** criterion.
4. Bind successful committed-candidate CI and the five actual human bar checks to
   the final evidence package. Isaac's approval and his report of Delsa's approval
   are already preserved in [the receipt](gate1_approval_receipt_20260905.md); do not
   ask them to repeat a generic approval, or treat that statement as completion of
   checks that remain undocumented.

The [evidence checker](../../scripts/check_evidence.py) still correctly returns
nonzero for categories 1, 2, 4 and 5. This is a real acceptance hold, not a software
test failure to suppress. Offline data QA and synthetic engine development can
continue; this preparation does not enable strategy training, paper orders or live
trading, and it does not waive later phase gates.
