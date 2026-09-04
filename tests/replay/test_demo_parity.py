from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from tradebot.core.clock import SimClock
from tradebot.core.config import MetricsConfig, RuntimeConfig
from tradebot.core.ports import StrategyContext
from tradebot.core.types import Bar, Forecast
from tradebot.demo import (
    TRACE_FIELDS,
    HelloStrategy,
    HistoricalSyntheticFeed,
    _run_pipeline,
    _synthetic_bars,
    build_gate0_manifest,
    metrics_paths,
    write_manifest,
    write_metrics_sidecars,
)

ROOT = Path(__file__).parents[2]
BACKTEST_CONFIG = ROOT / "configs" / "env" / "backtest.yaml"
PAPER_CONFIG = ROOT / "configs" / "env" / "paper.yaml"


def test_same_hello_strategy_has_backtest_paper_decision_parity(tmp_path: Path) -> None:
    run = build_gate0_manifest(BACKTEST_CONFIG, PAPER_CONFIG, git_sha="test-sha")
    manifest = run.manifest

    assert manifest["code_parity"] is True
    results = manifest["results"]
    assert isinstance(results, list)
    assert [result["mode"] for result in results] == ["backtest", "paper"]
    assert results[0]["strategy_class"] == results[1]["strategy_class"] == "HelloStrategy"
    assert results[0]["forecast_values"] == results[1]["forecast_values"] == [10.0, -10.0, 0.0]
    # The two wirings differ in exactly the two ports NN-1 permits.
    assert (results[0]["clock_class"], results[0]["feed_class"]) == (
        "SimClock",
        "HistoricalSyntheticFeed",
    )
    assert (results[1]["clock_class"], results[1]["feed_class"]) == (
        "WallClock",
        "PaperSyntheticFeed",
    )

    output = tmp_path / "gate0.json"
    digest = write_manifest(manifest, output)
    assert len(digest) == 64
    assert json.loads(output.read_text(encoding="utf-8")) == manifest
    assert output.with_suffix(".json.sha256").read_text(encoding="utf-8") == (
        f"{digest}  gate0.json\n"
    )


def test_generated_manifest_pins_its_nn7_labelling(tmp_path: Path) -> None:
    """Assert on the PRODUCED artifact, so a refactor cannot delete NN-7's label."""
    run = build_gate0_manifest(BACKTEST_CONFIG, PAPER_CONFIG, git_sha="test-sha")
    output = tmp_path / "gate0.json"
    write_manifest(run.manifest, output)
    produced = json.loads(output.read_text(encoding="utf-8"))

    assert produced["schema_version"] == 2
    assert produced["evidence_class"] == "smoke-demo-only-not-performance-evidence"
    assert produced["costs_modelled"] is False
    assert produced["pnl_reported"] is False
    assert produced["execution_enabled"] is False
    assert produced["availability_parity_demonstrated"] is False
    assert produced["fixture_base_ts"] == "2020-01-02T10:00:00+00:00"
    # The published comparator must be the one actually hashed, and must exclude
    # ts_recv, which differs between the two wirings by years.
    assert produced["trace_fields"] == list(TRACE_FIELDS)
    assert "ts_recv" not in produced["trace_fields"]


def test_metrics_sidecars_are_canonical_stable_and_name_all_counters(tmp_path: Path) -> None:
    run = build_gate0_manifest(BACKTEST_CONFIG, PAPER_CONFIG, git_sha="test-sha")
    output = tmp_path / "gate0.json"

    first = write_metrics_sidecars(run, output)
    second = write_metrics_sidecars(run, output)
    assert first == second, "canonical digests must not move between writes"
    assert set(first) == {"backtest", "paper"}

    for mode, digest in first.items():
        raw_path, digest_path = metrics_paths(output, mode)
        assert raw_path.name == f"gate0.metrics-{mode}.prom"
        assert digest_path.read_text(encoding="utf-8") == f"{digest}  {raw_path.name}\n"

    canonical = dict(run.metrics)["backtest"].canonical()
    assert "_created" not in canonical
    # All three families are present, including the zero-sample safety counters that
    # a subtractive line filter would have erased.
    assert "tradebot_events_processed" in canonical
    assert "tradebot_events_rejected" in canonical
    assert "tradebot_event_dispatch_failures" in canonical
    assert "<no-samples>" in canonical


@pytest.mark.parametrize("metrics_enabled", [False, True])
def test_manifest_event_count_is_observed_for_every_dispatched_event(
    monkeypatch: pytest.MonkeyPatch,
    metrics_enabled: bool,
) -> None:
    bars = _synthetic_bars("SYNTH_GBP_USD")
    clock = SimClock(bars[0].ts_open)
    config = RuntimeConfig(
        environment="backtest",
        run_id="measured-events",
        instrument="SYNTH_GBP_USD",
        metrics=MetricsConfig(enabled=metrics_enabled),
    )

    def emit_two_forecasts(
        strategy: HelloStrategy,
        bar: Bar,
        context: StrategyContext,
    ) -> Sequence[Forecast]:
        return (
            Forecast(
                strategy_id=strategy.id,
                instrument=bar.instrument,
                ts_event=bar.ts_close,
                ts_recv=context.clock.now(),
                value=1.0,
            ),
            Forecast(
                strategy_id=strategy.id,
                instrument=bar.instrument,
                ts_event=bar.ts_close,
                ts_recv=context.clock.now(),
                value=2.0,
            ),
        )

    monkeypatch.setattr(HelloStrategy, "on_bar", emit_two_forecasts)
    result, metrics = _run_pipeline(
        config,
        HistoricalSyntheticFeed((bars[0],)),
        clock,
        lambda bar: clock.advance_to(max(bar.ts_event, bar.ts_recv)),
        "test-sha",
    )

    assert result["forecast_values"] == [1.0, 2.0]
    assert result["events_processed"] == 3
    assert (metrics is not None) is metrics_enabled
