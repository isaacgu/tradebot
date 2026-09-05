# ADR index and number registry

Numbers are allocated here and **only** here. Do not hard-code an ADR number in `docs/SPEC.md` or in
another ADR; cite this registry instead. An agent that needs a number takes the next free one and
records it here in the same change.

| # | Title | Phase | Status |
|---|---|---|---|
| 0001 | Platform and Phase-0 stack | P0 | accepted |
| 0002 | Event types, availability, and deterministic ordering | P0 | accepted |
| 0003 | Clock, timezone, and FX boundary rules | P0 | accepted |
| 0004 | Errata adopted at specification freeze | P0 | accepted |
| 0005 | Gate evidence categories and deferral policy | P0 (doc); enforcement deferred to P1 | accepted |
| 0006 | Feed adapter timestamp, availability and skew boundary | P1 | accepted — partially implemented; see its Implementation status |
| 0007 | Data source selection | P1 | accepted in part; continuity and calendar evidence pending |
| 0008 | Local acquisition observability | P1 | accepted |
| 0009 | Immutable corpus, quality policy and point-in-time calendars | P1 | in implementation |
| 0010 | Causal decision replay and observable research inputs | Core engineering preparation | implemented; no phase-gate acceptance |
| 0011 | Engineering research controls | Core engineering preparation | implemented; no financial evaluation or phase-gate acceptance |
| 0012 | Offline simulated execution and exact cost accounting | P2 engineering preparation | implemented as a synthetic-only increment; no phase-gate acceptance |
| 0013 | Purpose-scoped research releases and guarded consumption | Data/research engineering preparation | implemented; no production release, trust pin or training approval activated |

**Next free number: 0014.**

Reserved topics that draw a number when written, at the phase shown:

| Topic | Phase | Notes |
|---|---|---|
| Broker selection | P4 | SPEC §8.6 criteria. Formerly hard-coded as "ADR-0005" in §8.6; that reference now points here. |
| Host time-sync policy | P4 | SPEC §3.4(b): the 250 ms alert and the unsynchronised-clock gate. Deployment concern, out of process. |
| Execution topology | P4 | Where the broker client runs, if the chosen broker's client is not installable on the §9.2 runtime. |
| Storage choice | P1 | SPEC §12.3 requires an ADR for it. |
| Fill model defaults | P2 | SPEC §12.3. |
| Vol-target level | P3 | SPEC §12.3; a Principal-reserved threshold under §0 rule 9. |

## Status vocabulary

- `not started` — allocated, no content beyond scope.
- `proposed` — written, not yet approved.
- `accepted` — approved and in force.
- `superseded by NNNN` — replaced; the ADR stays in place, never deleted.

An ADR that changes a **Principal-reserved threshold** (SPEC §0 rule 9: gate criteria, risk limits,
breaker levels or actions, live kill criteria) requires the Principal's written sign-off recorded in
the ADR itself, and must state the exact old and new values, the rationale, the evidence, and every
affected gate.
