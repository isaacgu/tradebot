# Gate 1 — definition and review approval confirmation

Recorded on 2026-09-05; receipt preparation began at 14:07:59 UTC. This is the
agent's recording time, not an inferred time of either human's review or signature.
Source: Principal Isaac Gumbi's direct message in local task
`01a06dc2-f007-7c31-9ecf-2f1989530173` (Review bot progress and GUI).

## Exact statement and context

The preceding response listed: (1) completed rebuilds and a passing reference-month
quality evaluation, (2) agreement on the revised QA calendar/counting rules, and
(3) Delsa's five documented bar checks and both final evidence-bound sign-offs.
It explicitly asked whether Isaac and Delsa approved the linked October EURUSD
definition: 13:00–16:30 London, excluding weekends and October 3/14, with its
documented counting rules, as a project QA rule rather than an FBS guarantee.

Isaac replied:

> 2 and 3.Delsa has give approval for the bar checks and both final bound siign offs same as i isaac gumbi ,i assume 1 is currently in progress as we speak

## Recorded scope and provenance

- **Definition approval recorded:** Isaac approves the exact definition asked about;
  Delsa Mashiki's concurrence is reported by Isaac in the same reply. No definition
  change or additional exclusion is inferred.
- **Five-bar approval reported:** Isaac reports Delsa's approval for the existing
  five selected bar checks in the [independent review](gate1_independent_review.md).
  This message is group-level approval evidence. It supplies no new per-bar values,
  calculation notes, discrepancy observations, personal review times or separately
  signed worksheet; none is invented or copied from automated results.
- **Sign-off approval statements received:** Isaac directly confirms his approval
  and reports Delsa's approval of the final bound sign-offs. Preserve this statement
  as received, without representing it as a separately authenticated Delsa signature.
- **Final artifact binding remains pending:** no completed new quality result or
  final evidence-manifest hash existed when the statement was recorded. These
  statements do not establish that that future result passes, that its bytes were
  inspected, or that a new final package was already bound to either decision.

Do not ask either human to repeat the same general approval merely because the old
templates have pending fields. If later review is necessary, identify the exact new
result, missing observation or changed artifact requiring it. Preserve the earlier
user-entered independent decision under Isaac's name; do not relabel it as Delsa's.

## Exact approved definition material

The approval refers to the materials linked immediately before the reply. All
following SHA-256 values were verified against the local files when recorded.

| Material | Path | SHA-256 |
|---|---|---|
| Human-readable definition | `docs/reports/reference_definition_proposal.md` | `63c10a75e3e0d20e9a009a191ce559ed92446e89e707faa49431920978d38332` |
| V4 proposal manifest | `build/gate1/reference-definition-20260905-v4/proposal.json` | `e2f22ccae7697985e5cf585d8f70c7c57819005af3a0c87fd4a051410df03c46` |
| V4 calendar draft | `build/gate1/reference-definition-20260905-v4/calendar-draft.json` | `1a57522fa4356ec060f0014e00d6b1135f1fdb7158e23e70f9328661c7f46385` |
| V4 counting-policy draft | `build/gate1/reference-definition-20260905-v4/policy-draft.json` | `a7b7fd6a04e71972413e1af3e625892684dd8f9cc460176963cb31c39df78c68` |

The preserved draft files and proposal wording are historical preparation artifacts;
this new receipt records the subsequent human decision without overwriting their
bytes or backdating their knowledge. The data-validation owner may prepare a new,
separately traceable reviewed-definition artifact with identical scope/counting
semantics and actual approval provenance, subject to the evaluator's contract.
This is not authority to modify the running job's frozen inputs or to silently
reinterpret the producer's original calendar identity.

Original source epochs stay unchanged. The unchanged-UTC QA baseline and disclosed
timestamp limitations are accepted as part of the definition; historical broker
timestamp accuracy is not thereby independently established. Acceptance remains
strictly `numerator * 1000 < denominator`. No outcome-based policy tuning, source
price edits, backdating or threshold waiver is authorized.

## Technical state at confirmation

At 14:09:10 UTC / 16:09:10 SAST, read-only checks found the exact launched
verification-v2 launcher and worker alive. The reference-month mechanical rebuild
was still the first job; no random-30-day start or final `result.json` existed.
The acceptance calculation was **not yet running**. The launch's original draft
label records its start-time state, not the subsequent approval received here.

Source candidate `5b56316a82749a8e60a9fea3596871c69325d400` has successful CI;
the docs-only head `ad58383f26202dcd1cf651c1a96f3423439e28b2` also has successful
CI. See [delivery evidence](guard_data_publication_20260905.md). These are known
implementation/publication identities, not a new final accepted-data package.

Remaining work: finish and verify both mechanical jobs, prepare reviewed inputs
with valid producer linkage, run the actual acceptance evaluation, then assemble
the final package and identify any specific outstanding human review evidence.
**Gate 1 remains NOT APPROVED pending technical acceptance and final binding.**
No training, financial acceptance, paper/live execution or later gate is authorized.
