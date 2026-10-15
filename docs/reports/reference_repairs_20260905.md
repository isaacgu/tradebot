# Reference-month repairs and verification handoff

Prepared September 5, 2026. **Implementation repaired; new real-data verification
is required. Reference acceptance and strategy training remain on hold.**
This is not a replacement for the Gate-1 evidence register or a human decision.

## Repairs delivered

- Quality calendar queries use the canonical New York **session close date**,
  including complete coverage collection before returning an unknown-gap verdict.
  No timestamp offset, threshold or quote correction was introduced.
- The producer has a separate complete-reference-month selection mode. October
  2024 selects all 23 weekday close-labelled targets, including empty sessions,
  plus one prehistory and one lookahead session. Context is identified separately;
  the original random 30-day criterion remains a different test. Lookahead must
  contain at least the loaded `price_reversion_ticks` count; sparse context is
  rejected without changing that threshold. The selected October lookahead has
  264,936 validated source rows against the configured five, with 237,953
  prehistory rows. The normal importer checks the retained counts and hashes.
- Calendar bytes, knowledge time and source-qualified instrument identity are
  bound to corpus identity. Two rebuilds must preserve raw bytes, implementation,
  calendar and complete output manifests. Corpus implementation version is 5.
- The evaluator discloses out-of-canonical-session evidence separately instead of
  losing it. Incomplete calendar coverage leaves judged counts null, not an
  apparent zero-defect result. Diagnostic counts and causal/retrospective evidence
  are distinct. Future-dated decision/knowledge claims are rejected. Padded human
  names cannot evade the case-insensitive distinct-name check; this still does
  not authenticate human identities.
- An immutable preparation tool creates a concrete calendar/counting proposal
  from definitions and captured sources, without reading prices or calculating
  the acceptance rate. Its output remains `DRAFT_REQUIRES_HUMAN_REVIEW`.

Independent agent reviews found and closed calendar-key, contextual-session,
future-approval, input-reread, output-symlink and timezone-provider issues. Those
engineering reviews are not the independent **human** Gate-1 review.

## Definition decision still required

Read [the proposal](reference_definition_proposal.md). The exact v4 packet is
[proposal.json](../../build/gate1/reference-definition-20260905-v4/proposal.json),
with [date-by-date entries](../../build/gate1/reference-definition-20260905-v4/calendar-review.csv).
This recaptures the same five primary sources using the repaired redirect boundary;
earlier packets remain preserved. Definitions and the 4,410-minute expectation are
unchanged; actual preparation availability and source hashes are freshly recorded.

| Artifact | SHA-256 |
| --- | --- |
| Draft calendar | `1a57522fa4356ec060f0014e00d6b1135f1fdb7158e23e70f9328661c7f46385` |
| Draft counting policy | `a7b7fd6a04e71972413e1af3e625892684dd8f9cc460176963cb31c39df78c68` |
| Calendar CSV | `3a90698388739321ed33774ec0c7c10e1a0b3acc3beea51913dd7c16c4cebb9a` |

The proposal is a project-owned 13:00–16:30 Europe/London QA window, excluding
weekends, October 3 and October 14. It implies 4,410 expected minutes, **not a
measured clean rate**. It does not claim those are FBS's complete trading hours.
Sources were retrieved in 2026; they are not information available to a 2024
strategy. Earlier diagnostics were already observed. Definitions must not be
chosen or changed to make a failing result pass.

Delsa's dated review and Isaac's subsequent definition decision must bind the
exact files and limitations. Approval statements already received are preserved;
they are not silently transferred to this new proposal. Approval of the
definitions is separate from final Gate 1. No approved policy/calendar, signed
receipt or production training release has been fabricated.

## Verified locally

The combined changed-component suite passed **155 tests** with warnings as errors
and without replacing prior coverage artifacts. Log:
[reference-repair-targeted-20260905-v3.log](../../build/preuse/reference-repair-targeted-20260905-v3.log),
SHA-256 `8e96161aec6c4ed35db4920b415e35632d6e2cdf292f31a6d4062a8a801b3ff2`.
Ruff format/check passed for all 13 changed Python files; mypy passed for the six
changed implementation files. Full combined checks and committed-candidate CI
must also cover the separately integrated research-consumption guard.

### Source-download security fix

Outcome: **fixed** within the optional primary-source capture boundary. The old
`capture_source` checked the final URL only after default urllib had followed a
redirect. A compromised allowed origin could therefore make an off-origin request
before rejection, or hide a forbidden hop by returning to the allowed origin.
No actual source compromise is claimed.

`scripts/prepare_reference_definition.py` now uses one origin-bound redirect
handler and a shared validator, enforcing HTTPS, original host, default HTTPS
port and no URL credentials **before each next request**. Final response identity
is checked before reading its body. This is the narrow shared urllib redirect
hook, preserving direct downloads and same-origin relative HTTPS redirects, the
timeout, bounded final capture and `UNAVAILABLE` error behavior. It does not claim
OS isolation or protection from compromised DNS/proxy/host trust.

