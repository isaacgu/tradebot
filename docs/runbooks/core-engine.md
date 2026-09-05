# Core decision engine: engineering replay

This first increment connects explicit closed-bar inputs to causal features and
reasoned forecasts through the existing event bus. Its output is software evidence.
Gate-1 acceptance, costs/fills, financial evaluation and later trading gates remain
separate requirements. Architecture and numerical definitions are in
[ADR-0010](../adr/0010-causal-decision-replay.md); the real-data consumption boundary
is defined in [ADR-0013](../adr/0013-purpose-scoped-research-authorization.md).

## Run the demonstration

From the repository root using the pinned environment:

```bash
uv run --no-sync python -m tradebot.research --synthetic
```

On this Windows workstation the existing pinned environment is inside WSL:

```powershell
wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/isaac.gumbi/Documents/ChatGPT/Bot && PYTHONTZPATH= .venv/bin/python -m tradebot.research --synthetic'
```

The fixed fixture has 160 invented one-minute bars for each of GBP/USD and EUR/USD,
with distinct trends, warmup and a quality interruption. It does not contact MT5 or
the network. All forecasting uses the same `MomentumStrategy.on_bar` implementation.

The command prints the report path and hash. Repeating identical code, config and
inputs on the same runtime reproduces the artifacts and does not overwrite them.
Changing a relevant input gives a different run directory. `latest.json` points to
the most recently completed run and is intentionally mutable.

## Replay a separately prepared Synthetic snapshot

The CLI is synthetic engineering only: both `--synthetic` and `--snapshot` are
non-training paths. A `--snapshot` manifest whose venue is not exactly `Synthetic`
is rejected before the feed is created, including an FBS-Demo manifest with valid
file hashes. A frozen selection alone is not permission to replay real market data.

Prefer the fixed `--synthetic` demonstration above. For a separately prepared,
genuinely invented clean-bar fixture, the snapshot schema is shown below. Digest
placeholders are illustrative, not runnable values; the fixed demonstration does
not create these Parquet files or this input manifest.

```json
{
  "schema_version": 1,
  "venue": "Synthetic",
  "timeframe": "1m",
  "files": [
    {
      "path": "clean/bars/Synthetic/1m/EURUSD/2024/01/part-<64hex>.parquet",
      "sha256": "<64hex>"
    }
  ],
  "dataset_id": "<existing storage.dataset_id over the ordered FileDigest list>"
}
```

```bash
uv run --no-sync python -m tradebot.research --snapshot build/research/synthetic-input-manifest.json --root build/research/synthetic-store --output-root build/research/synthetic-snapshot-replay
```

Paths are relative to `--root` and must enumerate clean-bar files in their canonical
venue/timeframe/instrument partitions. No automatic selection follows the active
collector. Hashes are checked before and after replay. Incompatible schemas,
timestamp precision loss, ambiguous ordering, venue/source mixing and index inputs
are errors. Supported durations are `1m`, `5m`, `15m`, `30m`, `1h`, `4h` and `1d`.
This command does not certify a dataset or authorize a research purpose. Never
relabel collected market data as Synthetic to avoid the real-data hold.

Every flagged bar suppresses a forecast and resets warmup. That includes unknown
quality flags and imputed receipt timestamps. A report containing only suppressed
or warmup decisions is valid engineering output; it must not be presented as a
healthy, tradable or profitable strategy.

## Real-data hold, QA reading and future authorized use

The low-level `SnapshotBarFeed` remains available for read-only data QA. It checks
selected-file identity, schema and ordering, with completion hashes at EOF; it
does not decide admission eligibility or grant training/evaluation permission.
Passing its ordinary real-data iterator directly to `iter_decisions` or
`publish_replay` is no longer a supported replay path and is rejected eagerly.

A future reviewed workflow must use `open_approved_snapshot` or
`run_approved_snapshot` from `tradebot.research.guarded`, supplying an exact
`ResearchPurpose.STRATEGY_TRAINING` or `ResearchPurpose.ECONOMIC_EVALUATION`, the
matching immutable selection and scope, and genuine hash-bound release evidence.
The registry digest must come from independently trusted operator configuration;
hashing a self-authored release does not establish approval. The required distinct
reviewer/Principal decisions and complete evidence must already exist. The UTC
`known_at` cutoff cannot exceed the host authorization time, and decisions and
observations must be available by that cutoff.

The factory authorizes before constructing the feed. Decision replay requires a
pristine guard-owned stream and the same explicit purpose; publication also checks
exact dataset/file provenance. Full consumption through verified EOF is required,
and failures or partial consumption cannot become completed evidence. A purpose
names authorized input use, not a completed strategy fit or economic evaluation;
the decision engine implements neither result.

No production trust registry, approved release or first real-data training run is
provided by this runbook. Current QA_ONLY/QUARANTINED dispositions, draft proposals
and incomplete gate evidence do not meet that future contract. Keep the hold when
evidence is absent; do not use an older unrestricted CLI or fabricate receipts.
The [authorization handoff](../reports/purpose-scoped-research-authorization.md)
records the exact future schema and the trusted-Python boundary's limitations.

## Read the output

| Artifact | Use |
|---|---|
| `latest.json` | Mutable pointer to a completed run and its report hash |
| `<run-id>/report.json` | Versioned identity, status counts, source flags, latest decisions, caveats and absent capabilities |
| `<run-id>/decisions.jsonl` | Complete ordered decision trail; one JSON row per input bar |
| `<run-id>/manifest.json` | Exact report and trace SHA-256 digests |

Statuses are `warmup`, `suppressed`, `abstain` and `forecast`. A forecast is a signed,
uncalibrated research signal on the core ±20 scale. It is not a buy/sell order,
probability, position size or approved strategy. Reasons and component features
explain the result. Spread/ATR and tick counts are visible diagnostics; only the
momentum returns and volatility currently determine forecast strength.

Availability controls what can enter the window. Market close describes when the
bar occurred. Decision time describes when the runner actually learned it. The
distinction is retained when historical observations arrive late. Horizon lengths
count observed bars; gap seconds expose missing time, without forward-filling.

Implementation changes produce new replay identities, including for unchanged
synthetic inputs. Keep earlier content-addressed reports as historical evidence;
do not rewrite their hashes or treat them as validation of the changed code. After
verification, an explicit fixed-synthetic replay can publish a current report.
Existing research-control and execution/accounting declarations also bind source
identity: register a new declaration and use fresh attempt identifiers instead of
overwriting an old ledger entry. None of these recovery steps releases real data.

## UI integration and team ownership

The UI task owns Grafana and exporters; the engine task owns features, strategies,
research replay and this report contract. Both tasks can communicate using the
Codex task tools. They share the workspace, so changes to frozen acquisition and
evidence files require explicit coordination with the run owner.

For a consumer: read `latest.json`, reject unsupported `schema_version`, verify the
referenced report SHA, and display its `evidence_class`, source kind and caveats.
Read detailed trace rows only when requested. Do not treat `status=COMPLETED` as
Gate-1 approval or live activity. A dashboard integration is complete only after
the UI task implements and verifies that consumer.

## Next engineering increments

1. Close Gate-1 data evidence and approvals with the data/UI task.
2. Extend the existing synthetic fill/accounting preview into a complete,
   independently validated cost-aware backtester; add the random-signal cost control.
3. Register the research hypothesis, data splits and trial counts before evaluation.
4. Measure incremental feature value and robustness across held-out periods and regimes.
5. Add portfolio allocation, risk and execution through their owning gated phases.

The runnable command begins the engine's implementation; the complete system
described by the specification remains a sequence of tested increments.
