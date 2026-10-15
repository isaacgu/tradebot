# Purpose-scoped research authorization — engineering handoff

Date: 2026-09-05. Engineering only. This increment supplies neither an approved
dataset nor a fitted strategy. Missing operator trust configuration and incomplete
Gate 1/2/history/stress evidence deny real-data use before a feed is created.
The separate acquisition/evaluation task owns those evidence decisions.

## What changes

`authorization.py` validates an exact future release; `guarded.py` owns the selected
immutable feed and requires verified EOF. `iter_decisions` and `publish_replay`
require that stream plus an explicit purpose for non-Synthetic inputs. The existing
CLI accepts Synthetic engineering snapshots only. Low-level snapshot reading stays
available for QA and does not assert authorization.

Supported purpose strings are exactly `STRATEGY_TRAINING` and
`ECONOMIC_EVALUATION`. These name permitted input use, not a result: the current
decision engine still does not fit a strategy or calculate economic performance.
Synthetic engineering cannot request either purpose through its unapproved path.

## Exact future metadata contract

All listed objects reject missing and unknown fields. Version is integer `1`, not
boolean. Digests are lowercase 64-character SHA-256. Metadata files are bounded to
8 MiB each and must be regular, symlink-free files. JSON duplicate keys, non-finite
numbers and numeric overflow are rejected. Evidence paths are canonical relative
POSIX paths under the evidence root and cannot be reused between references.

`scope` has exactly `source`, `venue`, `instruments`, `timeframe`, `start_utc`,
`end_utc`. Instruments are a sorted distinct nonempty array drawn from EURUSD and
GBPUSD. Timeframe is one of 1m, 5m, 15m, 30m, 1h, 4h, 1d. Scope is exact-match,
not implicit subsetting. Dates use `YYYY-MM-DDTHH:MM:SS[.ffffff]Z` or `+00:00`;
fractional seconds have at most six digits. The observation interval is half-open;
each complete bar interval must lie within it.

`snapshot` is the existing SnapshotSpec v1 representation: exactly
`schema_version`, `venue`, `timeframe`, `files`, `dataset_id`. Each ordered selected
file has exactly `path` and `sha256`; dataset identity is computed by the existing
storage contract. Path venue/timeframe/instrument must agree with scope.

`lineage` is an ordered array matching every selected file exactly, with entries
`{file: {path, sha256}, source_partitions: [...]}`. Every per-file parent list is
nonempty; each parent has exactly `id`, `sha256`, and
`eligibility: "APPROVED_FOR_PURPOSE"`. A repeated parent across files must retain
the same hash. QA_ONLY and QUARANTINED cannot be promoted by changing this field.

The release has exactly these fields:

| Field | Required value or binding |
| --- | --- |
| schema_version | 1 |
| kind | approved-research-snapshot |
| purpose | One supported purpose |
| scope, snapshot, lineage | Exact structures above |
| evidence | Exact roles listed below, each `{path, sha256}` |
| independent_review, principal_approval | Separate decisions on the same package |

Evidence roles are exactly `spec`, `candidate`, `producer_inventory`, `admission`,
`calendar`, `policy`, `reference_result`, `gate1`, `gate2`, `history`, `stress`,
`tick_fidelity`.

- `spec`: exact frozen SPEC bytes, SHA-256
  `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`.
- `candidate`: exactly `schema_version`,
  `kind: "research-authorization-candidate"`, `status: "COMPLETE"`,
  `retrieval_status: "COMPLETE"`, `purpose`, `dataset_id`, `scope`, `partitions`.
  Partitions have only `id` and `sha256` and equal the exact lineage union.
- `producer_inventory`: exactly `schema_version`,
  `kind: "research-authorization-inventory"`, `reproducibility_status: "PASSED"`,
  `purpose`, `dataset_id`, `scope`, `lineage`. Full per-file ancestry must equal
  the release; merely preserving the union of raw parents is insufficient.
- All remaining roles: exactly `schema_version`,
  `kind: "research-authorization-evidence"`, `role`, `status`, `purpose`,
  `dataset_id`, `scope`. Status is PASSED for `reference_result`, APPROVED for
  every other role. Admission additionally requires `partitions` with exact
  lineage-union identities/hashes and APPROVED_FOR_PURPOSE eligibility.

These are future normalized receipts, deliberately incompatible with today's
native draft/triage documents. Even a pinned release cannot use FAILED,
INDETERMINATE, pending or incompatible receipts. A future reviewed bridge must
reference genuine completed evidence decisions; filling out a schema is not a
replacement for approval. This validator checks consistency and trust pins; it
does not rerun gates or authenticate the human who produced a receipt.

