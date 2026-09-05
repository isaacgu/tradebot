# Gate 1 evidence — incomplete candidate

Status: **NOT APPROVED**. Phase 1 implementation and evidence collection are in progress.
This pack records available artifacts and unresolved acceptance criteria; it does not certify a
gate, authorize strategy evaluation, or enable trading. Status `FAILED` below includes required
evidence that is not yet supplied. Nothing is deferred past its frozen due gate.

For the remaining work and exact places where humans record decisions, start with the
[Gate-1 approval guide](gate1_approval_guide.md). The
[independent-review form](gate1_independent_review.md) and
[Principal approval form](gate1_principal_approval.md) have approval statements recorded,
but **final evidence bindings and required checks remain incomplete**. See the
[attributed receipt](gate1_approval_receipt_20260905.md). Isaac's decision and his report
of Delsa's approval are preserved; they do not establish that pending checks passed.

Latest permission update: Isaac reports FBS permits account-holder data use for his
personal project without further verification. The [permission record](fbs_data_permission.md)
applies demo-only training/refinement. No duplicate permission inquiry is planned.
Earlier contact-status and unsigned-template descriptions below are dated history,
not the current state; historical session evidence is still technically unresolved.

Frozen SPEC judged against: `docs/SPEC.md` v1.0, SHA-256
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`

## Evidence categories

| # | Category | Status | Content |
|---|---|---|---|
| 1 | CI run on a committed git SHA | FAILED | The current P1 candidate has no attached successful committed-SHA CI run. Commit the reviewed candidate and attach its immutable CI URL and full SHA after required jobs pass. Gate-0 CI does not certify these changes. |
| 2 | Report / manifest artifact hashes | FAILED | The replacement 30-day reproducibility report is PASSED: `build/gate1/30day-stable-b102ecdd/report.json` SHA-256 `3c7226e91d4c9a0a632ee85ed8ad273d6493256c0e0427c16e6aca5f047682bb`, independently checked against actual files. Calendar integration: `docs/reports/gate1_calendar.json` SHA-256 `f6321f15d29e8bf2a9d0aee1f8d5ccf8d92d526690f8137a33b1e937da3c1604`; broker reference bundle: `build/gate1/reference/bundle.sha256.json` SHA-256 `4805f4be4b1e13516e910697260736c8c80234130ce365b4d20089ab1e16487e`. The first run remains ineligible after code drift. A reference-month quality acceptance report is still owed; sample quality is FAILED and approved liquidity coverage is absent. |
| 3 | Observability evidence | PROVIDED | Actual P1 capture at 2026-09-04 20:15 UTC: `build/gate1/observability/exposition-20260904T201542Z.prom` SHA-256 `058ac5c96036836a7bd92a07705d9e5b381b4c6f269e09a60e0492ae0a8b16b7`; `build/gate1/observability/verification-20260904T201542Z.json` SHA-256 `a7edf47a3cc47f47d5d37e213d71ab2dd9a3e905f79ca1c55717d2f4362eb4e5`; actual rendered `build/gate1/observability/data-quality-before-report.png` SHA-256 `50ba2948f5b51422e9dbbec4e10c1b0c1ce4a2d4344013ad7bf2a706b0dc9be6`. This proves observability/format delivery, not liquid-hours quality acceptance or Gate approval. Later broker/current-navigation verification is recorded separately. |
| 4 | Independent reviewer sign-off | FAILED | Isaac reports Delsa Mashiki's approval; preserved in [the receipt](gate1_approval_receipt_20260905.md). Delsa's five hand checks, independence declaration and final candidate/evidence-bound [review record](gate1_independent_review.md) remain incomplete. This attributed statement and agent QA do not complete the required review. |
| 5 | Principal sign-off | FAILED | Isaac Gumbi's APPROVE entries and direct confirmation are recorded. Final candidate/evidence/CI bindings and the signed independent-review hash in [the Principal record](gate1_principal_approval.md) remain incomplete; the statement does not establish missing technical acceptance. |

## Inherited obligations from Gate 0

The first four cells are copied verbatim from Gate 0. A provided artifact discharges only the
identified obligation, not the whole gate.

| id | Category | Deferred at | Due at | Status | Evidence |
|---|---|---|---|---|---|
| G0-1 | Grafana screenshot format (§9.4) | Gate 0 | Gate 1 (data-quality dashboard, §4.6); full at Gate 4 | PROVIDED | Actual rendered P1 dashboard: `build/gate1/observability/data-quality-before-report.png` SHA-256 `50ba2948f5b51422e9dbbec4e10c1b0c1ce4a2d4344013ad7bf2a706b0dc9be6`. Unknown/unavailable Gate-1 evidence is visibly represented; this discharges the screenshot format, not quantitative quality acceptance. |
| G0-2 | Gate evidence checker `scripts/check_evidence.py` (§10.6, ADR-0005) | Gate 0 | Gate 1 (§13 P1 row) | PROVIDED | `scripts/check_evidence.py` SHA-256 `088d202422dd6ef1f2bf3af2e18fcd9d51b08831022546f9ad35a24d8629722d`. Local tests exercise statuses, artifact tampering, inherited obligations and the human-sign-off boundary. |

## Acceptance criteria and current evidence

| SPEC §4.6 criterion | Current evidence | What is still owed |
|---|---|---|
| Thirty sampled days of one-minute bars rebuilt twice from immutable raw | Mechanically PASSED: replacement has 30 distinct EURUSD dates, 6,646,477 ticks and 41,701 one-minute bars; 31 raw files and both 14-file clean manifests independently hash-verified, byte-identical outputs, all 27 current code hashes and quality policy match | Final committed-candidate CI/review; this reproducibility result does not certify quality, timestamp interpretation or Gate 1 |
| Five hand-verified venue-matched bars | Five prospectively selected recent 2026 FBS-Demo bars match independent Bid OHLC, and actual production Mid BarBuilder output matches the independent tick-Mid worksheet exactly for all five | Required human hand-verification remains pending; historical 2024 unavailability is disclosed, not added as a separate gate obligation |
| DST/time rules | Existing DST/session tests plus P1 code tests are available | Final current-candidate CI evidence, with relevant time/replay tests included |
| Live quality dashboard and less than 0.1% liquid-hours flagged bars for a reference month | Actual rendered dashboard/exposition provided; sample tick quality FAILED and reference-month liquid-hours criterion INDETERMINATE | Authoritative dated liquidity expectations, explicit gate-counted flag definition, measured reference-month numerator/denominator and full flag-category CI evidence |
| Point-in-time external calendar query | Narrow query-at-T criterion mechanically PASSED: real Fed import, reopened store and offline replay pass before/at/after retrieval availability; synthetic revisions are separately labelled | Final CI evidence; this does not supply historical field vintages or approved expected-liquidity coverage |

The actual calendar example imports the declared release time and three target-rate fields from
the [Federal Reserve's November 7, 2024 statement](https://www.federalreserve.gov/newsevents/pressreleases/monetary20241107a.htm).
The source was retrieved at `2026-09-04T19:52:46.280439+00:00`; exact HTML SHA-256 is
`098ac3402d7a4a747c7bf7e9346ebabb9f8ed3e933398929bdcf58deaf0d0ee9`. Both source bytes and local
receipt provenance are retained in the calendar report. The archive page's declared 2024 release
time does not establish original field vintages, so all imported fields remain unavailable before
retrieval and carry `AS_OF_UNVERIFIED`. Replaying the preserved snapshot produced byte-identical
official and synthetic SQLite databases; the two data classes remain separate.

The dated FBS October 2024 holiday notice and current weekly-hours/server-time pages are retained
as source context in that report. They do not establish approved, instrument-specific historical
FULL/PARTIAL/CLOSED dates or liquid intervals. **Zero liquidity-calendar dates are approved.**
No calendar rule is consulted by the bar-boundary function.

The first 30-day run's code freeze failed. A read-only 21:53 UTC audit compared all 27
declared dependency hashes against its selection: 26 matched; `boundary_probe.py` differed
(`a0160ed5…f712` expected, `b102ecdd…708e` observed; exact hashes and source bytes are preserved
in the drift artifact below). The quality policy hash still matched. The owner stopped only
that offline job through its owned execution session; no source was reverted and no old
artifact was deleted. Authorship and executed-function impact are not inferred. This run
cannot pass unchanged-code acceptance, even if its partial bytes appear deterministic.
Recovery in new `build/gate1/30day-stable-b102ecdd` completed at
`2026-09-04T23:30:06.900797+00:00` (September 5 01:30 SAST), after 5,672.439 seconds.
It has a different 30-date sample (6,646,477 primary ticks) because the candidate pool
grew before resampling with the same plan/seed. The two completed outputs each contain
14 clean files and 41,701 one-minute bars from 31 raw files. The producer reports PASSED
reproducibility, unchanged raw/implementation and byte-identical clean outputs.
An independent read-only audit subsequently passed all 25 checks: report/sidecar/selection,
all 59 actual raw/clean file hashes and exact file inventories, footer identities, dataset ID,
all 27 current code hashes, quality policy, tick/bar counts, sequence uniqueness and bar
time/OHLC/availability invariants. The audit imports no production module and preserves its
helper, runtime and per-file results. This is not a third rebuild or committed CI.
The older 530-test baseline separately captured the changed source hash before/after its checks.

### Quality findings in the completed sample

The quality summary is **FAILED**, specifically because of 34 retrospective `PRICE_OUTLIER`
annotations. Severe causal-flag counts and excluded rows are zero; all 6,646,477 input rows
remain present and eligible for bars. Adjacent identical raw payloads (1,465) are deliberately
retained, not silently deduplicated: repeated quotes are not duplicate sequence identities.

| Diagnostic | Tick rows/events | Bars carrying the causal flag | Interpretation |
|---|---|---|---|
| GAP_CALENDAR_UNKNOWN | 7,589 | 5,338 | Right-edge gap events without approved liquidity expectations; not proven missing market data |
| QUALITY_WARMUP | 401 | 26 | Rolling-quality history is not yet sufficient |
| SPREAD_OUTLIER | 37 | 6 | Diagnostic above the configured rolling-median threshold; rows remain eligible |
| PRICE_OUTLIER | 34 retrospective annotations | 0 | Later reversion identifies an earlier jump; QA-only, never added to causal bar inputs |
| TS_RECV_IMPUTED | 6,646,477 | 41,701 | Historical provenance contract, not a measured original receipt timestamp |

Flags overlap. The independent audit counts 5,347 bars with any non-provenance causal flag;
this is a descriptive union, **not** an approved gate numerator or liquid-hours rate. All 30
sample dates lack approved calendars, and the sample spans multiple years rather than a full
reference month. Cross-source status is `NOT_EVALUABLE` for this single source. An explicit
reviewed gate-counted flag definition and reference-month liquid-hours denominator are still
required; no provenance, warmup, unknown or retrospective class is silently included/excluded
to make the less-than-0.1% criterion pass. Retrospective annotations are future-informed QA,
not causal exclusions or permission to alter raw observations.

Additional archived primary-source research produced 62 October 2024 calendar review rows and
46 checkpoint observations. The eight observed Sunday opens conflict with the generic advertised
availability hypothesis. The candidate remains `PROVISIONAL_NOT_LOADABLE`, with zero approved
entries, and is not used to determine the liquid-hours denominator. Archive capture times are
preserved as source evidence, not relabelled as local historical availability: this candidate's
`available_at` is `2026-09-04T20:46:16.340049Z`.

The discrepancy may involve historical timestamp/session alignment or source coverage;
it is not diagnosed as missing market data. Relative to the archived advertised opens,
observed first ticks are roughly 3h05 later before October 27 and 1h53 later on October 27.
Broker confirmation of the October 2024 `time`/`time_msc` UTC basis, instrument sessions
and DST treatment is still needed. Raw bytes remain preserved. A deterministic rebuild
alone cannot certify that the historical timestamps have been interpreted correctly.

### September 5 provisional diagnostic addendum — not acceptance

The Principal authorized contacting FBS and continuing offline engineering in parallel,
not waiting for a reply and not waiving Gate 1. The prepared technical inquiry remains
**NOT SUBMITTED** pending the Principal's chosen name/email. The official contact form
contains only an unsubmitted draft; no contact details, CAPTCHA completion, submission,
reply or case number exists. No account identifiers or raw files have been sent.

The new diagnostic preserves the candidate's acquisition open-date view and separately
computes the canonical New York session-close-date month. Two existing September 30
checkpoint/raw pairs supply October 1 closes; November 1 closes are excluded. The 48 frozen
input windows contain 11,128,900 ticks, including boundary context outside the primary month.
The producer verified their compressed bytes, semantic streams and counts before/after.
Its archived-page claims reuse frozen candidate metadata; this helper does not reverify
the archived page bytes or elevate their generic availability claims to symbol liquidity.

| October 2024 canonical close-date view | EURUSD | GBPUSD |
|---|---|---|
| Tick rows in the primary month | 5,288,101 | 5,335,980 |
| Provisionally advertised minutes | 33,060 | 33,060 |
| Minutes with observed ticks, UTC baseline | 32,056 | 32,143 |
| Evaluable advertised minutes without observed ticks | 1,004 | 917 |
| Unknown advertised minutes, UTC baseline | 0 | 0 |

This partitions a provisional advertised-session hypothesis, **not approved expected
liquidity**. Zero unknown minutes applies only inside that stated baseline hypothesis;
it does not establish quote completeness outside it or prove that no exceptional session
existed. Minutes without observations are not adjudicated missing market data. The -2h/-3h
counterfactuals preserve 480/720 unknown minutes per symbol, respectively; no best-fit shift
is chosen and canonical timestamps remain unchanged. Source checkpoint diagnostics are
explicitly not P1 clean-quality flags; no sample-to-reference-month flag extrapolation is made.

An independent stdlib stream review rehashed all 48 raw/checkpoint pairs before and after,
recomputed 11,128,900 semantic rows and active-minute sets, and matched all 12 scenario
aggregates/ratios across close-date and original open-date views. A separate addendum audit
passed 40 checks, including all 96 actual input-file hashes, primary-month counts, six
close-view session/aggregate partitions and retained outlier arithmetic. These are distinct
checks with explicit scope, not human review or acceptance evidence.

The recorded-flag drilldown covers all 34 retrospective PRICE_OUTLIER annotations with five
stored neighbors on each side. Absolute Mid changes from the preceding stored tick range
from 1.15 to 6.65 pips; none of these annotated ticks also carries SPREAD_OUTLIER. UTC-month
counts are March 2020: 4; October 2022: 1; October 2024: 14; November 2024: 13; August 2026: 2.
The helper checked all seven clean tick-file hashes before/after. Stored neighbors need not
be consecutive market ticks across sample boundaries; these annotations are **not independently
proven erroneous quotes**. No quotes were removed and no quality threshold or gate budget changed.

After the new helper/tests, a separate final local run passed 735 warnings-as-errors tests
in 87.10 seconds, 88.24% overall coverage, Ruff formatting/lint, strict mypy on 84 files,
Bandit and core/non-core coverage tiers of 96%/87%. All 27 completed-corpus dependencies
and its report hash still matched. This is newer than the retained 704-test snapshot,
not committed CI. Approved dated liquidity intervals, a reviewed gate-counted flag policy,
the acceptance-quality report, reference-bar hand verification and human sign-offs remain owed.

The broker reference helper preserves all returned rows. Its five out-of-range 2024 responses
are excluded from comparison. A separately frozen recent sample (seed 20260905) yields five
genuine in-range FBS-Demo M1 bars matching source Bid OHLC; FBS declares chart mode Bid. The
worksheet separately computes actual tick Mid OHLC, not averages of Bid/Ask extrema. All human
review fields remain pending. The separate production-path check now proves exact Decimal Mid
OHLC equality for all five candidates using the real BarBuilder and frozen tick worksheets;
its input, output and production-source hashes were checked locally with no mismatches.
SPEC does not require these five bars to be from October 2024. No full-corpus or strategy-performance claim follows from
these five automated checks. Forty-five supporting manifest entries were locally hash-verified
with zero mismatches (including the separate calendar manifest; entries are not necessarily
distinct files).

## Available supporting artifacts

These are inspectable, source-backed materials, not a completed acceptance pack. Ignored `build/`
artifacts must be retained with the candidate or restored at these paths for later verification.

| Artifact | SHA-256 | Meaning and limit |
|---|---|---|
| `build/gate1/reference/fbs-demo-october-2024.calendar-candidate.json` | `3dd0b12b95b2126bc8b88363403b08c99e0ba9fc8767b6b5b5e11d9eee377631` | Provisional dated research; not loadable as approved liquidity coverage |
| `build/gate1/reference/fbs-demo-october-2024.calendar-manifest.json` | `d3812145bf2a2a4b0e0a613efaf0fe40c921f5721d8c2a6f619e374350915670` | Hashes candidate, review CSV, helper and archived-source index |
| `build/gate1/reference/recent-2026-seed-20260905/report.json` | `51a7d6757be392fcdac9d1bae814057e2aa73ab01c5cc07cacc163f1c4eba11b` | Five exact automated Bid matches; human_verified=false |
| `build/gate1/reference/autumn-2024-seed-20260904/report.json` | `d83cc1edd2c95a40a228cc921a4fee676300e073d0ea54ce56294a6a28bb39cc` | All five requested historical references unavailable/ineligible |
| `build/gate1/reference/comparison-worksheet.csv` | `0d7af181e1a21228a74d56cb4ecd130654de4331d32e2374215e41da61d541ae` | Unreviewed human worksheet; do not overwrite this hashed original |
| `build/gate1/reference/production-bar-check-v1/report.json` | `fbd7f9a805522e4da2b4d7f8fbc96106668fe5e6e35145a875abfb7f86d51f90` | Actual production Mid OHLC matches all five independent answers; human_verified=false |
| `build/gate1/reference/production-bar-check-v1/comparison.csv` | `b697201b75f388cbab95559cd606d8bc3dec757d2ba4792f0b7b8c95f80ceb80` | Production/independent Decimal comparison worksheet |
| `build/gate1/reference/production-bar-check-v1/sha256.json` | `0541c8862ce176b01ec3c3c6e610e0123555fa4edb17622734c5240e54943f78` | Hashes the production-path helper, report and comparison |
| `build/gate1/30day/selection.json` | `e98315febcd4fe6c064302069a790b5aa0e3bd0b0f6dd3a885d3ba65f6b0962c` | Frozen 30-date input selection; not a completed rebuild report |
| `scripts/build_gate1_corpus.py` | `4c605bbcef8416f9f385ccd44f69e955f13b3edc986768d44f2b72cf3a976918` | Frozen CLI; scoped Ruff/mypy and 20 warnings-as-errors tests passed locally |
| `deploy/grafana/dashboards/system.json` | `dc2cc8f8330f974edb448c7ff9416be701fecb8fdbf58f32e941e28018f9d906` | September 5 pre-use revision: current snapshots and USD 1,000 planning baseline; not rendered evidence |
| `deploy/grafana/dashboards/acquisition.json` | `d3d39a1e005d1cc5c053d2752a6ff733ba2f025dfd024c9683273ab4b95c69a7` | September 5 pre-use revision: exact primary crossed/locked counts and warning states |
| `deploy/grafana/dashboards/data-quality.json` | `c57bf80d4b7969d7d69aa5f8020583d3cc2bdf727664959f8049e3d706f2236a` | September 5 pre-use revision: missing-calendar warning; reproducibility, quality and approval are separate |
| `deploy/grafana/dashboards/broker.json` | `88bb64bb417c31e08f9171a4c5ce32729a69c4ddbd113bc8176f611dc9561dfc` | Post-baseline read-only account view with tested nonempty table joins; no bot performance certification |
| `deploy/grafana/dashboards/research.json` | `3eec512c8250c1d2fdbdbc808491c22e64de475f31853368d8cf908ec3786663` | Final five-panel instant-only engineering-replay summary; no live calls or performance |
| `src/tradebot/monitoring/research_status.py` | `916314a7e6a960ed481038533acac9c4d9ba84af80f698cfe99d7a3822fa98c3` | Bounded read-only report consumer; does not import/run replay or read traces |
| `src/tradebot/monitoring/acquisition_exporter.py` | `e560ffcacdaa1d3e30050cf8236e5b886c6b3bd262d75ba723b053723dc6b714` | Post-baseline additive research-status integration; independent from the frozen acquisition worker |
| `build/gate1/corpus-drift-20260904T215338Z.json` | `0c4a817d552648df5d6955dfe07e992d183a412d3223cd88555f3aedd0147c56` | Preserved first-run unchanged-code failure: 1/27 mismatched; quality policy unchanged |
| `build/gate1/corpus-drift-20260904T215338Z-boundary_probe.py` | `b102ecdd76aa4d3342b9be04be50ad5012e22866c9c93715fadea15f8802708e` | Exact changed source snapshot at drift detection, not a repair or attribution claim |
| `build/gate1/30day-stable-b102ecdd/selection.json` | `ff7465412af8834cc4b1634671e49e036ad8751b34c4483a6fae8cde12fddbf1` | Replacement's different frozen 30-date, 6,646,477-tick sample |
| `build/gate1/recovery-start-20260904T215901Z.json` | `97717cfce5e800981f84313c9e1561f5dd2b4dcaaf33082fca34858276e7ff24` | Read-only replacement metadata check: 27/27 code hashes and quality match; no final report yet |
| `build/gate1/30day-stable-b102ecdd/report.json` | `3c7226e91d4c9a0a632ee85ed8ad273d6493256c0e0427c16e6aca5f047682bb` | Completed reproducibility PASSED; quality FAILED, liquid-hours acceptance INDETERMINATE and Gate 1 false |
| `build/gate1/30day-stable-b102ecdd/report.sha256.json` | `eb5f6b9baea759f347436fca258a02ecd425082504ac682c473e6ab81594e98a` | Producer report checksum sidecar |
| `build/gate1/completed-corpus-verification-20260905T054036Z.json` | `6728e346aa3884ff3ae935f5a15eb36dbd0d177caad895ebdc4aa510cf4aeea2` | Independent actual-file/manifests/counts/quality-grain audit; all 25 checks pass, limitations explicit |
| `build/gate1/verify_completed_corpus.py` | `7b396fe13b6af787254f0a184aaff5b328f9e6a69eff7b6f3ea1af0b00cc7739` | Inspectable read-only audit helper, independent of production modules; no third rebuild |
| `scripts/diagnose_reference_month.py` | `b8d11c8ca8680d38a91f776804e9d396f6a197a5512b6fb5c58e70f6fa8b80b9` | Provisional offline hypothesis diagnostic; never creates an approved calendar or adjusts canonical timestamps |
| `tests/unit/scripts/test_reference_month_safety.py` | `9e2ed3babe015a1f33b93597ea0c3a3b1566f4c70bfaddfc5a3ff0387e569f33` | 31 scoped safety tests, also included in the newer 735-test run |
| `build/gate1/reference-diagnostics-v1/report.json` | `f30dd82b88f70d5b72296c36de6c326f12cdea2f4968446d93b530ea1c1dc07b` | 48-input provisional October close/open-date comparison; acceptance INDETERMINATE and gate_approved=false |
| `build/gate1/reference-diagnostics-v1/report.sha256.json` | `6db69943bba5668b40ae44b2df85aec78fdfa85b078a9014dcbf902e9cc169c9` | Provisional report byte count and checksum |
| `build/gate1/reference-diagnostics-v1/scoped-verification.json` | `9c0177890ba6ee3505666f96437fc70c6e5a58a75071f32704cba7dd2da71b5b` | Producer's 31-test/static/run record; no CI or approval |
| `build/gate1/reference-diagnostics-v1/independent-review.json` | `2090f3af01a3bf01eec264b59c5522b8e74e0111d1e0fa33f59f386d4f6de6b9` | Independent raw semantic/minute-grid scan matches all 12 scenario aggregates; diagnostic-only |
| `build/gate1/diagnostic-addendum-verification-20260905T063458Z.json` | `5265193660cf8d3aed8ffc38183fa4cd4b68a7ac360e6179c3c0682cf278223d` | Separate 40-check actual-input hash/arithmetic/recorded-outlier audit with explicit limitations |
| `build/gate1/verify_diagnostic_addendum_20260905.py` | `6813440c62f10d801eab47b7a0e057e7ff19ee1ef2527c0f9741c2d4c7972ad4` | Inspectable independent addendum QA helper, no production imports or input mutation |
| `build/gate1/price-outlier-drilldown-20260905.json` | `19cb6a480e409edc3fcc5c6f30d280b90d17440b9ffce44690412b12270c30b7` | Recorded annotation/context drilldown, not bad-quote adjudication |
| `build/gate1/inspect_price_outliers_20260905.py` | `877d3630225d1427d3771b88572c925d3743bce39d308f0bcc5ce808e8c2c2b3` | Read-only retained-neighbor drilldown, no flag-policy or source changes |
| `docs/reports/fbs-historical-calendar-inquiry.md` | `7dddd77778c6073d811c125a3271d739632d687c39e355006d6b11b647618341` | Prepared general technical inquiry and UTC baseline references; NOT SUBMITTED |
| `build/gate1/support-contact-20260905/request-prepared-not-submitted.png` | `b7773d9037bde4e93a73afde7f90de2c48a236914a0b1161af7eeba15c1b3be9` | Official form with unsubmitted draft; no chosen contact identity entered |
| `build/gate1/reference-diagnostic-final-20260905.log` | `bdb7904fe5cbddc4f92f63d70fefc15697fe51930e32453b50e53c4bce8eb016` | New full local 735-test/static/coverage verification; not committed CI |

The continuity probe is source-viability evidence, not this gate's raw/clean acceptance corpus.
At the 2026-09-04 21:59:31 UTC assessment it was observed stopped at 128/190 checkpoints and
27,704,840 ticks; the cause was unconfirmed. Its owning task resumed acquisition on September 5
at 05:31 UTC; the CPU-active worker was revalidating the same saved count, not yet fetching a
new chunk. No duplicate was launched by this task. These are dated observations, not live status.
By September 5 06:34:11 UTC, the worker was fetching new checkpoints: 166/190 completed,
36,444,994 primary ticks, zero recorded invalid rows/fetch errors, retrieval still incomplete.
Do not derive current run progress from an older partial report. The 20:15 UTC
observability capture and its embedded dashboard hashes describe that earlier snapshot;
the dashboard-definition rows above describe later files, not retroactive screenshot evidence.

## Local baseline and separately verified deltas

The frozen local baseline used dirty HEAD `b05d47b6135681b5007601e3a4758ac827278c4b` and source-tree
SHA-256 `2fd01582136ebec951055c93ca9600178b4f768ecbac302cb741ef967fd27ef9`. It passed 530 tests with
warnings as errors, 88.29% total coverage, core 96% and non-core 87% (required 90%/80%), frozen
uv lock/sync checks, Ruff formatting/lint, mypy, Bandit, and the Linux dependency audit.
The UTC, Africa/Johannesburg and America/New_York replay checks and six demos passed; demo
manifests and canonical metrics were identical. Before/after hashes preserved the tested scope.
These are local checks of that snapshot, not committed CI or a full test claim for later changes.

The original Windows observer audit failed on its seeded pip 24.2 (seven advisory records,
six distinct advisories); that failure is retained in the baseline result. Only the observer
environment was upgraded to pip 26.2. Its independent metadata re-audit then passed with no
known advisories and an unchanged before/after package inventory. Neither this remediation
nor the dashboard work changed the active acquisition environment or the corpus lockfile.

The later broker-only delta passed 64 tests, Ruff formatting/lint, strict mypy and Bandit.
Its Python hash is `e3b2d29e54273ba89bef11879552edcd1117a3a930852f1b3f4bc713bdd3cb3b`. A subsequent
JSON-only layout/table-join delta passed all 34 real query definitions through Prometheus and
Grafana. Synthetic, non-ingested fixtures also exercised the actual Grafana transforms for
positions, orders and quotes: each produced two correctly joined rows, including no-expiration
mapping. Those fixtures prove rendering behavior, not the existence of real positions. New
research components and launcher display changes are separate post-baseline deltas.
The final broker browser capture showed Fresh/Observing, demo, zero real positions/orders,
readable price/profit headers and no console errors. Account-change behavior is simulated in
unit tests; no live terminal account switch was performed. The native foreign-Host check
returned HTTP 403 without account/financial fields. These scoped checks do not authorize trading.

The later engineering-replay consumer passed 36 focused monitoring tests with warnings as
errors and scoped Ruff formatting/lint, strict mypy and Bandit. Its first real published
artifact is explicitly synthetic: 320 bars, 256 warmups, 2 suppressed decisions, zero abstentions
and 62 forecasts. This is completed engineering evidence, not live trading or strategy evaluation.
The producer's independent verification is recorded in `docs/reports/core-engine-engineering.md`;
its test counts and snapshot are not merged into the earlier 530-test baseline.
After the broker and research integrations, before the final JSON freshness patch, the complete monitoring unit-test group passed
101 tests with warnings as errors (`--no-cov`, 5.13s). That final invocation is retained in
the task execution history, not a redirected stdout artifact or new whole-repository run.
The restarted observer reported the same verified synthetic 320-bar summary and Prometheus
returned 320 for its bar count. The launcher display-only cast was checked by PowerShell parsing
and an actual idempotent launch that displayed all four monitoring service rows correctly.
After the final instant-query/layout fix, 13 focused research tests passed in 2.81s. All four
final research targets matched direct Prometheus, Grafana proxy and actual Grafana query-frame
labels/values at one identical instant, with exactly one numeric sample per series and matching
loaded configuration. The final 1280x720 browser capture showed all four counts and the complete
intro without console warnings/errors. This read-only check did not mutate reports or metrics;
live missing/rejected transitions remain covered by scoped unit/config tests, not induced here.
The final post-fix complete monitoring group also passed 101 warnings-as-errors tests in
5.00s without coverage collection, retained in task execution history. Native command-line
verification after selective observer restart confirmed its replacement `--gate1-report` path.
The report was missing at that historical observation. The September 5 observer now exposes
the completed reproducibility PASS separately from quality FAILED, calendar UNKNOWN and Gate 1
false. The data-quality freshness-only JSON/test delta passed 24 focused tests and Ruff
formatting/lint. The post-change runtime audit passed all 54 query/frame comparisons with zero
errors, matched loaded configurations, and verified that broker, research and quality snapshot
targets are instant-only. This prevents absent current flag series from retaining older values.
The September 5 browser capture at 1646x912 visibly shows rebuild YES, tick-quality issues
and indeterminate calendar acceptance, with no console warnings/errors. Its Broker link opens
a fresh demo snapshot with zero actual positions/orders. These are dated read-only observations,
not a simulation of positions or a strategy-performance claim.

On September 5 a newer whole-repository local run passed 703 tests with warnings as errors
in 81.09 seconds, 88.24% overall coverage and 96%/87% core/non-core tiers. Its stdout and
separate coverage file are retained. Ruff formatting/lint, strict mypy (82 source files)
and Bandit also passed locally. This is a newer snapshot, not an addition to the old 530-test
count and not committed CI. A subsequent test-only delta explicitly pins NONPOSITIVE_ASK
and single-source NOT_EVALUABLE behavior; its 10 focused quality tests and Ruff checks passed.
No production data/pipeline source changed for that test delta.
The final post-delta whole-repository rerun then passed **704 tests** with warnings as errors
in 70.30 seconds, 88.24% overall coverage, strict mypy on 82 source files and both coverage
tiers (core 96% >= 90%; non-core 87% >= 80%). It used its own isolated coverage file and
preserved stdout in `build/gate1/resume-final-20260905.log`. All 27 corpus dependency hashes
and the completed report hash still matched after these checks. This supersedes the 703-test
snapshot for local test status, without becoming committed-SHA CI or human approval.

The resumed launcher's readiness limit is 300 seconds by default, configurable from 15 to 600;
PowerShell parsing and an actual idempotent retry passed without duplicating the four services.
Its source hash is recorded below, separately from the frozen corpus dependency closure.

| Verification artifact | SHA-256 | Scope |
|---|---|---|
| `build/gate1/local-verification/20260904T205444Z-exl46rvi/result.json` | `6d1fe9a0eefcef2260dc3a657a9d73b6b46270676c5ce2a587d4e923c01c2043` | Completed baseline, including the original observer audit failure |
| `build/gate1/local-verification/20260904T205444Z-exl46rvi/stage-baseline.json` | `189e2f408c163a88c9a8de6555dd1914e7d98c21c0387c4fd47c22a1408ba348` | Early baseline capture before permitting post-baseline edits |
| `build/gate1/local-verification/20260904T205444Z-exl46rvi/remaining-scope.json` | `562d805b68b1b0ef809c7f8bcafdf2294cd9ceb988a33d227678e6283b705a6c` | Remaining timezone/demo commands and unchanged dependency closure |
| `build/gate1/local-verification/20260904T205444Z-exl46rvi/windows-observer-reaudit.json` | `ec98406f06d03bf8553d248dfb9cc912a2529d4c6b68ebfb30e2084c2d995883` | Post-baseline observer pip 26.2 audit, passed |
| `build/gate1/observability/broker-delta-verification-20260904T212207Z.json` | `530073c541f299569136fa70d7261d931b93b0573bf64eb35a91c564554f10ca` | 64-test Python delta; predates the final JSON-only polish |
| `build/gate1/observability/broker-query-validation-20260904T212950Z.json` | `c310d76a6db704f1275516f9dbc557f69f8eb8a3e8ac5225a4ad53b79c4dd22a` | Final broker dashboard's 34 live queries through both services |
| `build/gate1/observability/grafana-transform-fixture/result.json` | `9f1674c89b97b54035cb89de5fccf94c12c666d88359d40d8de42b79dd4d5b2f` | Three actual Grafana table transforms with explicitly synthetic nonempty inputs |
| `build/gate1/observability/broker-live-final-v2.png` | `5e14f2e1667b0df9b85cd81a214d28699373d1ae440937297fc07f3286773bff` | Actual final broker view, fresh demo with genuine zero positions/orders; no trading action |
| `build/gate1/observability/research-delta-verification-20260904T214040Z.json` | `02729561f62fd3c4534fc8e0cadd6fd408641287f17b5a4bb0d91a6925d907c3` | 36-test consumer/static-check record, updated with final 13-test instant-query/config checks |
| `build/gate1/observability/research-query-validation-20260904T215154Z.json` | `51d32b30bdf6f46f3731c223b66e62d9425783a720a190fb790eae0efbfe0e5f` | Four actual instant queries: direct Prometheus, Grafana proxy and Grafana frames match |
| `build/gate1/observability/research-live-final-v2.png` | `af6480af823dda315b33fc00616cdd4cea4638ab8ea0f307cf86c045cb380464` | Final rendered synthetic engineering-only view at 1280x720; no console warnings/errors |
| `build/gate1/observability/dashboard-after-corpus-20260905T054253Z.json` | `8f9f2ef6929ddd06198b14d173aafd3417dafab17f41c3a8dfb0a666c8bdefdb` | Post-corpus/freshness-patch runtime: 54 query/frame checks pass, three snapshot dashboards instant-only |
| `build/gate1/observability/data-quality-completed-20260905.png` | `4220d436ea1f3ae67999872a5e9f0adbdb1a21d23a08035a707baea1d2067d6b` | Actual completed-report dashboard at 1646x912; reproducibility YES, quality issues and calendar indeterminate |
| `build/gate1/observability/broker-restored-20260905.png` | `ea66f46a65a2f431b284ac52846c10f229d56ffc59cd8c63e91cdcd81c32ea50` | Restored observer's genuine fresh demo snapshot, zero positions/orders; view-only |
| `build/gate1/resume-verification-20260905.log` | `eb42c6b76574987a2d610c65824b461d3a46458db78d836a1b99fcf425e491d5` | September 5 local full-suite stdout: 703 warnings-as-errors tests passed; predates the test-only assertion delta |
| `build/gate1/resume-static-checks-20260905.log` | `bc76124d9b4f3f45a9134c2b482d910adb12747ff4ab097aeda2e0d0e62d1d9c` | Local Ruff format/check, strict mypy and Bandit command stdout; not CI |
| `build/gate1/resume-coverage-tiers-20260905.log` | `6932e08e1901a322f0698f8db17ec93316a90a3d86a9de504b059d5a501ace98` | Separate 96% core / 87% non-core local coverage thresholds passed |
| `build/gate1/resume-final-20260905.log` | `c70fc11018d5a1c32dc6d2fa898a0dbab1c3ce3d83a3d3d57ea5db5b90445d94` | Final post-delta full local run: 704 warnings-as-errors tests, strict mypy and both coverage tiers pass; not CI |
| `scripts/start_dashboard.ps1` | `50b9a4d2b3b2ef13fa279f37338ed5f4c629a630f06cf4883c72fe8ea632b9cd` | Configurable cold-start readiness limit; ownership/health checks retained |

## Reproduce

```bash
uv run --no-sync python -W error -m pytest
uv run --no-sync ruff check .
uv run --no-sync mypy
uv run --no-sync python scripts/validate_gate1_calendar.py --replay-report docs/reports/gate1_calendar.json --output build/gate1/calendar-replay.json
uv run --no-sync python scripts/check_evidence.py docs/reports/gate1_evidence.md
```

The evidence checker is expected to exit nonzero while the failed categories remain
unresolved. It must not be weakened to turn this candidate green. Machine checks neither
authenticate people nor issue approval.

The completed recovery's command is retained in `HANDOFF.md`; its selection/report and
independent audit are attached above. Preserve these completed artifacts. A new corpus command
requires a new output directory; never rerun into this result. The first attempt was stopped
after its freeze violation, not approved. A run with fewer than
30 sampled days is engineering smoke evidence only. The rebuild hashes its CLI, top-level package
initializer, full core/data subtrees, pyproject, lockfile and frozen SPEC before and after execution.
Unrelated monitoring modules are not corpus inputs. Any change within that dependency closure
invalidates implementation-unchanged evidence; input plans and quality policy must also remain
fixed. The completed local baseline and later delta checks above are not committed CI; final
current-candidate CI and both human approvals remain owed.

To repeat only the independent read-only byte/count audit against the preserved completed
corpus, use `./build/monitoring/venv/Scripts/python.exe build/gate1/verify_completed_corpus.py`.
This scans existing Parquet files and writes a new timestamped verification record; it does
not fetch ticks, rebuild bars, modify reports or approve a gate. The original audit used
the separate Windows observer runtime recorded in its artifact, not the producer's WSL runtime.

## Sign-off

Independent reviewer: Delsa Mashiki's approval is reported by Isaac and preserved in
[the receipt](gate1_approval_receipt_20260905.md); final evidence-bound review remains
incomplete in [gate1_independent_review.md](gate1_independent_review.md).

Principal: Isaac Gumbi's approval is recorded; final candidate, evidence, CI and
reviewer bindings remain incomplete in [gate1_principal_approval.md](gate1_principal_approval.md).

Both decisions still need completed evidence bindings. See the [approval guide](gate1_approval_guide.md) for
binding the same candidate/SPEC/evidence/CI identities, preserving the unsigned snapshot,
and mirroring actual names/dates for the unchanged checker. Do not sign APPROVED while
the reference-month acceptance report, human bar checks or current candidate CI are missing.

## September 5 approval-preparation update — not gate approval

The latest canonical local verification passed 872 warnings-as-errors tests in 99.33s,
88.40% overall coverage, Ruff formatting/lint, strict mypy on 90 files, Bandit and
96%/87% core/non-core coverage tiers. Its record is
`build/p3-root-verification-20260905/result.json` SHA-256
`51067f34d76f43bf84129510cd94efe87e1661ca06b87a082db8b3e073045ffe`.
This later engineering snapshot is not current committed-SHA CI or human approval.

The final acquisition candidate is now present at
`docs/reports/fbs-tick-continuity-v1-candidate.json` SHA-256
`3bebefcb5a5d672f86808e9dc2c88fd023bd6a35860049ef12ea1e4eb5cb60cd`.
Its sidecar is `docs/reports/fbs-tick-continuity-v1-candidate.json.sha256` SHA-256
`cae36ba62edd20f1ea7e470a17b43d72726f8acc4d2ae1fbee3f92df708201f4`.
Retrieval completed at 190 checkpoints and 42,796,598 primary ticks. Structural
evidence is FAILED because 379,467 primary crossed quotes occur in the Brexit window;
758,934 counts the two repeated fetches. Source viability is not accepted. Under the
existing SPEC §13 scope, that broader P3 stress-history finding is distinct from the
bounded October Gate-1 evaluation; neither may be used to conceal the other's failures.

Before this additive documentation update the previous pack was preserved verbatim at
`build/gate1/approval-preparation-20260905/pre-signoff-evidence.md` SHA-256
`a64df319ea62608830f370cdceb8dd64974e285e84d5c50d1d482b6d1ae28430`.
The earlier seal applies to that archived snapshot, not to the subsequently updated
pack. Raw data, produced reports, their sidecars, SPEC, policy and checker were not
changed to prepare these forms. Categories 1, 2, 4 and 5 remain FAILED.

## September 5 pre-use repairs — acceptance remains held

See [the preparation report](preuse_preparation_20260905.md) for the dashboard,
demo-account and source-disposition checks. FBS personal/account-holder permission
is Principal-reported and is no longer an outstanding permission inquiry. Training
and refinement remain demo-only; this does not answer the historical calendar questions.

The final additive admission manifest is `docs/reports/fbs-data-admission-v2.json`
SHA-256 `e5beb620cfb17e2df7e1ee735debcf6432a328cf2d13e20e5dd38cbc8d3871a1`.
Fresh file hashes and complete-set rechecks reconcile all 190 partitions. Ten are
QUARANTINED (six quote-defect and four empty); 180 remain QA_ONLY. The manifest
does not promote a research snapshot or impose an access control on existing
engineering replay. All original raw data and sealed acquisition results remain
unchanged, including the crossed/locked evidence.

The actual USD 1,000 demo starting account passed 14/14 read-only checks in
`build/preuse/demo-baseline-20260905-v2.json` SHA-256
`e15f10ad68600257dd2473bb610d7f78c50696ea54721e53a611a2a8c3be254b`.
The repaired dashboard was inspected visually through acquisition, quality, broker
and overview navigation; it displays completion separately from failed/indeterminate
quality and leaves execution disabled. New current snapshot definitions loaded
without a restart. The retained 54-query/frame audit is
`build/gate1/observability/dashboard-after-corpus-20260905T090919Z.json` SHA-256
`8ec86dca14dd6e95d64e679df16aa68ac704a9ee70cdad2b3c78e037e3c54fc1`.
These observations do not turn missing reference-month or human-check evidence green.

The subsequent whole-repository local check passed 962 warnings-as-errors tests
(87.20% total coverage; 96% core / 86% non-core), Ruff format/check, strict mypy on
101 files, Bandit, pip-audit and two isolated byte-identical Gate-0 demos/canonical
metric digests. Its full source/configuration inventory was unchanged during checks:
`build/preuse/local-checks-20260905T093126Z/result.json` SHA-256
`a054b9cbda82e93a278a98d8399977a5bd3145e1f57921ed8df90dbcd56305fb`.
This supersedes the earlier 872-test local snapshot for these changes; it is not
committed-candidate CI and does not change the unresolved evidence categories.

The new reference-month evaluator's actual-time run is
`build/gate1/reference-acceptance-readiness-20260905T094337Z/report.json` SHA-256
`62d75f4c43180e1dbe9dc65ebd77055c2466577eaa25dfe40f8b76fac0b139f4`.
It verifies the complete pinned producer file inventory and finds 16,608 October
close-month EURUSD bars with matching tick-minute coverage, but is **INDETERMINATE**:
0/31 approved calendar dates, no expected-minute denominator, draft flag treatments
and no exact policy/calendar approval bindings. This is the October subset of the
multi-year sample, not a complete reference-month rebuild. Gate/training flags stay
false. The earlier `100000Z` readiness artifact is preserved but superseded because
its timestamp was caller-supplied in the future. See the preparation report for
diagnostic-count interpretation and actual-time provenance.
