# Core decision engine: engineering replay

This first increment connects explicit closed-bar inputs to causal features and
reasoned forecasts through the existing event bus. Its output is software evidence.
Gate-1 acceptance, costs/fills, financial evaluation and later trading gates remain
separate requirements. Architecture and numerical definitions are in
[ADR-0010](../adr/0010-causal-decision-replay.md).

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

## Replay an explicitly selected snapshot

Once the data owner supplies the frozen input selection, use its clean-bar manifest
with this schema (digest placeholders below are illustrative only):

```json
{
  "schema_version": 1,
  "venue": "FBS-Demo",
  "timeframe": "1m",
  "files": [
    {
      "path": "clean/bars/FBS-Demo/1m/EURUSD/2024/03/part-<64hex>.parquet",
      "sha256": "<64hex>"
    }
  ],
  "dataset_id": "<existing storage.dataset_id over the ordered FileDigest list>"
}
```

```bash
uv run --no-sync python -m tradebot.research --snapshot build/research/input-manifest.json --root build/gate1/selected-store
```

Paths are relative to `--root` and must enumerate clean-bar files in their canonical
venue/timeframe/instrument partitions. No automatic selection follows the active
collector. Hashes are checked before and after replay. Incompatible schemas,
timestamp precision loss, ambiguous ordering, venue/source mixing and index inputs
are errors. This command does not certify the supplied dataset.

Every flagged bar suppresses a forecast and resets warmup. That includes unknown
quality flags and imputed receipt timestamps. A report containing only suppressed
or warmup decisions is valid engineering output; it must not be presented as a
healthy, tradable or profitable strategy.

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
2. Implement conservative simulated fills, spread, commission, slippage and financing
   with exact known-answer tests; add the random-signal cost control.
3. Register the research hypothesis, data splits and trial counts before evaluation.
4. Measure incremental feature value and robustness across held-out periods and regimes.
5. Add portfolio allocation, risk and execution through their owning gated phases.

The runnable command begins the engine's implementation; the complete system
described by the specification remains a sequence of tested increments.
