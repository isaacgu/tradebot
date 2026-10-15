"""End-to-end contracts for the engineering replay and its published artifacts."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path

import pytest

from tradebot.data.storage import (
    CLEAN_BAR_SCHEMA,
    ImmutableParquetWriter,
    clean_bar_path,
    dataset_id,
    file_manifest,
)
from tradebot.research.__main__ import main
from tradebot.research.demo import synthetic_setup
from tradebot.research.engine import ReplayConfig, iter_decisions
from tradebot.research.feed import ReplayBar, SnapshotBarFeed, SnapshotSpec
from tradebot.research.report import ReplayProvenance, publish_replay


def test_repeatable_full_artifact_explains_what_was_and_was_not_evaluated(tmp_path: Path) -> None:
    records, config, provenance = synthetic_setup("UNCOMMITTED")
    first = publish_replay(iter(records), config, provenance, output_root=tmp_path / "first")
    second = publish_replay(iter(records), config, provenance, output_root=tmp_path / "second")
    assert first.run_id == second.run_id
    assert first.report_sha256 == second.report_sha256
    assert first.decisions_sha256 == second.decisions_sha256
    report_path = first.directory / "report.json"
    report = json.loads(report_path.read_text())
    assert report["schema_version"] == 1
    assert report["evidence_class"] == "engineering-decision-replay-only"
    assert report["bars_processed"] == 320
    assert report["decisions_by_status"] == {"forecast": 62, "suppressed": 2, "warmup": 256}
    assert report["costs_modelled"] is False
    assert report["pnl_reported"] is False
    assert report["execution_enabled"] is False
    assert report["orders_created"] == 0
    assert report["gate_approvals_claimed"] == []
    assert report["economic_evaluation"] == "NOT_PERFORMED"
    assert report["forecast_scaling"] == "UNCALIBRATED"
    assert report["identity"]["provenance"]["dataset_id"] == provenance.dataset_id
    assert any("synthetic-v1: TS_RECV_IMPUTED" in item for item in report["caveats"])
    for filename, digest in json.loads((first.directory / "manifest.json").read_text())[
        "files"
    ].items():
        assert hashlib.sha256((first.directory / filename).read_bytes()).hexdigest() == digest
    pointer = json.loads((tmp_path / "first" / "latest.json").read_text())
    assert pointer["sha256"] == first.report_sha256
    assert pointer["report"] == f"{first.run_id}/report.json"
    repeated = publish_replay(iter(records), config, provenance, output_root=tmp_path / "first")
    assert repeated == first


def test_receipt_delayed_by_years_is_decision_time_and_never_market_close() -> None:
    rows, config, _ = synthetic_setup("UNCOMMITTED")
    delayed = tuple(
        ReplayBar(
            replace(row.bar, ts_recv=row.bar.ts_recv + timedelta(days=700)), row.source, row.seq
        )
        for row in rows[:140]
    )
    decisions = tuple(iter_decisions(delayed, config))
    forecast_decisions = [decision for decision in decisions if decision.forecast is not None]
    assert forecast_decisions
    for decision in forecast_decisions:
        assert decision.forecast is not None
        assert decision.forecast.ts_event == decision.decision_at == decision.available_at
        assert decision.forecast.ts_event > decision.bar_close + timedelta(days=699)
        assert decision.features is not None


@pytest.mark.parametrize(
    ("names", "seconds"),
    [
        ((), 60),
        (("EURUSD",), 60),
        (("Demo/US500",), 60),
        (("A/EURUSD", "B/GBPUSD"), 60),
        (("Demo/EURUSD", "Demo/EURUSD"), 60),
        (("Demo/EURUSD",), 0),
        (("Demo/EURUSD",), True),
    ],
)
def test_invalid_replay_selection_is_rejected(names: tuple[str, ...], seconds: int) -> None:
    with pytest.raises(ValueError):
        ReplayConfig(names, seconds)


def test_empty_missing_and_wrong_timeframe_cannot_publish_a_success(tmp_path: Path) -> None:
    rows, config, provenance = synthetic_setup("UNCOMMITTED")
    for data, settings in [
        ((), config),
        (rows[:1], config),
        (rows, replace(config, timeframe_seconds=300)),
        (rows, ReplayConfig(("Unknown/EURUSD",), 60)),
    ]:
        with pytest.raises(ValueError):
            publish_replay(data, settings, provenance, output_root=tmp_path)
        assert not list(tmp_path.iterdir())


def test_forged_manifest_identity_is_rejected() -> None:
    _, _, provenance = synthetic_setup("UNCOMMITTED")
    with pytest.raises(ValueError, match="differs"):
        replace(provenance, dataset_id="f" * 64)
    with pytest.raises(ValueError):
        ReplayProvenance(provenance.dataset_id, "live", provenance.source_manifest, "UNCOMMITTED")


def test_real_parquet_schema_reaches_cli_with_venue_identity_and_same_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records, config, _ = synthetic_setup("UNCOMMITTED")
    paths = []
    for instrument in config.instruments:
        symbol = instrument.split("/")[1]
        selected = [record for record in records if record.bar.instrument == instrument]
        path = clean_bar_path(
            tmp_path,
            venue="Synthetic",
            timeframe="1m",
            instrument=symbol,
            month=selected[0].bar.ts_open.date(),
            corpus_id="a" * 64,
        )
        rows: list[dict[str, object]] = []
        for record in selected:
            bar = record.bar
            rows.append(
                {
                    "instrument": symbol,
                    "ts_open": bar.ts_open,
                    "ts_close": bar.ts_close,
                    "ts_recv": bar.ts_recv,
                    "available_at": bar.available_at,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "volume_kind": bar.volume_kind,
                    "n_ticks": bar.n_ticks,
                    "spread_mean": bar.spread_mean,
                    "spread_max": bar.spread_mean,
                    "bid_close": None,
                    "ask_close": None,
                    "source": record.source,
                    "seq": record.seq,
                    "quality_flags": list(bar.quality_flags),
                }
            )
        with ImmutableParquetWriter(
            path,
            CLEAN_BAR_SCHEMA,
            identity={
                "tradebot.kind": "clean-bar",
                "tradebot.instrument": symbol,
                "tradebot.venue": "Synthetic",
                "tradebot.source": "synthetic-v1",
                "tradebot.timeframe": "1m",
                "tradebot.corpus_id": "a" * 64,
                "tradebot.month": "2024-01",
            },
        ) as writer:
            writer.write_rows(rows)
        paths.append(path)
    manifest = file_manifest(paths, relative_to=tmp_path)
    spec = SnapshotSpec("Synthetic", "1m", manifest, dataset_id(manifest))
    loaded = tuple(SnapshotBarFeed(tmp_path, spec).records())
    assert len(loaded) == len(records)
    expected = [
        (d.status, d.forecast.value if d.forecast else None)
        for d in iter_decisions(records, config)
    ]
    assert [
        (d.status, d.forecast.value if d.forecast else None) for d in iter_decisions(loaded, config)
    ] == expected
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"schema_version": 1, **asdict(spec)}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tradebot.research",
            "--snapshot",
            str(snapshot),
            "--root",
            str(tmp_path),
            "--output-root",
            str(tmp_path / "output"),
        ],
    )
    main()
    pointer = json.loads((tmp_path / "output" / "latest.json").read_text())
    report = json.loads((tmp_path / "output" / pointer["report"]).read_text())
    assert report["bars_processed"] == 320
    assert report["identity"]["provenance"]["source_kind"] == "immutable_clean_snapshot"
    assert report["identity"]["provenance"]["dataset_id"] == spec.dataset_id
    assert set(report["instruments"]) == {"Synthetic/EURUSD", "Synthetic/GBPUSD"}


def test_cli_produces_readable_summary_and_checks_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["tradebot.research", "--synthetic", "--output-root", str(tmp_path / "runs")]
    )
    main()
    assert "Financial evaluation: NOT_PERFORMED" in capsys.readouterr().out
    assert (tmp_path / "runs" / "latest.json").is_file()
    wrong_spec = tmp_path / "SPEC.md"
    wrong_spec.write_text("not the frozen spec")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tradebot.research",
            "--synthetic",
            "--spec",
            str(wrong_spec),
            "--output-root",
            str(tmp_path / "failed"),
        ],
    )
    with pytest.raises(SystemExit):
        main()
    assert not (tmp_path / "failed").exists()