Each decision has exactly `person`, `decision: "APPROVED"`, `purpose`,
`dataset_id`, `package_sha256`, `decided_at_utc`, and `artifact: {path, sha256}`.
Its artifact JSON must equal the decision without the artifact reference. People
must differ after trimming and case-folding. Times must satisfy reviewer <=
Principal <= explicit authorization `known_at` <= actual UTC host time sampled
during authorization. There is no caller-supplied override for the final bound.
Historical replay cutoffs remain valid; a future simulated cutoff cannot activate
future-dated approvals early. This check trusts the host clock, not a simulated
market clock; host-time integrity remains an operational responsibility.

Package SHA-256 hashes the release excluding both decision records. Canonical
encoding uses UTF-8, sorted keys, compact separators, ASCII escaping, finite JSON
values, and one terminal LF. The final release, including its decisions, is then
hashed as exact file bytes and pinned in the independently trusted registry.

The registry has exactly `schema_version: 1`,
`kind: "research-release-registry"`, `releases: [...]`. Entries have exactly
`release_sha256`, `purpose`, `dataset_id`; duplicate release hashes are rejected.
`load_trusted_registry` requires its exact expected SHA-256 from trusted operator
configuration. Computing that expected hash from an untrusted dataset manifest
does not establish independent trust. No production registry or pin is installed.

## Consumer API and completion

The reviewed workflow loads trusted registry state, then calls
`open_approved_snapshot(root=..., spec=..., purpose=..., trusted_registry=...,
requested_scope=..., release_path=..., evidence_root=..., known_at=...)`.
The factory authorizes eagerly before root resolution, feed creation or iterator
access. It accepts no external feed factory or unrelated records iterable.

`run_approved_snapshot(consumer, ...)` invokes a consumer only after authorization
and returns only after the consumer actually reads through verified EOF. It does
not drain skipped rows. Every row must have the approved source, qualified
instrument, duration and interval; availability cannot exceed known_at. Selected
payload hashes retain existing preflight and EOF checks; bound metadata is checked
before reading and at completion, including after the consumer returns.

Decision replay accepts only the exact factory-issued stream class; public subclass
overrides cannot impersonate the approval checks. It requires a pristine stream at
request time and first advancement.
Its processed row count must equal the stream's yielded count at EOF. Prefix reads,
lazy handoff, interleaved reads and shared drivers cannot certify a suffix. Failures
latch; catching an inner failure cannot produce a successful outer runner result.

Publication binds exact dataset/files, release hash, registry hash, scope and
known_at into replay identity. It explicitly reports no economic evaluation, gate
approval, execution or strategy fitting. The two enforcement modules are included
in implementation hashes. Old artifacts are not rewritten; a changed source
identity makes old displays stale until an explicit new engineering replay.

## Verification and limits

Verification uses invented metadata and generated fixture Parquet, never collected
market payload or a production release. Tests cover default missing-trust denial,
scope/lineage/approval tampering, parser and file boundaries, actual guarded
publication, repeatability, and adversarial partial-consumption paths. Exact
counts, source hashes and commands are bound by
`build/admission-guard-staged-verification-v2-20260905/proof.json` and
`build/admission-guard-staging-20260905/publication-manifest-v2.json`. The first
staged integration run passed 819 tests, but publication review then found and
closed future-known-at approval activation and public-subclass impersonation gaps.
Their regressions failed before the respective fixes. The superseding v2 run
passed 823 tests in 144.05 seconds under pinned Python 3.12.14 with warnings treated
as errors: 275 new tests (183 authorization, 63 stream, 25 entrypoint, 4 actual
invented-Parquet integration) plus 548 existing tests. Statement-plus-branch
coverage across the five staged research modules is 93.01%. This is not
whole-repository coverage or evidence of strategy quality. V1 proof and patch are
preserved, but v1 evidence alone does not verify the two review fixes.
Ruff formatting/lint, strict mypy and source Bandit are separately recorded in the
proof. Canonical publication must additionally verify staged/canonical byte equality
and rerun the repository checks against the jointly frozen source tree.

This is a trusted-Python workflow guard, not an operating-system sandbox. An
operator deliberately pinning fabricated evidence, hostile in-process code, direct
Parquet access or manual Synthetic relabeling are outside that trust model. A
generic consumer's external side effects cannot be rolled back by this wrapper.
Gate 2, history/stress approval, a strategy-fit implementation, statistical trial
controls, calibrated costs, full simulated broker/risk/OMS and operational parity
remain separate work. No first real-data training run is authorized by this change.