Regression tests are in
`tests/unit/scripts/test_prepare_reference_definition.py`. Ordered verification:

1. Syntax/type/style: mypy and Ruff format/check passed for those two files;
   Bandit passed for the capture script. `git diff --check` passed.
2. Original trigger and alternatives: `pytest -W error --no-cov` with real urllib
   redirect processing and fake HTTP/HTTPS/FTP transport reproduced **26 failures**
   before the source fix. The same suite now rejects forbidden destinations before
   a second request, including HTTP/private targets, off-host/protocol-relative
   hops, alternate ports, credentials and return-to-origin chains.
3. Legitimate control and package checks: the complete preparation suite passed
   **42 tests**, including direct HTML/PDF and same-origin relative redirects.
   The combined data suite passed 155 tests. A fresh independent reviewer checked
   161 transport-free cases under Python 3.12.14, with socket creation hard-failed,
   and found no concrete surviving bypass or compatibility regression.

Reproduction log: `build/preuse/source-redirect-red-20260905.log`, SHA-256
`5b876e3b74138f8eb3fc8575bb02771ef8a1165467843085d672fc1341b1758d`.
Passing log: `build/preuse/source-redirect-green-20260905.log`, SHA-256
`5b517122a50a9f53224e8d9b50fd5f8a8a2317446a18c2de6520698987d4b021`.
No test contacted a private/off-scope endpoint. The focused security-fix skill
required a pre-patch investigation, trigger/control tests and one fresh candidate
review; those checks determined the shared pre-request enforcement point.

## Append-only offline verification

The first durable sequence started **2026-09-05 11:38:51 UTC** and was deliberately
stopped at **11:58:17 UTC** before changing the evaluator's padded-name check.
Only the verified corpus child was terminated. Its parent recorded return code
`-15`, no completed producer report, unchanged source at termination and
`NOT_SATISFIED`; the second job did not start. The original script, patch, imports
and partial outputs remain at
`build/gate1/reference-repair-verification-20260905-v1/`. No raw files were deleted.

The v2 sequence uses already saved source files and does not call MT5:

1. Complete October EURUSD: import 25 explicitly labelled inputs and rebuild
   twice using the exact draft calendar, for engineering evidence only.
2. Original seeded 30-day EURUSD sample: rebuild twice under the repaired
   implementation, with the same seed `20260904`. This does not replace the
   separate complete-month acceptance calculation.

V2 output root: `build/gate1/reference-repair-verification-20260905-v2/`.
Its `launch.json` pins exact commands and frozen dependency hashes once started;
no launch file means it has not started. The preserved
[v1 launch manifest](../../build/gate1/reference-repair-verification-20260905-v1/launch.json)
has SHA-256
`a4d181587d7165a15115eae40b263363daf08082f6eb9a563faf00f4838cd3a4`.
Consult v2's `events.jsonl` and the two job logs for actual progress. Each successful
producer writes `report.json` and its checksum. The runner writes its final
`result.json` only after finishing or detecting an unsatisfied job. An absent final
report is not completion. A vanished process without a final result is interrupted
evidence, not a pass. Source changes or a failed job stop the sequence.

The earlier 30-day result remains preserved historical evidence; because the
derivation code changed, its old implementation hashes are not current-code
validation. New output directories preserve both old results and raw acquisition.
No final reference acceptance rate is being computed before definition approval.

## Route to the first real training run

The unattended jobs do **not** auto-start training when they finish. The remaining
sequence is explicit:

1. Review/freeze definitions, finish immutable rebuild evidence, then run the
   actual reference evaluator and retain its real verdict under `<0.1%` unchanged.
2. Bind current code/CI, dataset, calendar, policy and reports; obtain five actual
   human bar checks, Delsa's independent decision and Isaac's decision on the same
   final package. This closes Gate 1 only if all its criteria are satisfied.
3. Complete and accept the P2 backtester/Gate-2 work. The existing decision engine
   does not implement strategy fitting or constitute accepted economic research.
4. Complete strategy-specific history/stress/tick-fidelity evidence and genuine
   purpose-scoped admission. Current `QA_ONLY`/`QUARANTINED` partitions and failed
   Brexit evidence must not be promoted by a status edit.
5. Implement and review the first strategy-fit experiment, including time splits,
   leakage controls and costs, then run it through the guarded approved snapshot.

The USD 1,000 demo profile is unchanged; `execution_enabled` remains false.
No source-use email needs to be requested again: Isaac's reported personal-use
permission is retained. It is separate from historical-session evidence and
technical readiness. Data quality skills informed the explicit grain, denominator,
provenance and uncertainty checks; they did not supply approval or lower a gate.
