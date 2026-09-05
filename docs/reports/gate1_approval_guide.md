# Gate 1 — approval guide and remaining work

Prepared 2026-09-05. **NOT READY FOR GATE CLOSURE. Approval statements received;
final evidence-bound sign-offs remain incomplete.**
This is the route to closing Gate 1, not a declaration that it has passed.

Isaac's approval entries and his report of Delsa Mashiki's independent approval are
preserved in the [approval receipt](gate1_approval_receipt_20260905.md). No personal
check, missing evidence binding or technical acceptance is inferred from those statements.

## Where the humans sign

| Role | Document | Required action |
|---|---|---|
| Independent human reviewer | [Gate 1 independent review](gate1_independent_review.md#final-reviewer-decision) | Inspect the final candidate and evidence, complete the five bar checks, record findings and an explicit decision |
| Principal — Isaac Gumbi | [Gate 1 Principal approval](gate1_principal_approval.md#final-principal-decision) | Review the same candidate, evidence and independent decision; explicitly grant or withhold Gate 1 approval |

The reviewer and Principal must be different humans. An agent's review, a request to
continue development, Gate-0 approval, or an unsigned form is not either signature.
The authoritative category/status register remains [gate1_evidence.md](gate1_evidence.md).

## Remaining work before an APPROVED decision

| Item | Verified position | Closure needed |
|---|---|---|
| Thirty-day immutable rebuild | PASSED mechanically: 6,646,477 ticks, 41,701 M1 bars, byte-identical rebuilds | Bind the preserved results to the reviewed implementation and final candidate |
| Five venue-matched bars | Five automated Bid comparisons and five production Mid comparisons match | A human must inspect and hand-verify all five; record outcomes in the reviewer form |
| DST and quality tests | Latest local suite: 962 warnings-as-errors tests passed, 87.20% coverage; static, dependency audit and coverage tiers passed; see [pre-use verification](preuse_preparation_20260905.md#final-local-verification) | Successful `quality` and `secrets` CI jobs on the final committed candidate; preserve relevant logs/artifacts |
| Reference-month liquid-hours quality | INDETERMINATE; October diagnostic is provisional, not the required clean-bar quality rate | Reviewed dated liquidity expectations, an explicit prospectively agreed flag-counting policy, and an actual P1 reference-month numerator/denominator proving the unchanged `<0.1%` criterion |
| Point-in-time calendar query | Narrow query-at-T test mechanically passes | Attach its identity and current CI; do not mistake it for a historical FX liquidity calendar |
| Observability and inherited obligations | PROVIDED | Retain the hashed exposition, screenshots and checker evidence |
| Independent and Principal decisions | Approval statements received; final bindings incomplete | Delsa completes the independent record, then Isaac completes the Principal record against the same final review package |

The [reference-month evaluator](../../scripts/evaluate_reference_month.py) is now
implemented and locally tested. It requires exact clean-file inventory tied to an
independently pinned producer report, dated liquidity coverage and a reviewed counting
policy; missing evidence remains `INDETERMINATE`. The existing 30-day corpus is still
a sampled multi-year dataset, not a complete reference month. The earlier provisional
diagnostic measures advertised-session hypotheses, not the P1 clean-bar flagged rate.
Changing a status or adding signatures cannot substitute for actual acceptance inputs.

The counted-flag definition needs a recorded decision before that evaluation: historical
`TS_RECV_IMPUTED` provenance, warmup, unknown coverage, actual causal defects and
retrospective price annotations are different classes. The final policy must state
each class's treatment, overlap counting, missing expected bars, instrument/month
scope, interval membership and denominator. Do not exclude a class or pick an offset
because doing so makes the observed result pass. The `<0.1%` threshold remains frozen.
Any actual criterion change requires the Principal-reserved ADR/SPEC process and
new evidence, not a waiver in an approval form.

## What the completed acquisition does and does not establish

The sealed continuity candidate contains 190 checkpoints and 42,796,598 primary ticks.
Its report checksum matches its sidecar. Retrieval is complete, but its structural
status is FAILED: 379,467 primary crossed quotes occur in the Brexit window. The
corresponding 758,934 count covers two repeated fetches, not twice as many distinct
primary observations. Preserve the report and affected raw evidence; do not edit prices.

SPEC §13 already places deep/stress-history readiness at per-strategy P3 entry.
That broader source-viability failure does not automatically fail the separate
October reference-month evaluation. The completed random 30-day rebuild spans multiple
years and is a different artifact. Equally, a bounded Gate-1 pass would not approve the
Brexit data or clear a strategy's mandatory stress-history requirements. October's
own calendar, timestamp and clean-quality acceptance remains unresolved.

The sample's 34 retrospective price-outlier annotations also remain disclosed and
unadjudicated. The successful rebuild proves reproducibility, not absence of defects.

## Evidence to open first

- [Main evidence pack](gate1_evidence.md): exact criteria, artifact hashes and caveats.
- [Completed rebuild](../../build/gate1/30day-stable-b102ecdd/report.json).
- [Provisional October diagnostic](../../build/gate1/reference-diagnostics-v1/report.json).
- [Recorded price-outlier cases](../../build/gate1/price-outlier-drilldown-20260905.json).
- [Five-bar source worksheet](../../build/gate1/reference/comparison-worksheet.csv) and
  [production Mid comparison](../../build/gate1/reference/production-bar-check-v1/comparison.csv).
- [Latest local test verification](../../build/p3-root-verification-20260905/result.json).
- [Sealed acquisition candidate](fbs-tick-continuity-v1-candidate.json) and
  [checksum sidecar](fbs-tick-continuity-v1-candidate.json.sha256).

Build artifacts are local and Git-ignored. Give the reviewer read access to them or
an approved, hash-verified evidence bundle; repository links alone do not deliver them
to another machine. Do not upload raw broker files or personal data by implication.

## Finalization sequence and signature binding

1. Resolve the pre-evaluation policy/calendar decisions in the Principal form. These
   decisions are not Gate-1 approval. Implement/run the reference-month acceptance
   evaluation; keep failed, unavailable and earlier provisional evidence visible.
2. Review the exact commit scope, including the existing dirty-worktree changes.
   Commit the agreed candidate, submit a normal PR, and obtain both required CI jobs.
   Do not push directly to protected `master` or treat old Gate-0 CI as current evidence.
   No commit, push or PR was made by this approval-document preparation.
3. Freeze an unsigned review package: full candidate commit, SPEC hash, immutable
   evidence manifest/snapshot, dataset, calendar and counted-flag policy hashes, and
   CI URL/SHA/job results. Both people must review and cite that same package. The
   current dirty checkout HEAD is not a final candidate identity.
4. The independent reviewer completes their form. The Principal then reviews that
   signed form and records its hash in the Principal form. A HOLD/REJECT decision
   remains a blocker; missing inputs do not become approved exceptions.
5. Retain each actual human decision and provenance. Mirror the real name/date in
   the matching main-pack category row and `## Sign-off` lines. The existing checker
   expects `Independent reviewer: <name>  date: YYYY-MM-DD` and
   `Principal: <name>  date: YYYY-MM-DD`. It does not read the linked forms or
   authenticate the people. Attach signed-document path/SHA references in category 2
   as well, so current artifact checking covers their bytes.
6. Re-run the unchanged checker. It must continue to fail until all required evidence
   is present. A successful machine check is still not a human approval. Preserve the
   unsigned snapshot separately so adding signatures cannot create circular hashes.
   Material candidate, policy or evidence changes require renewed CI/review as applicable
   and new signatures; never transplant an old signature to a different candidate.

```bash
uv run --no-sync python scripts/check_evidence.py docs/reports/gate1_evidence.md
```

The current expected result is nonzero for categories 1, 2, 4 and 5. This is intentional.
Gate 1 approval would authorize the next gated development phase only, not strategy
financial acceptance, paper/live trading or bypassing later gates.
