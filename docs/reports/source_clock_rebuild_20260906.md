# Corrected-source rebuild — September 6, 2026

## Completed outcome

The entire sequence finished at **2026-09-06 13:48:40 UTC / 15:48:40 SAST**.
The mechanical rebuild passed, but the reference quality criterion did not:
**16 / 4,410 expected liquid minutes = 0.362811791%**, above the unchanged strict
**0.1%** limit. The exact comparison `16 * 1000 < 4410` is false.

The evaluator's formal status is `INDETERMINATE` because final hash-bound
independent-review and Principal approvals were not supplied to this diagnostic.
That missing binding does not erase the independently visible numerical failure;
adding approval metadata alone would not make the data acceptable.

The producer completed at **2026-09-06 13:30:32 UTC / 15:30:32 SAST**
with exit code 0 and `reproducibility_status: PASSED`. Its reported processing
time was 8,498.771 seconds (about 2 hours 22 minutes). Both builds used 5,905,147
selected primary ticks and produced 35,569 M1 bars and 25 D1 bars including
context. Those counts are not the reference acceptance denominator.

The producer report SHA-256 is
`ce0af1e24de335d869e887a3c6911fd07b5252b3ea6d3a4433760c3ac56f41ba`.
The same wrapper ran acceptance from **13:30:33 to 13:48:40 UTC**, about
18 minutes. All 82 launch-pinned inputs matched the read-only check; the final
wrapper reports `changed_inputs: []`. No duplicate run was started.

The completed evaluator reports zero missing expected liquid minutes, zero
actual bar minutes without tick coverage, and zero observed out-of-session tick
rows. Its producer-inventory validation passed without errors. These checks do
not establish that every possible market tick was delivered. Its full canonical
reference-session evidence includes 24 distinct counted-flag minutes; only the
16 inside the fixed expected-liquid bins enter this criterion. Whole-stream
producer tick/row flag counts must not be substituted for either minute count.

- Acceptance report: `build/gate1/source-clock-reference-acceptance-20260906-v1/report.json`,
  SHA-256 `9b0ca7ccda33d5c09f669733d48d67fa81f730561a3f22e49f5451ab81ec898f`.
- Final wrapper receipt: `build/gate1/source-clock-rebuild-run-20260906-v1/result.json`,
  SHA-256 `73bb3154cd496531a5f214d0fc912e5db6a52d85b9b7bc9eb8365e981bec7ae1`.
- Wrapper status: `COMPLETED_DIAGNOSTIC_NOT_TRAINING_RELEASE`.

Implemented the explicitly scoped source-clock interpretation and completed a new
full reference-month producer/acceptance sequence. **Completion is not an
acceptance pass or training release.** Raw archives, quality thresholds,
counting rules and trading settings remain unchanged.

The corrected selection exposed four omitted late-Friday reference intervals
(720 minutes), plus a November 1 lookahead interval (120 minutes). The fixed
demo-only supplement completed in 40.563 seconds and retrieved:

| Carrier-label start date | Primary ticks | Repeat |
|---|---:|---|
| 2024-10-04 | 21,263 | Identical |
| 2024-10-11 | 17,105 | Identical |
| 2024-10-18 | 28,138 | Identical |
| 2024-10-25 | 29,895 | Identical |
| 2024-11-01 | 17,756 | Identical |
| Total | 114,157 | Two separately retained responses each |

The account remained exact FBS-Demo, demo, USD 1,000 balance/equity, zero margin,
zero positions and zero pending orders. No account identifier was saved and no
trading API call was made. The dashboard observer was unavailable on port 8766;
the capture performed its own bounded direct-terminal checks instead.

The root artifact audit verified all 20 native/canonical files and compared all
eight fields across 228,314 rows including repeats. This establishes preserved
response agreement, not independence from the broker/terminal cache or proof
that every possible market tick exists. It does not clear the previous 17
intraday gaps or seven price-spike minutes.

## Verification

- Full suite: **1,725 tests passed**, warnings treated as errors, 88.12% coverage;
  core and non-core coverage tiers passed (96% and 87% displayed).
- Ruff formatting/lint, strict mypy over 123 files, Bandit and pip-audit passed.
- Gate-0 smoke reproduced with identical backtest/paper event hashes.
- Three-cell companion notebook executed successfully with no cell errors.
- Existing unrelated edits in `reference_definition_proposal.md` were preserved;
  its pre-existing trailing whitespace was not rewritten.

## Completed job and frozen inputs

The worker started at **2026-09-06 11:08:39 UTC**. It selected 30 acquisition-label
chunks, covering 23 target canonical sessions plus prehistory and lookahead.
Selection coverage concerns mapped request intervals, not observed tick completeness.

