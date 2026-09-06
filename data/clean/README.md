# Clean data

Phase 1 now implements deterministic clean-tick and clean-bar Parquet output. Conventional
paths under a chosen output root are:

- `clean/ticks/{venue}/{instrument}/{yyyy}/{mm}/part-{corpus_id}.parquet`
- `clean/bars/{venue}/{timeframe}/{instrument}/{yyyy}/{mm}/part-{corpus_id}.parquet`

Venue remains part of each series identity. Clean ticks retain availability, source sequence,
quality flags and bar eligibility; crossed/locked/out-of-session observations remain evidence
instead of disappearing. Retrospective diagnostics have a separate column and cannot rewrite
an earlier causal bar input. Bars retain quote-derived spread statistics and closing bid/ask.

Rebuilds consume the same immutable raw snapshot, sort deterministically and record complete
file manifests plus a content-derived `dataset_id`. No future calendar revision may alter the
bar-boundary function. Missing expected-liquidity coverage leaves affected completeness/quality
criteria indeterminate, even if rebuilt files are byte-identical.

Current evidence outputs are isolated under ignored `build/` run directories. This directory
does not contain a dataset accepted for strategy evaluation or trading. Gate 1 remains pending;
see `docs/reports/gate1_evidence.md` for completed artifacts and outstanding criteria.
