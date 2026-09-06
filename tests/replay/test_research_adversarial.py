"""Adversarial integration checks for replay isolation and audit publication."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tradebot.core.errors import EventDispatchError
from tradebot.research.demo import synthetic_setup
from tradebot.research.engine import ReplayConfig, iter_decisions
from tradebot.research.feed import ReplayBar
from tradebot.research.report import publish_replay


def test_interleaved_instruments_match_their_standalone_decisions() -> None:
    records, config, _ = synthetic_setup("UNCOMMITTED")
    joint = list(iter_decisions(records, config))
    for instrument in config.instruments:
        standalone = list(
            iter_decisions(
                (record for record in records if record.bar.instrument == instrument),
                ReplayConfig((instrument,), config.timeframe_seconds, config.momentum),
            )
        )
        assert standalone == [decision for decision in joint if decision.instrument == instrument]


def test_changed_future_suffix_cannot_change_past_decisions() -> None:
    records, config, _ = synthetic_setup("UNCOMMITTED")
    split = 150  # Both instruments have already completed the default warmup.
    changed = records[:split] + tuple(
        replace(
            record,
            bar=replace(
                record.bar,
                open=record.bar.open * Decimal(2),
                high=record.bar.high * Decimal(2),
                low=record.bar.low * Decimal(2),
                close=record.bar.close * Decimal(2),
            ),
        )
        for record in records[split:]
    )
    original_decisions = list(iter_decisions(records, config))
    changed_decisions = list(iter_decisions(changed, config))
    assert any(decision.forecast is not None for decision in original_decisions[:split])
    assert original_decisions[:split] == changed_decisions[:split]
    assert original_decisions[split:] != changed_decisions[split:]


def test_per_instrument_sequence_tie_has_explicit_instrument_tiebreak() -> None:
    records, config, _ = synthetic_setup("UNCOMMITTED")
    tied = tuple(replace(record, seq=record.seq // 2) for record in records)
    # The same source may use the same sequence for two instruments at one instant.
    assert tied[0].seq == tied[1].seq
    assert tied[0].bar.available_at == tied[1].bar.available_at
    assert tied[0].bar.instrument < tied[1].bar.instrument
    assert len(list(iter_decisions(tied, config))) == len(tied)
    with pytest.raises(ValueError, match=r"increasing|order"):
        list(iter_decisions((tied[1], tied[0], *tied[2:]), config))


def test_old_bar_arriving_late_cannot_enter_warmed_history() -> None:
    records, config, _ = synthetic_setup("UNCOMMITTED")
    late = replace(
        records[0],
        seq=records[-1].seq + 1,
        bar=replace(
            records[0].bar,
            ts_recv=records[-1].bar.available_at + timedelta(seconds=1),
        ),
    )
    with pytest.raises(EventDispatchError, match="halted") as error:
        list(iter_decisions((*records, late), config))
    assert isinstance(error.value.__cause__, ValueError)
    assert "increasing" in str(error.value.__cause__)


def test_source_cannot_switch_inside_one_instrument_history() -> None:
    records, config, _ = synthetic_setup("UNCOMMITTED")
    # Distinct availability keeps this source change legal under global sort order.
    switched = replace(records[-1], source="synthetic-v2")
    with pytest.raises(ValueError, match="source"):
        list(iter_decisions((*records[:-1], switched), config))


def test_eof_verification_failure_cannot_publish_or_replace_latest(tmp_path: Path) -> None:
    records, config, provenance = synthetic_setup("UNCOMMITTED")
    prior = publish_replay(records, config, provenance, output_root=tmp_path)
    pointer_before = (tmp_path / "latest.json").read_bytes()
    report_before = (prior.directory / "report.json").read_bytes()
    entries_before = sorted(path.name for path in tmp_path.iterdir())

    def corrupt_feed() -> Iterator[ReplayBar]:
        yield from records
        raise ValueError("snapshot digest changed at EOF")

    with pytest.raises(ValueError, match="digest changed"):
        publish_replay(corrupt_feed(), config, provenance, output_root=tmp_path)
    assert (tmp_path / "latest.json").read_bytes() == pointer_before
    assert (prior.directory / "report.json").read_bytes() == report_before
    assert sorted(path.name for path in tmp_path.iterdir()) == entries_before


def test_existing_trace_tampering_is_detected_without_overwrite(tmp_path: Path) -> None:
    records, config, provenance = synthetic_setup("UNCOMMITTED")
    published = publish_replay(records, config, provenance, output_root=tmp_path)
    pointer_before = (tmp_path / "latest.json").read_bytes()
    trace = published.directory / "decisions.jsonl"
    tampered = trace.read_bytes() + b'{"tampered":true}\n'
    trace.write_bytes(tampered)
    with pytest.raises(FileExistsError, match="immutable run differs"):
        publish_replay(records, config, provenance, output_root=tmp_path)
    assert trace.read_bytes() == tampered
    assert (tmp_path / "latest.json").read_bytes() == pointer_before
