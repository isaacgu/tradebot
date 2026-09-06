# ADR-0009: Immutable corpus, quality policy and point-in-time calendars

Status: accepted for implementation; real-data Gate-1 acceptance remains pending.
Date: 2026-09-04. SPEC v1.0 SHA-256:
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`.

## Decision and assumptions frozen before evaluation

Use PyArrow, pinned by `uv.lock`, for immutable raw and deterministically derived
clean Parquet. Import only checksum-verified, completed FBS diagnostic chunks into
the storage pipeline; never rewrite their source files. Raw carries ingest-run
identity and exact source fields. Historical receipt time is imputed as event time
with `TS_RECV_IMPUTED`; ingest wall time remains audit-only. Venue is a path key.
Clean snapshots identify sorted clean-file hashes. Revisions create new raw runs
and explicit clean supersession records rather than changing old raw evidence.

Retain invalid/out-of-session evidence with flags; exclude ineligible quotes from
mid/bar aggregation. Repeated equal quotes within a source response are not removed
by value hashing. Re-fetch overlap is handled by the existing positional contract.
Use the shared normalizer and bar builder. Storage-only spread maximum and closing
bid/ask stay outside the delivered core Bar type, as the interface ADR requires.

Quality defaults are prospective engineering policy, not tuned to the observed
reference-month flagged rate: spread multiple 10 and price sigma multiple 20 follow
SPEC 4.4; rolling history is one hour; minimum history is 20 observations; transient
price reversion horizon is five ticks; a gap threshold is ten seconds where median
inter-tick time is below one second. Short history remains explicitly indeterminate.
The five-tick horizon targets isolated reverted spikes while keeping sustained moves.
Ten seconds is a conservative multi-second interruption detector for an ordinarily
sub-second stream. It is independently owned from live staleness breakers even where
their numeric defaults match. Cross-source divergence needs separately sourced
calibration and remains NOT_EVALUABLE with one venue. These choices do not change
the frozen <0.1% gate criterion, risk limits, breakers or any Principal-reserved value.

Calendar storage is append-only SQLite, using one record per source/record/field/
vintage with separate event, receipt and availability timestamps. Historical facts
without archived publication evidence become available when retrieved, never at an
invented historical timestamp. A query at T sees only available vintages, and fields
are decomposed independently before bus publication.

Expected-liquidity records require explicit dated FULL/PARTIAL/CLOSED expectations,
source citation, effective date and known-at time. Unknown dates remain unknown.
Calendars are consumed only by quality/completeness, never bar-boundary functions.
Settlement calendars remain separate from expected liquidity and belong to the
future cost-model integration. Source-backed economic/holiday population and
reference-bar hand verification are evidence tasks, not satisfied by unit fixtures.

## Success criteria

- Immutable source preservation and deterministic clean snapshots survive repeat runs.
- Raw corruptions, unsafe paths, incomplete chunks and schema ambiguity fail closed.
- Rebuild thirty randomly selected complete session-days twice from stored raw and
  compare every resulting one-minute bar file byte-for-byte; record seed and hashes.
- Synthetic examples exercise each implemented quality category and temporal edge.
- Real calendar/reference evidence is reported separately from software tests.
- The evidence checker enforces the frozen spec hash, five category statuses,
  artifact checksums and obligations due at Gate 1. It cannot authenticate or invent
  reviewer/Principal approvals, and cannot replace missing CI evidence with local tests.

Sources: [Parquet writer API](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.write_table.html),
SPEC 2.4, 4, 10.6 and the accepted feed/interface ADRs.
