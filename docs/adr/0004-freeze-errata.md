# ADR-0004: Errata adopted at specification freeze

Status: accepted — SPEC v1.0, 2026-09-04.

- Supplied draft: `docs/SPEC-supplied-2026-09-03.md`, SHA-256 `2335e37dff7e3e0e7f7b88cf3974d9af5d953c404a32d95703bae55bed7e1fbc`
- Frozen v1.0: `docs/SPEC.md`, SHA-256 `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`

## Context

SPEC §0 requires the document to be frozen at v1.0 before Phase 0 begins, and the review of the
Phase-0 candidate found defects that sit directly on gate criteria. Freezing a document with known
contradictions writes those contradictions into the single source of truth; endlessly editing means
Phase 1 never starts.

Alternatives considered:

1. Freeze as-is and correct in a v1.1. Rejected: three defects make gates literally unpassable as
   written, so v1.1 would be needed before any gate could be signed.
2. Return the draft for open-ended redrafting. Rejected: the defects are enumerable and the rest of
   the document is sound.
3. Freeze at v1.0 with a bounded, enumerated errata list, preserving the supplied draft byte-for-byte
   for provenance. Chosen.

## Decision

Freeze at v1.0 with the errata below. The supplied draft is preserved unmodified so the diff between
what was handed over and what was frozen stays auditable; `pyproject.toml` keeps both files in
ruff's `extend-exclude`, which is load-bearing because ruff 0.16 formats fenced Python inside
Markdown and would otherwise rewrite the §3.6 sketch and change the provenance hash.

### Class A — impossible to satisfy as written

| Where | Defect | Correction |
|---|---|---|
| §3.4 | "UK-DST-only weeks" is a Gate-0 pass condition and cannot occur under post-2007 rules | Time-qualified: contemporary cases are the two US-only windows; a genuine UK-DST-only window existed 1995–2006 and is inside the §4.1 depth, so pre-2007 ingestion requires its own test |
| §6.8 | Gate-2 decision/order-intent code-parity test names `PaperBroker`, a P4 deliverable | Split: an arrival-time decision/order-intent code-parity test at Gate 2; the `PaperBroker` fill-level behavioural-parity test at Gate 4 |
| §10.6 | Dashboard screenshots demanded at every gate, including gates that have no dashboard | Replaced by the three-status evidence model (ADR-0005) |
| §4.6 | Known-answer test against a third-party chart is undefined for a broker-priced CFD | Venue-matched for FX; a documented weaker substitute for CFDs, labelled as not a known-answer test |

### Class B — contradicts a non-negotiable

| Where | Defect | Correction |
|---|---|---|
| §8.2 | `uuid4[:8]` in `client_order_id` breaks NN-10 and makes §6.8's identical-ID check unsatisfiable | Deterministic suffix per ADR-0002; random fragments prohibited |
| §3.1 #3 | Look-ahead stated as `ts_event > clock.now()` alone, which permits point-in-time leakage and fails NN-6 | Availability key `max(ts_event, ts_recv, available_at)` |
| §1.4 NN-2 | Same defect at the non-negotiable itself | Rewritten with universal scope; the bus is the primary enforcement point, not the only one |

### Class C — names a later phase's deliverable

§13 P1's multi-year history requirement, §8.6's broker decision, and §10.6's dashboards. Each is
re-phased with its owning phase and due gate cited. **A criterion may be re-phased only because the
artifact it names does not exist at that phase, never because a build failed to meet it.**

### Class D — safety carve-out (Principal decisions, not typos)

| Where | Decision |
|---|---|
| §0 rule 9 | Gate criteria, risk limits, breaker levels/actions and live kill criteria are **Principal-reserved**. Agents may propose via ADR; only the Principal approves and activates, naming exact old and new values, rationale, evidence and affected gates. Criteria are frozen before evaluation; no threshold may be relaxed to turn a failure into a pass; a legitimate prospective recalibration invalidates and re-runs the affected evidence. |
| §7.5 | "Halt" defined as two latched states — `ENTRY_HALT` and `FLATTEN_HALT` — never process termination. The process must never intentionally terminate while exposure remains. The −15% drawdown breaker is a `FLATTEN_HALT`; every other breaker is an `ENTRY_HALT`. |
| §8.3 | Native broker SL/TP mandatory for **every open position** (was "every entry"). Position-level, because on a netting account one instrument has one stop and a literal per-entry stop is structurally impossible once two strategies share an instrument. Stops are read back and confirmed after every fill, modification, reconnect and restart; unconfirmable protection closes the position and latches `ENTRY_HALT`. Client-side logic may supplement, never replace. |
| §2.3, §13 | Cash/spot index CFDs for v1, not futures-referenced; no expiry or rollover logic. Index instruments are `data_only` / `trading_enabled: false`. |
| §13 | Deep tick history moves from a P1 gate to a per-strategy P3 entry requirement; Gate 1 proves the pipeline on a bounded, resumable, checksummed corpus. |
| §12.3 | Branch protection recorded as a repository setting, with the solo-repo limitation stated honestly rather than describing an approval process that does not exist. |

### Class E — factual corrections

§2.1 NFP and BoE cadence marked `[VERIFY]` against primary calendars rather than asserted; §2.3
cash-session table footnoted as documentation, not a source; §2.4 split into an expected-liquidity
calendar and a settlement calendar, neither importable by a bar-boundary function; §3.4's clock
policies separated into three named measurands; §6.3 currency conversion applies the correct side of
the quote.

## Consequences

The frozen document's identity is its SHA-256, recorded here and in every gate evidence file
(§10.6). Every recorded evidence hash from before the freeze is invalidated and must be regenerated
on the first committed SHA — a sunk cost of the first commit, not of the errata. Any further change
requires an ADR and a version bump.

## Verification

`git diff --no-index --ignore-space-at-eol -- docs/SPEC-supplied-2026-09-03.md docs/SPEC.md`
enumerates every content change without treating the provenance copy's supplied CRLF endings as
edits (exit status 1 is expected when the diff is non-empty). `make check` passes on the frozen tree.
Gate 0 remains **unsigned**: freezing the specification clears one of its blockers and creates none
of the others.
