"""Corpus selection and policy inputs cannot inflate or silently alter gate evidence."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from tradebot.data.acquisition_probe import ChunkRequest, fx_session_bounds
from tradebot.data.corpus import ProbeArtifact


@pytest.fixture
def runner() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "build_gate1_corpus.py"
    spec = importlib.util.spec_from_file_location("build_gate1_corpus", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


POLICY = """schema_version: 1
status: prospective
thresholds:
  spread_multiplier: "10"
  price_sigma: "20"
  price_reversion_ticks: 5
  gap_seconds: 10
  fast_market_median_seconds: 1
  rolling_horizon_seconds: 3600
  minimum_history: 20
"""


def test_load_thresholds_keeps_exact_decimal_and_duration_policy(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    path = tmp_path / "quality.yaml"
    path.write_text(POLICY)
    values = runner.load_thresholds(path)
    assert values.spread_multiplier == Decimal("10")
    assert values.price_sigma == Decimal("20")
    assert values.gap_threshold == timedelta(seconds=10)
    assert values.rolling_horizon == timedelta(hours=1)
    assert values.minimum_history == 20


@pytest.mark.parametrize(
    "old,new",
    [
        ("schema_version: 1", "schema_version: true"),
        ("schema_version: 1", "schema_version: 2"),
        ("status: prospective", "status: approved"),
        ('spread_multiplier: "10"', "spread_multiplier: 10.0"),
        ('spread_multiplier: "10"', 'spread_multiplier: "NaN"'),
        ('price_sigma: "20"', 'price_sigma: "0"'),
        ("gap_seconds: 10", "gap_seconds: true"),
        ("gap_seconds: 10", 'gap_seconds: "10"'),
        ("price_reversion_ticks: 5", "price_reversion_ticks: 0"),
        ("minimum_history: 20", "minimum_history: 1"),
        ("minimum_history: 20", "undocumented_knob: 20"),
        ("schema_version: 1", "unknown: 1\nschema_version: 1"),
    ],
)
def test_policy_drift_or_ambiguous_types_are_rejected(
    runner: ModuleType,
    tmp_path: Path,
    old: str,
    new: str,
) -> None:
    path = tmp_path / "quality.yaml"
    path.write_text(POLICY.replace(old, new))
    with pytest.raises((ValueError, yaml.YAMLError)):
        runner.load_thresholds(path)


@pytest.mark.parametrize(
    "old,new",
    [
        ("status: prospective", "status: prospective\nstatus: prospective"),
        ("  minimum_history: 20", "  minimum_history: 20\n  minimum_history: 20"),
    ],
)
def test_duplicate_policy_keys_are_rejected_even_if_values_match(
    runner: ModuleType,
    tmp_path: Path,
    old: str,
    new: str,
) -> None:
    path = tmp_path / "quality.yaml"
    path.write_text(POLICY.replace(old, new))
    with pytest.raises((ValueError, yaml.YAMLError), match="duplicate"):
        runner.load_thresholds(path)


def artifact(
    day: date, *, instrument: str = "EURUSD", rows: int = 100, window: str = "reference"
) -> ProbeArtifact:
    start, end = fx_session_bounds(day)
    request = ChunkRequest(
        logical_symbol=instrument,
        broker_symbol=instrument,
        window_id=window,
        session_date=day,
        index_in_window=0,
        start=start,
        end=end,
    )
    return ProbeArtifact(
        request=request,
        ordinal=0,
        plan_hash="a" * 64,
        source="FBS-Demo",
        run_id="fixture",
        completed_at=datetime(2026, 9, 4, tzinfo=UTC),
        raw_path=Path("fixture.tsv.gz"),
        checkpoint_path=Path("fixture.checkpoint.json"),
        semantic_sha256="b" * 64,
        compressed_sha256="c" * 64,
        expected_rows=rows,
        artifact_id="d" * 64,
    )


def test_sample_is_seeded_order_independent_and_has_thirty_distinct_nonempty_dates(
    runner: ModuleType,
) -> None:
    days = [
        date(2024, 9, 29) + timedelta(days=index)
        for index in range(60)
        if (date(2024, 9, 29) + timedelta(days=index)).weekday() in (6, 0, 1, 2, 3)
    ]
    data = tuple(artifact(day) for day in days)
    other_pair = tuple(artifact(day, instrument="GBPUSD") for day in days)
    selected = runner.select_sample(data + other_pair, instrument="EURUSD", days=30, seed=20260904)
    reordered = runner.select_sample(
        tuple(reversed(data + other_pair)), instrument="EURUSD", days=30, seed=20260904
    )
    assert selected == reordered
    assert len(selected) == len({item.request.session_date for item in selected}) == 30
    assert all(
        item.request.logical_symbol == "EURUSD" and item.expected_rows > 0 for item in selected
    )
    assert list(selected) == sorted(selected, key=lambda item: item.request.session_date)


def test_empty_days_or_another_pair_cannot_fill_a_sample_shortfall(runner: ModuleType) -> None:
    sunday = date(2024, 9, 29)
    data = (
        artifact(sunday),
        artifact(sunday + timedelta(days=1), rows=0),
        artifact(sunday + timedelta(days=1), instrument="GBPUSD"),
    )
    with pytest.raises(ValueError, match="only 1 saved"):
        runner.select_sample(data, instrument="EURUSD", days=2, seed=1)


def test_overlapping_windows_do_not_silently_choose_a_vintage(runner: ModuleType) -> None:
    day = date(2024, 9, 29)
    with pytest.raises(ValueError, match="overlapping"):
        runner.select_sample(
            (artifact(day), artifact(day, window="overlap")), instrument="EURUSD", days=1, seed=1
        )


@pytest.mark.parametrize("days", [0, -1])
def test_sample_size_must_be_positive(runner: ModuleType, days: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        runner.select_sample((), instrument="EURUSD", days=days, seed=1)
