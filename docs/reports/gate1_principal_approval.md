# Gate 1 — Principal decisions and final approval

Update 2026-09-05: Isaac's approval entries are retained below; see the
[approval receipt](gate1_approval_receipt_20260905.md) for the statements received
and remaining bindings. They do not establish that pending technical checks passed.
The original template text and user-entered decision below are preserved.

**UNSIGNED — NOT READY FOR APPROVAL.** Prepared 2026-09-05.
Expected Principal: Isaac Gumbi. Naming the role owner is not a signature.
No Gate-1 approval is recorded, and authorization to continue work is not approval.

Read the [approval guide](gate1_approval_guide.md), [main evidence pack](gate1_evidence.md)
and [independent human review](gate1_independent_review.md).

## Pre-evaluation decisions — not final Gate 1 approval

These unresolved definitions must be reviewed and recorded before the final reference-month
quality evaluation. They are not permission to change data or relax the frozen threshold.

| Decision needed | Required record | Status |
|---|---|---|
| Reference scope and dated liquidity expectations | Instruments, session-close month, FULL/PARTIAL/CLOSED dates, exact liquid intervals, timestamp basis, source citations and coverage limitations | NOT APPROVED |
| Counted-flag and denominator policy | Every flag class including imputed receipt provenance, warmup, unknown gaps and retrospective price flags; overlap treatment, expected missing bars, interval membership and denominator | NOT APPROVED |
| Historical timestamp discrepancy disposition | Evidence-backed acceptance, correction or unresolved status; no best-fit offset chosen from the desired result | NOT RESOLVED |

The FBS inquiry can proceed once contact identity is supplied, but engineering need not
wait for its reply. A provisional schedule must not be relabelled authoritative. If a
different evidence route is proposed, document its sources and limitations for review.
Any actual change to a gate criterion requires an explicit Principal-approved ADR/SPEC
amendment with old/new values and fresh evidence; this form is not that amendment.

Decision records / approved policy and calendar references: **NOT SUPPLIED**.

## Final candidate to be approved — all bindings required

| Binding | Value |
|---|---|
| Gate and authorized scope | Gate 1 bounded data-pipeline acceptance only |
| Full candidate commit SHA and PR URL | PENDING |
| Frozen SPEC | v1.0; SHA-256 `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37` |
| Unsigned reviewed evidence snapshot/manifest path and SHA-256 | PENDING — must be the same package reviewed independently |
| Immutable CI run URL, tested SHA and both job results | PENDING — `quality` and `secrets` must pass |
| Accepted dataset, calendar, counted-flag policy and acceptance-report hashes | PENDING |
| Independent reviewer name and decision date | NOT SUPPLIED |
| Signed independent-review document path and SHA-256 | PENDING |

## Principal review checklist

- [ ] I reviewed the exact candidate/evidence package and the independent human's decision.
- [ ] All five Gate-1 acceptance tests and all inherited obligations are satisfied.
- [ ] Current committed-SHA CI passed; local tests have not been substituted for it.
- [ ] The five bar checks are human-verified and the approved reference-month quality
  report satisfies the unchanged strict `<0.1%` criterion.
- [ ] No material finding remains unresolved or is disguised as an approved exception.
- [ ] I understand that the failed Brexit source evidence remains a separate P3
  stress-history limitation, and that Gate 1 does not approve every downloaded tick.
- [ ] I understand that this decision does not authorize live orders, financial
  strategy acceptance, or bypassing any later phase gate.

## Final Principal decision

Complete personally only when the candidate and required evidence are ready. Record
exactly one explicit decision: **APPROVE**. A HOLD or REJECT
keeps Gate 1 unapproved. Do not sign APPROVE while required fields/checks are missing.

- Decision: **APPROVE**
- Full human name: **Isaac Gumbi**
- Decision date (YYYY-MM-DD) and UTC timestamp: **2026-09-05 10:19**
- Candidate SHA, evidence-manifest hash and reviewer-document hash confirmed: **NOT SUPPLIED**
- Explicit Gate-1-only decision statement and rationale: **Approved**
- Authentic decision source (signed document, approval URL or retained direct message): **NOT SUPPLIED**

After an authentic approval, record this document's final SHA-256 and mirror the actual
name/date in category 5 and the main pack's sign-off section. Attach both signed document
hashes through category 2 for machine verification. Neither agents nor the checker may
invent this decision. Candidate/evidence changes require renewed review, not copied signatures.
