# Tradebot handoff — Phase 1 in progress

## Current assignment and status

Latest source delivery: `5b56316a82749a8e60a9fea3596871c69325d400` is committed and
pushed in [draft PR 4](https://github.com/isaacgu/tradebot/pull/4). Required CI
`quality` and `secrets` passed in run 33966725497: 1,610 warnings-as-errors tests,
88.34% coverage. No merge to `master` occurred. The purpose-scoped admission guard,
reference-month repairs and dashboard's separate report-integrity/code-match
indicators are implemented. See [the exact new delivery and remaining requirements](docs/reports/guard_data_publication_20260905.md).

Both the 320-bar synthetic decision replay and four-case/eight-fill synthetic
execution/accounting runner were run twice from this source, with identical repeat
reports and zero broker orders. The dashboard now shows Current code. This is an
offline engineering foundation, not genuine model fitting, full financial validation
or an always-on trading system. Real-data training was not queued or performed.

The acquisition task's source-frozen verification sequence is
`build/gate1/reference-repair-verification-20260905-v2/`, launched 12:31:33 UTC.
Its observed log reached reference-month rebuild 1/2 after importing 25 inputs;
no final result existed at observation. Preserve all run inputs and do not start a
duplicate. The source-backed v4 calendar/counting definition remains a proposal
requiring the recorded Delsa/Isaac decision, not an adopted FBS session schedule.

Gate 1 remains unapproved; evidence categories 2, 4 and 5 remain FAILED. The
register's older CI/candidate bindings and all human forms still need a final
exact-package binding; the new CI does not transplant previous approvals.
Historical uncommitted/no-CI descriptions below refer to their earlier snapshots.

Latest September 5 preparation: the operator confirmed **USD 1,000 demo starting
capital**, matching planned live capital; live orders remain unauthorized. See
`configs/accounts/demo_usd_1000.json`, the read-only `scripts/verify_demo_baseline.py`,
and [FBS permission scope](docs/reports/fbs_data_permission.md). Isaac reports FBS
allows account-holder data use for his personal project without further verification.
Do not send a duplicate permission request. Historical liquidity/timestamp evidence
and gate acceptance are separate, still unresolved technical requirements.

The [pre-use preparation report](docs/reports/preuse_preparation_20260905.md) records
the dashboard repairs, actual broker-baseline checks and final additive source
disposition at `docs/reports/fbs-data-admission-v2.json`: 10 QUARANTINED partitions
and 180 QA_ONLY, with all strategy/execution eligibility false. V1 and original raw
artifacts remain preserved. Quarantine is an evidence manifest, not a new access
control in the engineering SnapshotBarFeed. Do not describe all collected data as
ready for strategy training or merge these findings with the separate reference month.

Approval statements from Isaac and his report of Delsa's approval have been received;
see [the receipt](docs/reports/gate1_approval_receipt_20260905.md). Prior descriptions
below of unsigned templates are historical; final evidence-bound decisions and the
five documented human bar checks are still incomplete, not silently granted.

The user requested Gate-1 finalization and explicit human approval documents. Start with
[the approval guide](docs/reports/gate1_approval_guide.md); the
[independent reviewer form](docs/reports/gate1_independent_review.md) and
[Principal form](docs/reports/gate1_principal_approval.md) remain unsigned and not ready
for approval. Required reference-month policy/calendar/evaluator evidence, human bar
checks and current committed-SHA CI are still owed. The previous sealed Gate-1 pack
was preserved at `build/gate1/approval-preparation-20260905/pre-signoff-evidence.md`
before updating links and dated status notes. Neither sign-off was fabricated and
no criterion, threshold, source report or checker was changed.

Parallel core-engine preparation is documented in
[the core-engine handoff](docs/core-engine-handoff.md). Its synthetic decision
replay is engineering evidence only; it does not change P1 status or certify a
strategy. The UI/data task and core-engine task have established direct coordination.

The September 5 research-control increment is now published as eight new, uncommitted
files: three modules, three test modules, ADR 0011 and an engineering report. It adds
immutable engineering declarations, failed/incomplete attempt evidence and chronological
split controls with denied lockbox execution. See
[the P3 engineering report](docs/reports/p3-research-controls-engineering.md).
Root canonical verification passed 872 warnings-as-errors tests in 99.33s, 88.40%
coverage, Ruff formatting/lint, strict mypy on 90 files, Bandit and 96%/87% coverage
tiers. The log is `build/p3-root-verification-20260905/checks.log`. This is local
software evidence only. No existing runtime entry point, dashboard report pointer,
frozen pipeline source, HEAD or Gate-1 evidence-pack bytes were changed by publication.
The README/HANDOFF status update is separate from that sealed evidence pack.

At 2026-09-05 06:50:41 UTC, the local acquisition exporter reported 190/190
checkpoints, 42,796,598 ticks, zero invalid checkpoints/fetch errors and retrieval
complete with the worker stopped. Final report/sidecar validation remains owned by
the acquisition task; this observation alone does not approve source viability or Gate 1.

Gate 0 remains approved. Phase 1 data engineering and the whole-system Grafana overview,
acquisition, data-quality, read-only broker and engineering-replay views are implemented candidates. Gate 1 is
**not approved**; its [incomplete evidence pack](docs/reports/gate1_evidence.md) lists the missing
CI, actual reference-month quality acceptance and human approvals. The completed 30-day
reproducibility check passes, but the corpus quality summary is FAILED. Live exposition and
rendered screenshots now discharge the inherited screenshot-format obligation, not quantitative
quality acceptance. Post-baseline broker hardening has separate targeted verification.

Implemented P1 building blocks include the shared timestamp normalizer/deferral path,
checksummed resumable FBS acquisition, immutable Parquet raw/clean storage, quality diagnostics,
bar construction/rebuild tooling, field-vintage calendar storage, explicit liquidity-calendar
lookup and the gate evidence checker. A captured local baseline passed 530 warnings-as-errors
tests, Ruff/mypy/Bandit and both coverage tiers; this is not committed CI or a full test claim for
later broker/research edits. The September 5 final current-candidate rerun passed 704 tests
with warnings as errors, 88.24% overall coverage, strict mypy and core/non-core tiers of
96%/87%. The later provisional-diagnostic snapshot passed 735 tests and the same
coverage tiers; both runs remain local evidence, not CI or Gate-1 acceptance.

The source-backed calendar example is real: the Fed's November 7, 2024 release was captured,
four fields were imported, and reopened cutoff queries and an offline replay passed. Original
historical vintages are unproven, so these fields use retrieval availability and
`AS_OF_UNVERIFIED`. Separate synthetic revisions test mechanics only. The calendar report
retains source URLs, timestamps, hashes and database artifacts. No historical FX liquidity
dates have been approved; calendar-dependent quality remains indeterminate. The October 2024
Sunday-open discrepancy may involve historical timestamp/session alignment or coverage; its
cause is not diagnosed. Rebuild determinism cannot certify historical UTC interpretation.

The system dashboard is the entry point for the whole bot, not only downloads. Data/run health
and completed engineering-replay summaries are available now; economic backtests, accepted
strategies, portfolio, risk, execution and bot PnL remain explicitly not implemented/no evidence
until their owning phases deliver them. Index CFDs stay
`data_only`. There are no trading orders, real fills, strategy-performance results or gate
approvals implied by dashboard visibility.

The immediate next steps are to review the completed reproducibility evidence and 34 retrospective
price-outlier findings, resolve the reference-month counted-flag policy explicitly, source approved
dated liquidity expectations, hand-verify the five
prepared venue-matched references, run CI on a committed candidate, and obtain the separate
required human sign-offs. Five automated broker Bid matches and five actual production Mid
matches are recorded, but none is a human signature.
The evidence checker reports unresolved requirements; it cannot authenticate signatures or
approve a gate. Use the README for whole-system concepts and reproducible commands.

## September 5 parallel clarification and provisional diagnostics

The Principal authorized both contacting FBS and continuing bounded offline engineering
without waiting for a reply. This does not approve a provisional liquidity calendar,
rewrite source timestamps, select a gate-counted flag policy or waive any Gate-1 requirement.
The [prepared technical inquiry](docs/reports/fbs-historical-calendar-inquiry.md) remains
**NOT SUBMITTED**: the official contact form contains a prepared draft but requires a chosen
name and email, which have been requested from the Principal. No contact details were entered,
no CAPTCHA completed and no Submit pressed. No reply or case number exists.
The form screenshot is `build/gate1/support-contact-20260905/contact-fields-required.png`,
SHA-256 `368cd46ee4cdf2d6702efe6999f32421f923af91370880ead4a0b0caf1848165`.
No account identifier or raw tick file is included in the prepared inquiry.

The new diagnostic is complete at `build/gate1/reference-diagnostics-v1/report.json`,
SHA-256 `f30dd82b88f70d5b72296c36de6c326f12cdea2f4968446d93b530ea1c1dc07b`.
It preserves the candidate's acquisition **open-date** view and separately computes SPEC §3.4
canonical session **close-dates**. The true October view includes September 30 open / October 1
close and excludes November 1 close. Its frozen manifest adds the two existing September 30
boundary observations to the candidate's 46: 48 checkpoint/raw pairs, 11,128,900 scanned ticks.
The close-date month itself contains 5,288,101 EURUSD and 5,335,980 GBPUSD ticks.

Against 33,060 provisionally advertised minutes per symbol, the UTC baseline observes ticks
in 32,056 EURUSD / 32,143 GBPUSD minutes, leaving 1,004 / 917 evaluable minutes without
observations. These are not proven market outages or liquid-hours quality rates. The -2h/-3h
counterfactuals retain 480/720 unknown minutes per symbol; neither is selected as a correction.
An independent stream reviewer reproduced all 12 close/open-view scenario aggregates and
semantic hashes. A separate 40-check audit rehashed all 96 checkpoint/raw files, reconciled
the close-month counts, and checked the retained outlier drilldown arithmetic.
No approved calendar, gate-counted flag definition or reference-month P1 clean-flag result
is supplied; source-checkpoint flags are not relabelled as P1 quality flags.

The 34-case recorded price-outlier drilldown retains five stored neighbors each side and
shows absolute prior-stored-tick Mid changes of 1.15–6.65 pips, without adjudicating bad
quotes or changing data/policy. Its artifact and limits are attached in the Gate-1 pack.
The older sealed 704-test evidence remains historical. The new final local snapshot passed
735 warnings-as-errors tests in 87.10s, 88.24% coverage, Ruff formatting/lint, strict mypy
on 84 files, Bandit and 96%/87% coverage tiers. Log:
`build/gate1/reference-diagnostic-final-20260905.log`, SHA-256
`bdb7904fe5cbddc4f92f63d70fefc15697fe51930e32453b50e53c4bce8eb016`.
All 27 corpus dependencies and the completed corpus report remained unchanged.

## Active run and verification handoff

- Acquisition resumed through its owning task at 2026-09-05 05:31 UTC: launcher PID 15124,
  worker PID 206676 and existing terminal PID 203900. The worker was CPU-active revalidating
  saved data at 128/190 chunks and 27,704,840 ticks; this was not yet a new-download count.
  Resume logs are `build/fbs-tick-continuity-v1/resume.stdout.log` and `resume.stderr.log`.
  This task did not launch a duplicate. The earlier 2026-09-04 stopped observation is historical;
  its cause remains unconfirmed. Inspect current process/checkpoint state before acting.
  At 06:34:11 UTC (08:34 SAST), new fetch/checkpoint work had reached 166/190 and
  36,444,994 primary ticks, zero recorded invalid rows/fetch errors; retrieval remained incomplete.
- The first offline acceptance rebuild at `build/gate1/30day` was stopped by its owner
  after a dependency-hash mismatch; it cannot satisfy the unchanged-code criterion. Its frozen
  selection contains 30 EURUSD dates, seed 20260904, timeframe 1m, batch size 16384 and
  6,719,590 primary ticks. Selection SHA-256:
  `e98315febcd4fe6c064302069a790b5aa0e3bd0b0f6dd3a885d3ba65f6b0962c`.
  Imports produced 32 immutable raw files; first rebuild work was interrupted, not completed.
  Its owner sent Ctrl-C only to owned Codex execution session `70573` (a task session, not an
  OS PID), which returned exit 1. Do not start another job into this existing append-only
  output directory. Raw and intermediate artifacts remain preserved.
  The exact launched command was:

  ```powershell
  wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/isaac.gumbi/Documents/ChatGPT/Bot && PYTHONTZPATH= .venv/bin/python scripts/build_gate1_corpus.py --days 30 --batch-size 16384 --output-dir build/gate1/30day'
  ```

  It uses the pinned WSL `.venv` Python 3.12.14, not the Windows monitoring interpreter.
  At 2026-09-04 21:28 UTC, the first sort had approximately 200 scratch Parquet runs;
  this is historical intermediate work, not completed-bar or gate progress.
- A read-only drift audit at 21:53 UTC found 26/27 declared code files and the quality
  config unchanged. `src/tradebot/data/boundary_probe.py` changed from
  `a0160ed5717fcf1610a111747f90dac7730e4cb82905ea883f6867f66724f712` to
  `b102ecdd76aa4d3342b9be04be50ad5012e22866c9c93715fadea15f8802708e`, with file mtime
  20:53:26 UTC. Authorship is not established. `build/gate1/corpus-drift-20260904T215338Z.json`
  and its separate current-source snapshot preserve the failure; restoring a file later
  would not erase it. The later 530-test baseline captured the changed hash both before
  and after its own run, so that separate historical test result remains correctly scoped.
  The owner's exact-process check found no surviving old worker before recovery.
- The replacement completed in `build/gate1/30day-stable-b102ecdd` at
  2026-09-04 23:30:06 UTC (September 5 01:30 SAST), after approximately 94 minutes 32 seconds.
  Its historical execution session was `64411` (not an OS PID). The actual launch command was:

  ```powershell
  wsl -d Ubuntu -- bash -o pipefail -lc 'cd /mnt/c/Users/isaac.gumbi/Documents/ChatGPT/Bot && PYTHONTZPATH= .venv/bin/python scripts/build_gate1_corpus.py --days 30 --batch-size 16384 --output-dir build/gate1/30day-stable-b102ecdd 2>&1 | tee build/gate1/30day-stable-b102ecdd.log'
  ```

  The new selection SHA-256 is `ff7465412af8834cc4b1634671e49e036ad8751b34c4483a6fae8cde12fddbf1`:
  30 distinct EURUSD dates, 6,646,477 primary ticks, seed 20260904, 1m and batch 16384.
  The candidate pool grew, so the same plan/seed produced different days from the original;
  do not reuse the old 6,719,590 count. The 21:59 UTC recovery-start audit matched all 27
  code hashes and the quality config. Both rebuilds now contain 14 matching clean files,
  6,646,477 ticks and 41,701 one-minute bars from 31 immutable raw files. The producer reports
  reproducibility PASSED, unchanged raw/code and byte-identical clean outputs. An independent
  read-only audit passed all 25 checks against actual files, counts, identities and current hashes.
  Report SHA-256: `3c7226e91d4c9a0a632ee85ed8ad273d6493256c0e0427c16e6aca5f047682bb`.
  Audit: `build/gate1/completed-corpus-verification-20260905T054036Z.json`, SHA-256
  `6728e346aa3884ff3ae935f5a15eb36dbd0d177caad895ebdc4aa510cf4aeea2`.
  This does not certify historical UTC interpretation or Gate acceptance.
- Preserve completed raw, clean, selection and report artifacts. Later implementation changes
  need their own evidence rather than relabelling this completed snapshot. Frozen CLI SHA-256:
  `4c605bbcef8416f9f385ccd44f69e955f13b3edc986768d44f2b72cf3a976918`.
  Quality remains FAILED specifically because of 34 retrospective PRICE_OUTLIER annotations;
  all 6,646,477 rows remain causally eligible and 1,465 adjacent repeated payloads are retained.
  All 30 selected dates lack approved liquidity calendars. The 7,589 GAP_CALENDAR_UNKNOWN
  tick events are not proven missing market data. The mixed-date sample is not a reference
  month's approved liquid-hours denominator. The original run remains ineligible.
- Local baseline evidence is under `build/gate1/local-verification/20260904T205444Z-exl46rvi/`.
  The 530-test snapshot has core coverage 96%, noncore 87%, total 88.29%, plus passing locked
  dependency checks, Ruff, mypy, Bandit and Linux dependency audit. `stage-baseline.json` records
  unchanged code/HEAD before later source edits; `remaining-scope.json` separately proves all
  three timezone replays and six identical demo manifests/canonical metric digests. These are
  local uncommitted evidence, not CI or approval.
- The additional Windows observer audit found advisories in seeded pip 24.2. That failure is
  preserved; upgrading only the observer environment to pip 26.2 was independently re-audited
  successfully in `windows-observer-reaudit.json`. Acquisition/corpus environments were not
  changed by this remediation. Broker/research changes after the baseline require their own
  scoped results and must not be described as covered by the original 530-test whole-tree run.
- The post-baseline broker Python delta passed 64 tests, Ruff formatting/lint, strict mypy
  and Bandit. Final table/query artifacts and hashes are in the Gate-1 pack. Native smoke
  observed a fresh demo snapshot with zero positions/orders and foreign-Host HTTP 403;
  account switches were tested with unit fakes, not by switching the live terminal.
  After an intentional MT5 account switch, selectively restart the observer. The launcher
  table-display cast was a separate one-line delta that passed PowerShell parsing.
- The additive Engineering Replay consumer has separate 36-test/static-check verification.
  `/d/tradebot-research` has five panels: caveats, artifact state, source class, bars replayed
  and decision-status counts. The initial verified synthetic artifact contains 320 bars,
  256 warmups, 2 suppressed decisions, 0 abstentions and 62 forecasts, not trades or live calls.
  It verifies completed reports read-only; missing/invalid artifacts remain unknown. The
  separate core-engine handoff records that producer's own verification snapshot; do not
  merge its test counts with either the 530-test baseline or this later consumer delta.
  Combined monitoring verification before the final JSON freshness patch passed 101 tests with warnings as errors and no
  coverage collection (5.13s; actual command retained in task history). The restarted observer
  and Prometheus both exposed the expected verified synthetic 320-bar summary. This is not
  another full-repository run. After the final instant-query/layout fix, 13 focused research
  tests passed. All four targets matched direct Prometheus, Grafana proxy and Grafana frames
  at the same instant; the final research screenshot passed at 1280x720 with no console issues.
  The actual idempotent launcher displayed four service rows. Its later optional `-Gate1Report`
  parameter passed PowerShell parsing; use the selective exporter restart in the runbook
  to follow `build/gate1/30day-stable-b102ecdd/report.json` without changing exporter defaults.
  A final post-fix combined monitoring run also passed all 101 tests in 5.00s with warnings
  as errors/no coverage collection (task-history evidence, not a new whole-repository run).
  After selective observer restart, native command-line verification confirmed the exact
  replacement report argument; its report was missing at that historical observation.
  The resumed 2026-09-05 observer now exposes the completed reproducibility PASS separately
  from quality FAILED, calendar UNKNOWN and Gate 1 false. Research remains verified.
- The four dashboard services were restored on September 5. Grafana's cold start took about
  4m20s; the first 15-second readiness error was premature, not a stopped process. The launcher
  now defaults to 300 seconds (`-StartupTimeoutSeconds`, allowed 15–600). PowerShell parsing
  and an actual idempotent retry passed with the same four service processes, no duplicate.
- Final September 5 verification passed 704 warnings-as-errors tests in 70.30s, strict mypy
  on 82 source files, 88.24% overall coverage, core 96% and non-core 87%. The retained log is
  `build/gate1/resume-final-20260905.log`, SHA-256
  `c70fc11018d5a1c32dc6d2fa898a0dbab1c3ce3d83a3d3d57ea5db5b90445d94`.
  It supersedes the earlier same-day 703-test snapshot after the test-only assertion delta;
  Ruff format/check and Bandit also passed locally. All 27 corpus code hashes and the
  completed report still matched. Keep this separate from missing committed-SHA CI.
  The final 54-query/frame audit and actual quality/Broker screenshots are attached in
  the Gate-1 pack; the UI shows reproducibility PASS separately from FAILED quality.
- The current evidence checker intentionally exits nonzero for categories 1, 2, 4 and 5.
  Categories/obligations already supplied are not a gate approval. No monitoring automation
  has been created implicitly; further scheduled follow-up needs the user's request.

## Original Phase-0 assumptions (historical provenance)

- Submission of the document authorizes creation of a reviewable P0 candidate, but does not
  silently change its draft status or authorize Gate 0/P1.
- P0 “paper mode” means live-shaped forecast wiring. P4 still owns the PaperBroker, OMS, risk,
  reconciliation, and real broker adapter.
- P0 “backtest” is a causal smoke/replay, not performance evidence, so it emits no PnL and does
  not pretend that P2's cost model exists.
- NN-4 kill switches and NN-9 reconciliation become applicable when P4 introduces an execution
  path; they are not weakened or stubbed in P0.
- Both spring and autumn US/UK DST mismatch windows satisfy the impossible contemporary phrase
  “UK-DST-only week.” ADR-0003 records the exact interpretation.

## Principal decisions — ALL RESOLVED 2026-09-04

All eight are closed and adopted in ADR-0004. Recorded here for provenance; do not re-litigate.

1. **Freeze or edit** → Frozen at v1.0 **with errata**, supplied draft preserved byte-for-byte at
   `docs/SPEC-supplied-2026-09-03.md`. ADR-0004.
2. **Receipt-time admission** → Confirmed. Availability is `max(ts_event, ts_recv, available_at)`.
   NN-6's storage obligation is *not* subsumed by it. ADR-0002.
3. **P0 smoke-test boundaries** → Confirmed. NN-7 **applies** and is discharged by the label, not
   waived. The NN-1 code-parity claim is narrowed to `code_parity`, and the paper-mode limitation is stated
   explicitly in the evidence pack.
4. **FX daily intervals** → Confirmed: half-open `[17:00 NY, next 17:00 NY)`, boundary tick belongs
   to the new session, labelled by close, Sunday open labelled MONDAY. Broker-priced instruments use
   **their own** broker trading-day boundary; exchange cash-session bars are separate features.
5. **Clock thresholds** → **Three** distinct policies, not two, and ADR-0003's justification was a
   category error, now corrected. Local guard (1 s) ≠ host sync (250 ms) ≠ broker skew (§7.5).
6. **Gate-0 dashboard screenshot** → §10.6 rewritten to a three-status evidence model. Gate-0
   observability is the Prometheus exposition; only the screenshot *format* is deferred. ADR-0005.
7. **Data and broker sources** → Split three ways. The *decision* defers to P4; the broker's *price
   history* does not, because §6.4 binds before Gate 3. Constraints recorded in ADR-0007.
8. **`ts_recv >= ts_event`** → Retained as fatal for platform-produced events (`Bar`, `Forecast`);
   replaced for externally-clocked events (`Tick`, `Fill`) by an explicit `available_at` plus a
   measured, flagged, budgeted skew observable. **No epsilon anywhere.** ADR-0006 blocks the first
   P1 adapter.

Additional Principal decisions taken at the same time: cash/spot index CFDs with indices
`data_only`; Principal-reserved thresholds (§0 rule 9); `ENTRY_HALT` / `FLATTEN_HALT` halt states
(§7.5); position-level mandatory broker stops (§8.3); deep history moved from the P1 gate to a P3
entry requirement; branch protection on `master`.

## Evidence

- Design: `docs/adr/0001-platform-and-p0-stack.md`, ADR-0002, ADR-0003.
- Interface resolution: `docs/interfaces.md`.
- Gate candidate: `docs/reports/gate0_evidence.md`.
- Committed-SHA CI evidence: run `33852037018`, candidate
  `4de5f7a540ed216b3568141bd83392af3189c3cf`; details and hashes are in the gate pack.

## What the next role needs to know

Gate 0 is approved. The spec is frozen, Isaac Gumbi recorded Principal approval on 2026-09-04,
committed-SHA CI is green, and the §12.3 `master-release-gate` ruleset is active. The Principal
authorised temporary public visibility to make GitHub enforcement available without a Pro
subscription. Delsa Mashiki independently reviewed the committed candidate and CI evidence and
approved on 2026-09-04. Every Gate-0 evidence category reads `PROVIDED`. Phase 1 has since started
under its separate assignment. Its current implementation and unresolved gate requirements are
recorded above and in `docs/reports/gate1_evidence.md`; Gate-0 approvals do not carry into Gate 1.

**Preserve ADR-0006's adapter contract.** Throughout P1: preserve both `ts_event` and
`ts_recv`, add `available_at` and never normalise the raw local stamp, publish in
`(available_at, source, seq)` order behind a single head-of-line deferral queue, and use one shared
normaliser for both the historical and live paths (NN-1). Synthesise `seq` where the source has none
and keep it stable across re-ingests; deduplicate by **position over an overlap window, never by
hashing values**. Introduce a raw tick schema capable of representing locked and crossed quotes
(`ask <= bid`), flag them, and exclude them from clean mid/bar/trading delivery; the delivered `Tick`
continues to require `ask > bid`. Make clean bars use `ts_event == ts_close` and seal them with the
configured `seal_latency`. Treat venue as part of the instrument key — never merge two venues into
one series.

Two traps worth naming. Freshness and staleness read `ts_recv`, never `available_at`. And backfilled
events legitimately arrive years old with a current receipt stamp, so any arrival bound must exempt
them or the trader halts on its own recovery path.

Real instrument facts and data-source terms marked `[VERIFY]` require primary-source research and
ADRs. A third-party source is a *candidate* until its licence permits the intended use **in
writing** — silence is not consent (ADR-0007).
