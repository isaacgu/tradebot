# ADR 0011 — Engineering preregistrations, attempts and chronological splits

Date: 2026-09-05. Status: staged engineering decision; canonical publication coordinated
with the UI/Gate-1 task. This does not approve a phase gate.

## Context

SPEC 5.1 requires hypotheses and splits to be declared before experiments, and all
attempts to remain visible. The existing causal decision replay establishes neither
economic performance nor the research process needed for Gate 3. Gate 1 remains
unapproved, and cost/fill modelling and the financial validation suite are absent.

## Decision

Add three independent modules without changing the existing replay report or its
dashboard pointer:

- `registry.py`: content-addressed, immutable ENGINEERING_ONLY declarations and
  append-only START/FINISH events in local SQLite. BEGIN IMMEDIATE serializes writes.
  Each operation validates schema, canonical event bytes, chained hashes, the stored
  head/count and legal lifecycle transitions. Start metadata identifies the requested
  partition. Failed attempts remain recorded; abrupt interruption leaves an incomplete
  STARTED record. Retry requires a new attempt ID, not replacement of old evidence.
- `splits.py`: nonempty UTC half-open training, validation and lockbox windows.
  Inter-window gaps must preserve the declared embargo, which must cover the
  nonnegative prospective label horizon. Route by observation start; require decision
  availability inside that same window and the entire label endpoint no later than
  its end. Late data is purged, not reassigned to a later partition. Lockbox execution
  is denied before acquiring the input iterator.
- `experiment_demo.py`: register and run one fixed, known synthetic fixture through
  the existing causal replay. Bind its exact manifest, configuration, split, SPEC,
  implementation hashes and runtime in the declaration. Each unlocked partition gets
  fresh strategy state. Publish immutable report/trace pairs, then mark completion;
  there is no latest pointer and no market-data input option.

The fixture windows are fixed before this demonstration executes: training
10:00–11:15, validation 11:20–12:30, lockbox 12:35–12:40 UTC on 2024-01-08;
embargo five minutes, prospective label horizon one minute. These are short software
test partitions, not economically meaningful holding periods or research windows.
No return labels are calculated, no parameters fitted, and no variants selected.

## Consequences and boundaries

This is research-control scaffolding, not completion of P3 or a substitute for
SPEC 5.1's economic hypothesis, broker/cost evidence, walk-forward, CPCV, DSR, PBO,
red-team review or single-use Gate-3 lockbox release. Engineering attempt counts
are not independent financial trials and must not be fed into DSR.

The full synthetic fixture is already known, generated and checksummed. The selector
also exhausts unselected records for source EOF verification and inspects routing
timestamps. We claim only that lockbox records are not delivered to features or
strategies. We do not claim untouched real-data custody, external preregistration
timestamping, filesystem isolation or a lockbox-unlock mechanism.

The local hash chain detects accidental/partial corruption, not a coordinated rewrite
of the database and all hashes. It is not cryptographic access control. A future
real-research workflow needs an externally anchored audit policy and separately
controlled lockbox storage. Immutable artifacts and SQLite are not one cross-resource
transaction: a crash after publication can leave an orphan artifact plus STARTED
evidence; it must never turn an incomplete attempt into success. Audit validates
ledger evidence, not continued existence of every externally referenced artifact.
The publication protocol has not established power-loss durability across SQLite
and artifact directories. Following storage recovery, verify each report/trace hash
before relying on a COMPLETED ledger entry. Ordinary process interruption and
storage/power failure are distinct failure models.

There is no change to execution policy, frozen data-pipeline files, thresholds,
raw data, collection checkpoints, dependency locks or existing UI contracts.

## Verification

Focused tests cover split boundaries, availability, purging, invalid plans, denied
lockbox access, full iterator exhaustion, immutable registrations, failed/incomplete
attempts, corruption, duplicate/concurrent writes and deterministic end-to-end
artifacts. Exact final commands, outcomes and file hashes belong in the accompanying
engineering report; passing these checks makes no financial or gate claim.