The background wrapper performed two clean rebuilds and, after verified
reproducibility, invoked the unchanged reference acceptance evaluator. It did not
create a human approval or start training/orders. Preserve the pinned source-clock
policy, calendar, counting definition, source evidence and pipeline code as this
run's provenance. Neither MT5 nor a keep-awake requirement is needed for this
completed job; no follow-up worker was launched by the wrapper.

- Live log: `build/gate1/source-clock-rebuild-run-20260906-v1/producer.log`.
- Overall result: `build/gate1/source-clock-rebuild-run-20260906-v1/result.json`.
- Producer: `build/gate1/source-clock-reference-rebuild-20260906-v1/`.
- Acceptance: `build/gate1/source-clock-reference-acceptance-20260906-v1/report.json`.
- Capture manifest SHA256: `ec3bfd57c0c10d0732c8a81a59ba43f859d86134f7aeaba6c18574caa2b35372`.
- Capture audit: `build/gate1/source-clock-supplement-audit-20260906-v1.json`,
  SHA256 `018de37022ff97c7249b4778de8ac9a3dea022306fe8e69bb0ac030b3f89e5bf`.
- Launch manifest SHA256: `ea7d16572cd319c92a8a12e1ff067f28ea3f7d9a2643c5337604d3735d5acf14`.
- Executed notebook: `build/gate1/source-clock-preflight-notebook-20260906-v1/source-clock-preflight.executed.ipynb`,
  SHA256 `d1bbbb36f37356c8797285d48b321484a4e61cbd29217e7165318e83250aac58`.

## Next milestone

The [bounded minute attribution](corrected_minute_attribution_20260906.md) now
reconciles all 16 expected-liquid minutes: 13 retrospective price, 2 gap and 1
spread minute, with no overlap in that subset. The exact CSV, hash-bound script,
stable-sequence comparison and successfully executed notebook are retained there.
The same note preserves the independent audit's 366/367 strict failure for a
matching producer-pinned file omitted from the outer launch list. No original
launch, failed audit, data or threshold was rewritten.

### Prior-evidence applicability

Keep the [September 5 approval receipt](gate1_confirmation_20260905.md) attached
to its original no-epoch-shift QA baseline. The later, explicitly scoped clock
interpretation is ADR-0014, with the September 6 acceptance receipt below. These
are distinct interpretations and decision contexts, not a silent rebinding of
the earlier result.

The existing random-30-day report remains at
`build/gate1/reference-repair-verification-20260905-v2/random-30day/report.json`,
SHA-256 `a0c7e4047d9866b4fef76a072e4512452b4b2abd27f21f31bc85ae5b8ece52b3`.
Its pinned `bars.py`, `normalize.py`, `quality.py`, `storage.py` and
`core/time_rules.py` hashes match the new producer. This supports retaining its
baseline engineering evidence where inputs and code paths are unaffected. Its
October rows do not become corrected-clock evidence merely through that reuse;
the producer/orchestration changes and explicit clock path have their separate
tests and corrected two-pass run. The new 30 acquisition-label chunks are not a
new random 30-day sample.

The existing five venue-matched bar samples in
[the independent review](gate1_independent_review.md) are August 17–21, 2026,
outside this clock policy's bounded 2024 scope. Preserve their existing source
worksheet, comparisons and actual approval provenance; do not claim that those
samples verify October's clock correction or invent new human calculations.
The final package must explicitly identify each reused artifact and its limited
applicability. Repeating every prior test is not automatic, nor does reuse waive
validation of an affected code path.

### Completed-result and decision bindings

Isaac's direct acceptance and his subsequent report of Delsa's acceptance are
recorded in [the September 6 receipt](source_clock_acceptance_receipt_20260906.md).
The general human acceptance question is resolved. The technical run is complete,
with a numerical failure; investigation of the 16 counted minutes and final
evidence binding remain separate unfinished work. No repeat general approval is
requested by this outcome.

The frozen diagnostic uses the September 5 `known_at` value and no final approval
binding. Leave those inputs unchanged. Any subsequent evidence-bound evaluation
must use a new output directory and a genuine knowledge time compatible with its
approval evidence; receipt time is not a substitute for an unknown decision time.
Approval metadata alone does not require rebuilding unchanged corpus bytes.

Attribution is complete; the next quality task is reviewing the newly relevant
contexts of those 16 minutes, retaining original and corrected results. Do not
assume they are the same minutes as the old 24-minute failure: the clock and
session assignment changed. Distinguish source observations, detector behavior
and genuine defects before proposing a repair. No favorable exclusions, price
edits or threshold relaxation are inferred from human acceptance. A subsequent
numerical pass would still need truthful final evidence binding.

The genuine fitting/calibration consumer and phase-appropriate P2 backtest proof
are still separate implementation work. Current runnable demos are synthetic
engineering evidence. Broker order execution, OMS/reconciliation and production
risk controls are not implemented by this patch. Training and execution remain
disabled. See ADR-0014 for scope and retained limitations.
