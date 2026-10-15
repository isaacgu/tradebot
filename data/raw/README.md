# Raw data

Phase 1 provides an append-only raw Parquet writer in `tradebot.data.storage` and a checksum-
verified import path in `tradebot.data.corpus`. The conventional layout under a selected data
root is `raw/{source}/{instrument}/{yyyy}/{mm}/part-{artifact_id}.parquet`.

FBS source responses and completed checkpoint metadata remain immutable diagnostic artifacts
under ignored `build/fbs-tick-continuity-v1/`. The corpus importer copies completed, verified
chunks into its own raw snapshot; it does not rewrite the running probe's files. Source decimal
text, timestamp/flag/volume fields, row position, source identity, run identity and ingest audit
timestamp are retained. Revisions from another ingest retain their distinct provenance.

The current Gate-1 evidence run selects a new ignored `build/` output directory rather than
publishing accepted data here. No market data is bundled for redistribution or accepted for
research/trading by this README. A successful copy/import is not a data-quality or phase-gate
approval. See `docs/reports/gate1_evidence.md` for the actual acceptance status.
