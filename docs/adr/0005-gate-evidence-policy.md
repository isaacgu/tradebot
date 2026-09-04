# ADR-0005: Gate evidence categories and deferral policy

Status: accepted — SPEC v1.0, SHA-256 `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`.
Policy text is in force from Gate 0; the automated checker is deferred to P1 (see Consequences).

## Context

SPEC §10.6 as supplied required every gate evidence file to link "CI runs, report JSON hashes,
dashboard screenshots and the reviewer's sign-off". Gate 0 has no dashboard — Grafana is allocated
to P1/P4 — so the requirement is unsatisfiable there. The Phase-0 architect correctly refused to
mark it "N/A" and escalated instead.

The real risk is not Gate 0. It is a future agent at Gate 3 or Gate 5 citing a Gate-0 precedent to
drop evidence that *does* exist.

Alternatives considered:

1. Mark the screenshot N/A at Gate 0. Rejected: creates exactly that precedent.
2. Build a Grafana dashboard at Gate 0 to satisfy the letter of the rule. Rejected: speculative work
   (§12.1 #2), SRE-role work inside the Architect's phase (§14), contradicts the deliberate "no
   network listener in P0", and would photograph three counters from a millisecond-lived process.
   That is evidence theatre.
3. A fixed row-per-category table with a constrained three-value status enum. Chosen.

## Decision

The §10.6 model: five fixed categories, each carrying exactly one of `PROVIDED`,
`DEFERRED-BY-PHASE`, `FAILED`. Four properties make it resistant to abuse, and all four are needed:

1. **Forward citation.** `DEFERRED-BY-PHASE` is legal only where §13 allocates the deliverable to a
   strictly later phase, and the row must cite that phase *and* the gate criterion that makes it
   due. If §13 allocates it nowhere, the row is `FAILED` until the Principal signs a SPEC amendment
   — which prices an invented deferral at a signed spec change.
2. **No-downgrade.** A category is never deferred wholesale if any machine-readable artifact of that
   class exists; defer only the missing *format*, and attach the strongest artifact that does exist.
3. **Carry-forward.** Every deferred row is copied verbatim into the next gate's file and must be
   discharged by the gate it names.
4. **SPEC-hash pin.** Every gate evidence file records the SHA-256 of the frozen SPEC it was judged
   against. Without this the citation mechanism is circular: §13 is editable by the same agent doing
   the deferring, who could edit first and cite second.

Categories 1, 2, 4 and 5 (CI on a committed SHA; artifact hashes; independent reviewer sign-off;
**Principal sign-off**) can never be deferred. §13's preamble is unambiguous that agents may not
self-certify a gate, so any "exhaustive" category list omitting the Principal's signature would be
materially worse than the vague line it replaces.

**Gate-0 observability = the Prometheus text exposition** emitted by `make demo`, with its digest
recorded per mode. It is machine-readable, hashable against a re-run, greppable, and can fail CI — a
PNG can do none of those. It also closes a real gap: the demo constructed a `CoreMetrics` instance
and discarded it, so the `metrics.enabled` flag bought no artifact at all. State the claim narrowly:
none of the three counters appears in §9.4's metric list, so this is **not** a preview of any
dashboard. It proves the `CoreMetrics → registry → exposition` path is live and deterministic, which
is a *precondition* for §9.4.

**Canonicalisation is constructive, never subtractive.** The hashed record is built from
`registry.collect()` as `(family name, type, sorted label pairs, value)` for every family including
empty ones, skipping only samples whose name ends `_created` (the client stamps `time.time()` there,
which would break byte-stability). A "drop all `#` lines" filter was rejected: the exposition emits
`# HELP`/`# TYPE` for every family including zero-sample families, so a line filter would erase all
trace of the rejection and dispatch-failure counters — the only two safety-relevant families — and a
subtractive drop-list in the hashed-evidence path is precisely the surface a future engineer widens
to make CI green (§12.1 #6).

The lexical rule "ban the token N/A" was **rejected**: trivially evaded ("none", "—", an empty
cell) and it false-positives on the architect's own correct sentence "No screenshot is silently
marked N/A". Constrain the Status *cell* to the enum; leave prose alone.

## Consequences

Gate 0's observability row is `PROVIDED`, not deferred; only the Grafana screenshot *format* is
deferred, owned by §9.4, due at Gate 1 for the data-quality dashboard under §4.6's "Quality
dashboard live" and in full at Gate 4.

The automated checker (`scripts/check_evidence.py`) is deferred to QA at Gate 1 as this policy's own
first carry-forward obligation, with §13's P1 row amended to name it. §14 assigns §10 and the gate
evidence templates to QA, and the same role argument that rejects Grafana-at-Gate-0 applies
symmetrically. Gate 0 has one evidence file and a human signature; a Markdown parser adds nothing
there.

A rule of the shape "a PR must not modify the checker and an evidence file together" is **inert as a
SPEC rule** — CI triggers on direct pushes too, and a direct push bypasses any PR-scoped rule. It is
recorded in §12.3 as a required repository setting instead, so the document does not read enforced
while being unenforced.

## Verification

Appendix G carries the template. Gate 0's evidence file is the first instance. The checker's absence
is itself a tracked `DEFERRED-BY-PHASE` row.
