# ADR 0013 — Purpose-scoped research releases and guarded consumption

Date: 2026-09-05. Status: engineering design and implementation, coordinated
with the data and platform tasks. No release, approval, trust pin or phase-gate
acceptance is created by this change.

## Context and pre-implementation acceptance criteria

`SnapshotBarFeed` validates immutable selected-file bytes, not permission to use
them for a particular research purpose. The current admission producer intentionally
emits only QA_ONLY or QUARANTINED, with no eligible strategy selection. Reference
quality PASSED is one criterion, not a gate approval. Current Gate 2 and strategy
history/stress release evidence are absent. Actual strategy fitting is also absent;
the existing synthetic split demonstration is not training.

The boundary must reject an unapproved use before feed creation or iterator access,
including direct public API paths, not only the CLI. A valid release cannot be paired
with unrelated records, expanded scope, changed files or incomplete consumption.
Synthetic engineering must remain usable without pretending to be training. These
are testable requirements written before implementation, not relaxed gate criteria.

## Decision and trust model

Introduce a strict future release contract with two explicitly supported purposes:
STRATEGY_TRAINING and ECONOMIC_EVALUATION. A caller must supply an operator-pinned
registry digest from trusted runtime configuration, plus the exact release and
requested dataset scope. No CLI option, production registry, configuration pin,
approval receipt or released selection is installed in this increment.

The registry pins final release bytes and their purpose/dataset identity. Release
metadata binds the exact selected clean files, raw partition lineage, source,
venue, instruments, timeframe, UTC scope, fixed SPEC, data/evaluation evidence and
distinct reviewer/Principal decisions on the same package. Decision dates cannot
exceed the explicitly supplied known-at cutoff, which in turn cannot exceed actual
UTC host time sampled during authorization. Historical cutoffs are supported;
caller-controlled simulation time cannot activate future approvals early. Package
hashes exclude the two decision records to avoid a circular digest dependency; complete release hashes
include them. Exact decision artifact bytes are checked separately.

This is a trusted operator/Python workflow boundary, not cryptographic identity
authentication, gate re-evaluation or OS isolation. Self-authored dataset booleans
and status strings cannot confer authority. An operator who deliberately pins a
fabricated approval can defeat the trust model, as can arbitrary code which reads
Parquet or calls a strategy outside the public guarded workflow. We do not claim
protection against either. Protecting real trust configuration and authenticating
human approvals remain operational obligations. The authorization-time bound trusts
the host clock; it does not authenticate time
or protect against an operator or hostile Python changing that clock.

## Strict future evidence contract

Gate/reference/history/stress/tick-fidelity/calendar/policy/admission authorization
receipts use a new strict normalized metadata format. Their status, exact role,
purpose, dataset and scope must agree; missing, FAILED, INDETERMINATE, draft or
unknown evidence is rejected even if its bytes appear in a pinned release.
Selected admission partitions and lineage must agree exactly and be explicitly
eligible for the requested purpose. QA_ONLY and QUARANTINED are never promoted by
renaming a lineage field. Candidate and producer-inventory receipts also use strict
future normalized schemas with exact purpose/scope/dataset. Candidate evidence
requires complete retrieval and the exact partition union. Producer inventory
requires reproducibility PASSED and the same per-file parent mapping, not merely
the same union of parents. They are additional facts, not research approval.

The existing native triage/draft documents deliberately do not satisfy this future
receipt contract. No compatible real-data authorization is claimed today. A future
producer must bind actual reviewed gate and selection decisions to final evidence;
it must not fill out the new schema as a substitute for obtaining those decisions.
The data task must review that future producer integration before any trust pin is
configured. Calendar/policy decisions before evaluation and final phase-gate release
are distinct; neither substitutes for the other or creates a circular approval rule.

Metadata reads are bounded to 8 MiB, reject duplicate keys and non-finite numbers,
and verify file identity around reads. UTC timestamps require an explicit zero
offset and at most microsecond precision; excess precision is rejected, not rounded.

## Consumption and completion

A factory authorizes eagerly, then owns the selected snapshot feed. Protected
ordinary constructors prevent accidental creation of approval tokens or streams.
Every record must match the approved source/instrument/timeframe/time scope and be
available by the authorization's known-at time. No out-of-scope data is silently
filtered. Preserve the existing reader's complete preflight and EOF hash checks,
then recheck authorization metadata. Failures remain latched and incomplete streams
cannot return a successful completed-consumer result. A consumer must actually reach
EOF; the wrapper does not drain unprocessed data and call it completed training.
Decision replay requires a pristine stream both at request time and first iterator
advancement. Its observed-row count must match the stream count at EOF; interleaved
consumption latches failure even if caller code catches the resulting exception.

Public decision replay and publication check authority eagerly. Non-Synthetic
records require the exact factory-issued stream class, not a public subclass that
can override its validation methods, and an explicit matching purpose; unrelated
iterables or mismatched provenance fail before input access or output creation.
The CLI remains synthetic engineering only. Synthetic data cannot request a
training/economic purpose through the unapproved demo path. The low-level immutable
reader remains available for QA, and causal availability enforcement remains a
separate requirement from release authorization.

Publication records the release/registry identity and scope without claiming a gate,
identity authentication, fitted strategy or economic success. New enforcement source
modules are included in replay implementation hashes. Existing published artifacts
remain immutable; source changes make prior views stale rather than rewriting them.

## Boundaries

No trading, strategy fit, actual market dataset use, data promotion, threshold
change or approval occurs here. The new runner enforces a future authorized consumer
boundary; it is not a model-training implementation or a complete backtester. The
full gate, history, stress, cost, parity, risk and reconciliation requirements remain.

Concrete staged/canonical verification and exact schema details will be recorded in
the corresponding engineering handoff after tests and independent review. Publication
must be coordinated with the simultaneous data/evidence changes and platform owner.
