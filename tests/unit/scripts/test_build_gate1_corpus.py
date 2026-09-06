"""Corpus selection and policy inputs cannot inflate or silently alter gate evidence."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml

from tradebot.data.acquisition_probe import ChunkRequest, fx_session_bounds
from tradebot.data.corpus import ProbeArtifact
from tradebot.data.source_clock import SourceClockInterval, SourceClockPolicy
from tradebot.data.storage import FileDigest, sha256_path


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


def reference_artifacts(
    *,
    start: date = date(2024, 9, 29),
    end: date = date(2024, 11, 1),
    empty_open_date: date | None = None,
) -> tuple[ProbeArtifact, ...]:
    result = []
    cursor = start
    while cursor < end:
        if cursor.weekday() in (6, 0, 1, 2, 3):
            result.append(artifact(cursor, rows=0 if cursor == empty_open_date else 100))
        cursor += timedelta(days=1)
    return tuple(result)


def reference_artifacts_with_lookahead_rows(rows: int) -> tuple[ProbeArtifact, ...]:
    values = reference_artifacts()
    assert values[-1].request.session_date == date(2024, 10, 31)
    return (*values[:-1], artifact(date(2024, 10, 31), rows=rows))


@pytest.mark.parametrize(
    ("reference_month", "start", "end", "weekend_side"),
    [
        ("2024-04", date(2024, 3, 28), date(2024, 5, 1), "prehistory"),
        ("2024-05", date(2024, 4, 29), date(2024, 6, 3), "lookahead"),
    ],
)
def test_reference_context_uses_adjacent_trading_close_across_weekends(
    runner: ModuleType,
    reference_month: str,
    start: date,
    end: date,
    weekend_side: str,
) -> None:
    selected = runner.select_reference_month(
        reference_artifacts(start=start, end=end),
        instrument="EURUSD",
        reference_month=reference_month,
    )

    if weekend_side == "prehistory":
        assert selected.chunks[0].canonical_close_date == date(2024, 3, 29)
        assert selected.target_chunks[0].canonical_close_date == date(2024, 4, 1)
        assert (
            selected.chunks[0].artifact.request.end
            < selected.target_chunks[0].artifact.request.start
        )
    else:
        assert selected.target_chunks[-1].canonical_close_date == date(2024, 5, 31)
        assert selected.chunks[-1].canonical_close_date == date(2024, 6, 3)
        assert (
            selected.target_chunks[-1].artifact.request.end
            < selected.chunks[-1].artifact.request.start
        )


def test_reference_month_rejects_duplicate_close_date_keys(runner: ModuleType) -> None:
    first = artifact(date(2024, 9, 29))
    duplicate = artifact(date(2024, 9, 30), window="duplicate-close")
    object.__setattr__(duplicate.request, "end", first.request.end)

    with pytest.raises(ValueError, match=r"duplicate.*canonical close date"):
        runner.select_reference_month(
            (first, duplicate),
            instrument="EURUSD",
            reference_month="2024-10",
        )


def test_reference_month_rejects_noncanonical_session_bounds(runner: ModuleType) -> None:
    malformed = artifact(date(2024, 9, 29))
    object.__setattr__(
        malformed.request,
        "start",
        malformed.request.start + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="noncanonical FX session bounds"):
        runner.select_reference_month(
            (malformed,),
            instrument="EURUSD",
            reference_month="2024-10",
        )


@pytest.mark.parametrize(
    ("minimum", "error"),
    [
        (True, TypeError),
        (5.0, TypeError),
        (0, ValueError),
        (-1, ValueError),
    ],
)
def test_reference_month_rejects_invalid_lookahead_minimum(
    runner: ModuleType,
    minimum: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error, match="minimum_lookahead_ticks"):
        runner.select_reference_month(
            reference_artifacts(),
            instrument="EURUSD",
            reference_month="2024-10",
            minimum_lookahead_ticks=minimum,
        )


@pytest.mark.parametrize("rows", range(5))
def test_reference_month_rejects_sparse_lookahead(
    runner: ModuleType,
    rows: int,
) -> None:
    with pytest.raises(ValueError, match=rf"has {rows} rows; requires at least 5"):
        runner.select_reference_month(
            reference_artifacts_with_lookahead_rows(rows),
            instrument="EURUSD",
            reference_month="2024-10",
            minimum_lookahead_ticks=5,
        )


@pytest.mark.parametrize("rows", [5, 264_936])
def test_reference_month_accepts_minimum_and_dense_lookahead(
    runner: ModuleType,
    rows: int,
) -> None:
    selected = runner.select_reference_month(
        reference_artifacts_with_lookahead_rows(rows),
        instrument="EURUSD",
        reference_month="2024-10",
        minimum_lookahead_ticks=5,
    )
    assert selected.chunks[-1].artifact.expected_rows == rows


def test_reference_month_default_lookahead_minimum_is_quality_policy(
    runner: ModuleType,
) -> None:
    expected = runner.QualityThresholds().price_reversion_ticks
    with pytest.raises(ValueError, match=rf"requires at least {expected}"):
        runner.select_reference_month(
            reference_artifacts_with_lookahead_rows(expected - 1),
            instrument="EURUSD",
            reference_month="2024-10",
        )


def test_reference_month_selection_uses_close_dates_and_exact_context(
    runner: ModuleType,
) -> None:
    inputs = reference_artifacts()
    selected = runner.select_reference_month(
        tuple(reversed(inputs)),
        instrument="EURUSD",
        reference_month="2024-10",
    )

    assert len(selected.target_chunks) == 23
    assert len(selected.chunks) == 25
    assert selected.chunks[0].role == "PREHISTORY"
    assert selected.chunks[0].artifact.request.session_date == date(2024, 9, 29)
    assert selected.chunks[0].canonical_close_date == date(2024, 9, 30)
    assert selected.target_chunks[0].artifact.request.session_date == date(2024, 9, 30)
    assert selected.target_chunks[0].canonical_close_date == date(2024, 10, 1)
    assert selected.target_chunks[-1].artifact.request.session_date == date(2024, 10, 30)
    assert selected.target_chunks[-1].canonical_close_date == date(2024, 10, 31)
    assert selected.chunks[-1].role == "LOOKAHEAD"
    assert selected.chunks[-1].artifact.request.session_date == date(2024, 10, 31)
    assert selected.chunks[-1].canonical_close_date == date(2024, 11, 1)


def test_reference_month_retains_a_completed_empty_target(runner: ModuleType) -> None:
    empty_open = date(2024, 10, 15)
    selected = runner.select_reference_month(
        reference_artifacts(empty_open_date=empty_open),
        instrument="EURUSD",
        reference_month="2024-10",
    )

    empty = [item for item in selected.target_chunks if item.artifact.expected_rows == 0]
    assert len(empty) == 1
    assert empty[0].artifact.request.session_date == empty_open
    assert empty[0].canonical_close_date == date(2024, 10, 16)


def test_reference_month_missing_target_fails_closed(runner: ModuleType) -> None:
    inputs = tuple(
        item for item in reference_artifacts() if item.request.session_date != date(2024, 10, 15)
    )

    with pytest.raises(ValueError, match="2024-10-16"):
        runner.select_reference_month(
            inputs,
            instrument="EURUSD",
            reference_month="2024-10",
        )


@pytest.mark.parametrize(
    ("removed", "message"),
    [
        (date(2024, 9, 29), "prehistory"),
        (date(2024, 10, 31), "lookahead"),
    ],
)
def test_reference_month_requires_adjacent_nonempty_context(
    runner: ModuleType,
    removed: date,
    message: str,
) -> None:
    inputs = tuple(item for item in reference_artifacts() if item.request.session_date != removed)
    with pytest.raises(ValueError, match=message):
        runner.select_reference_month(
            inputs,
            instrument="EURUSD",
            reference_month="2024-10",
        )


def test_reference_month_mode_is_explicit_and_rejects_random_controls(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "new-output"
    args = runner._arguments(
        [
            "--selection-mode",
            "reference-month",
            "--reference-month",
            "2024-10",
            "--output-dir",
            str(output),
        ]
    )
    assert args.reference_month == "2024-10"
    assert args.days is None
    assert args.seed is None

    with pytest.raises(SystemExit):
        runner._arguments(
            [
                "--selection-mode",
                "reference-month",
                "--reference-month",
                "2024-10",
                "--days",
                "23",
                "--output-dir",
                str(output),
            ]
        )


def test_random_mode_preserves_original_defaults(runner: ModuleType, tmp_path: Path) -> None:
    args = runner._arguments(["--output-dir", str(tmp_path / "new-output")])
    assert args.selection_mode == "random-sample"
    assert args.days == 30
    assert args.seed == 20260904
    assert args.source_clock_policy is None
    assert args.source_clock_evidence == []
    assert args.source_supplement_manifest is None


@pytest.mark.parametrize(
    "extra",
    [
        ["--calendar", "calendar.json"],
        ["--calendar-known-at", "2026-09-05T00:00:00Z"],
        ["--calendar-instrument", "FBS-Demo/EURUSD"],
    ],
)
def test_calendar_cli_requires_a_hashable_file_and_utc_cutoff_pair(
    runner: ModuleType,
    tmp_path: Path,
    extra: list[str],
) -> None:
    with pytest.raises(SystemExit):
        runner._arguments(["--output-dir", str(tmp_path / "new-output"), *extra])


def test_calendar_cutoff_cannot_postdate_the_evidence_run(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text('{"schema_version": 1, "entries": []}')
    repository = Path(__file__).resolve().parents[3]
    args = runner._arguments(
        [
            "--calendar",
            str(calendar_path),
            "--calendar-known-at",
            "2999-01-01T00:00:00Z",
            "--plan",
            str(repository / "configs" / "probes" / "fbs_tick_continuity_v1.json"),
            "--quality-config",
            str(tmp_path / "quality.yaml"),
            "--output-dir",
            str(tmp_path / "new-output"),
        ]
    )
    monkeypatch.setattr(runner, "_git_identity", lambda: {"head": "head", "status": ""})
    monkeypatch.setattr(runner, "_code_hashes", lambda: {"code.py": "b" * 64})
    thresholds = SimpleNamespace(price_reversion_ticks=5)
    monkeypatch.setattr(runner, "load_thresholds", lambda _: thresholds)

    with pytest.raises(ValueError, match="later than the run start"):
        runner.run(args)


def test_reference_month_report_separates_roles_and_threads_calendar(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text('{"schema_version": 1, "entries": []}')
    output = tmp_path / "reference-output"
    repository = Path(__file__).resolve().parents[3]
    args = runner._arguments(
        [
            "--selection-mode",
            "reference-month",
            "--reference-month",
            "2024-10",
            "--calendar",
            str(calendar_path),
            "--calendar-known-at",
            "2026-09-05T00:00:00Z",
            "--plan",
            str(repository / "configs" / "probes" / "fbs_tick_continuity_v1.json"),
            "--quality-config",
            str(tmp_path / "quality.yaml"),
            "--output-dir",
            str(output),
        ]
    )
    rebuild_calls: list[dict[str, object]] = []
    manifest = (FileDigest(path="clean/file.parquet", sha256="e" * 64),)
    rebuilt = SimpleNamespace(
        corpus_id="f" * 64,
        clean_tick_files=(tmp_path / "tick.parquet",),
        clean_bar_files=(tmp_path / "bar.parquet",),
        quality=(),
        bar_rows_by_timeframe=(("1m", 1),),
    )

    monkeypatch.setattr(runner, "_git_identity", lambda: {"head": "head", "status": ""})
    monkeypatch.setattr(runner, "_code_hashes", lambda: {"code.py": "b" * 64})
    thresholds = SimpleNamespace(price_reversion_ticks=7)
    monkeypatch.setattr(runner, "load_thresholds", lambda _: thresholds)
    monkeypatch.setattr(runner, "sha256_path", lambda _: "a" * 64)
    monkeypatch.setattr(runner, "version", lambda _: "fixture")
    selected_minima: list[object] = []
    original_select = runner.select_reference_month

    def select_with_recorded_minimum(*positional: object, **keywords: object) -> object:
        selected_minima.append(keywords["minimum_lookahead_ticks"])
        return original_select(*positional, **keywords)

    monkeypatch.setattr(runner, "select_reference_month", select_with_recorded_minimum)
    monkeypatch.setattr(
        runner,
        "discover_probe_artifacts",
        lambda *_args, **_kwargs: reference_artifacts(empty_open_date=date(2024, 10, 15)),
    )
    monkeypatch.setattr(
        runner,
        "import_raw_artifact",
        lambda item, **_kwargs: SimpleNamespace(
            artifact=item,
            files=(tmp_path / f"{item.request.session_date}.parquet",),
        ),
    )
    monkeypatch.setattr(runner, "file_manifest", lambda *_args, **_kwargs: manifest)

    def fake_rebuild(*_args: object, **kwargs: object) -> SimpleNamespace:
        rebuild_calls.append(kwargs)
        return rebuilt

    monkeypatch.setattr("tradebot.data.corpus.rebuild_from_raw", fake_rebuild)

    report = runner.run(args)

    selection = report["selection"]
    assert isinstance(selection, dict)
    assert selection["selection_mode"] == "reference-month"
    assert selection["reference_month"] == "2024-10"
    assert selection["expected_target_sessions"] == 23
    assert selection["selected_target_sessions"] == 23
    assert "seed" not in selection and "days" not in selection
    chunks = selection["chunks"]
    assert isinstance(chunks, list) and len(chunks) == 25
    assert chunks[0]["role"] == "PREHISTORY"
    assert chunks[1]["role"] == "REFERENCE_MONTH_TARGET"
    assert chunks[-1]["role"] == "LOOKAHEAD"
    assert selection["empty_target_chunk_ids"] == ["EURUSD/reference/2024-10-15"]
    assert selection["calendar"]["sha256"] == "a" * 64
    assert selection["calendar"]["calendar_instrument"] == "FBS-Demo/EURUSD"
    assert report["gate_approved"] is False
    assert report["reproducibility_status"] == "PASSED"
    assert report["reference_month"]["acceptance_status"] == "INDETERMINATE"
    assert report["reference_month"]["target_primary_ticks"] == 2_200
    assert len(rebuild_calls) == 2
    assert rebuild_calls[0]["calendar"] is rebuild_calls[1]["calendar"]
    assert rebuild_calls[0]["calendar_id"] == rebuild_calls[1]["calendar_id"] == "a" * 64
    assert rebuild_calls[0]["known_at"] == datetime(2026, 9, 5, tzinfo=UTC)
    assert rebuild_calls[0]["calendar_instrument"] == "FBS-Demo/EURUSD"
    assert selected_minima == [thresholds.price_reversion_ticks]
    assert rebuild_calls[0]["thresholds"] is thresholds
    assert "source_clock" not in selection
    assert rebuild_calls[0]["source_clock"] is None


def clock_policy(evidence_sha256: str = "a" * 64) -> SourceClockPolicy:
    start = datetime(2024, 9, 29, 21, tzinfo=UTC)
    end = datetime(2024, 11, 2, tzinfo=UTC)
    return SourceClockPolicy(
        schema_version=1,
        policy_id="fixture-autumn-eurusd-only",
        source="FBS-Demo",
        instrument="EURUSD",
        status="EXPERIMENTAL_ONLY",
        label_start=start,
        label_end=end,
        evidence_sha256=(("anchor", evidence_sha256),),
        intervals=(
            SourceClockInterval(
                label_start=start,
                label_end=datetime(2024, 10, 27, 3, tzinfo=UTC),
                offset_seconds_to_utc=-10800,
            ),
            SourceClockInterval(
                label_start=datetime(2024, 10, 27, 4, tzinfo=UTC),
                label_end=end,
                offset_seconds_to_utc=-7200,
            ),
        ),
    )


def friday_supplements() -> tuple[ProbeArtifact, ...]:
    result = []
    for day in (
        date(2024, 10, 4),
        date(2024, 10, 11),
        date(2024, 10, 18),
        date(2024, 10, 25),
        date(2024, 11, 1),
    ):
        # The selector consumes request attributes; the separate supplemental loader
        # validates real short-interval request types and their captured files.
        item = artifact(day - timedelta(days=1), window="supplement")
        start = datetime(day.year, day.month, day.day, 21, tzinfo=UTC)
        object.__setattr__(item.request, "session_date", day)
        object.__setattr__(item.request, "start", start)
        object.__setattr__(
            item.request, "end", start + timedelta(hours=2 if day.month == 11 else 3)
        )
        result.append(item)
    return tuple(result)


def clock_artifacts() -> tuple[ProbeArtifact, ...]:
    return reference_artifacts() + friday_supplements()


def test_clock_month_selects_friday_tails_and_labels_acquisition_counts(runner: ModuleType) -> None:
    selected = runner.select_clock_reference_month(
        clock_artifacts(),
        instrument="EURUSD",
        reference_month="2024-10",
        source_clock=clock_policy(),
    )
    assert len(selected.expected_target_close_dates) == 23
    assert len(selected.required_windows) == 25
    assert len(selected.chunks) == 30
    assert {
        item.artifact.request.session_date
        for item in selected.chunks
        if item.artifact.request.session_date.weekday() == 4
    } == {
        date(2024, 10, 4),
        date(2024, 10, 11),
        date(2024, 10, 18),
        date(2024, 10, 25),
        date(2024, 11, 1),
    }
    payload = runner._clock_selection_payload(selected)
    assert payload["coverage_status"] == "MAPPED_REQUEST_COVERAGE_COMPLETE"
    assert payload["actual_tick_completeness"] == "NOT_ESTABLISHED_BY_REQUEST_COVERAGE"
    assert "target_primary_ticks" not in payload
    assert "selected_target_sessions" not in payload
    assert (
        runner.select_clock_reference_month(
            tuple(reversed(clock_artifacts())),
            instrument="EURUSD",
            reference_month="2024-10",
            source_clock=clock_policy(),
        )
        == selected
    )


@pytest.mark.parametrize("removed", [date(2024, 10, 4), date(2024, 10, 8), date(2024, 11, 1)])
def test_clock_month_fails_closed_for_missing_target_or_context_request(
    runner: ModuleType, removed: date
) -> None:
    inputs = tuple(item for item in clock_artifacts() if item.request.session_date != removed)
    with pytest.raises(ValueError, match="missing mapped request coverage") as error:
        runner.select_clock_reference_month(
            inputs, instrument="EURUSD", reference_month="2024-10", source_clock=clock_policy()
        )
    if removed == date(2024, 10, 4):
        assert "2024-10-04T18:00:00+00:00, 2024-10-04T21:00:00+00:00" in str(error.value)
    elif removed == date(2024, 11, 1):
        assert "LOOKAHEAD/2024-11-01" in str(error.value)


def test_clock_month_legacy_pool_reports_all_five_missing_friday_tails(runner: ModuleType) -> None:
    with pytest.raises(ValueError, match="missing mapped request coverage") as error:
        runner.select_clock_reference_month(
            reference_artifacts(),
            instrument="EURUSD",
            reference_month="2024-10",
            source_clock=clock_policy(),
        )
    assert str(error.value).count("REFERENCE_MONTH_TARGET/") == 4
    assert str(error.value).count("LOOKAHEAD/") == 1


def test_clock_month_does_not_bridge_an_unmapped_weekday_policy_hole(runner: ModuleType) -> None:
    policy = clock_policy()
    first, second = policy.intervals
    hole_start = datetime(2024, 10, 8, 12, tzinfo=UTC)
    hole_end = hole_start + timedelta(hours=1)
    policy = replace(
        policy,
        intervals=(
            replace(first, label_end=hole_start),
            replace(first, label_start=hole_end),
            second,
        ),
    )
    with pytest.raises(ValueError, match="unmapped label interval"):
        runner.select_clock_reference_month(
            clock_artifacts(), instrument="EURUSD", reference_month="2024-10", source_clock=policy
        )


def test_clock_month_rejects_duplicate_acquisition_dates(runner: ModuleType) -> None:
    inputs = clock_artifacts()
    with pytest.raises(ValueError, match="duplicate acquisition-label start date"):
        runner.select_clock_reference_month(
            (*inputs, inputs[0]),
            instrument="EURUSD",
            reference_month="2024-10",
            source_clock=clock_policy(),
        )


@pytest.mark.parametrize("instrument,source", [("GBPUSD", "FBS-Demo"), ("EURUSD", "other")])
def test_clock_month_scope_is_exact(runner: ModuleType, instrument: str, source: str) -> None:
    with pytest.raises(ValueError, match="exact FBS-Demo/EURUSD scope"):
        runner.select_clock_reference_month(
            clock_artifacts(),
            instrument=instrument,
            reference_month="2024-10",
            source_clock=replace(clock_policy(), instrument=instrument, source=source),
        )


def test_clock_policy_requires_exact_real_evidence_bindings(
    runner: ModuleType, tmp_path: Path
) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text("fixture evidence")
    path = tmp_path / "policy.json"
    policy = clock_policy(sha256_path(evidence))
    path.write_text(policy.to_json())
    bound = runner.load_source_clock_inputs(path, [f"anchor={evidence}"])
    assert bound.policy.identity == policy.identity
    assert bound.sha256 == sha256_path(path)
    assert bound.to_dict()["approval_status"] == "NOT_ASSESSED_BY_PRODUCER"
    for bindings in ([], [f"wrong={evidence}"], [f"anchor={evidence}", f"anchor={evidence}"]):
        with pytest.raises(ValueError, match="evidence"):
            runner.load_source_clock_inputs(path, bindings)
    evidence.write_text("changed evidence")
    with pytest.raises(ValueError, match="policy/evidence changed"):
        bound.verify_unchanged()
    with pytest.raises(ValueError, match="policy/evidence changed"):
        runner.load_source_clock_inputs(path, [f"anchor={evidence}"])


@pytest.mark.parametrize(
    "extra",
    [
        ["--source-clock-policy", "clock.json"],
        ["--selection-mode", "clock-reference-month", "--reference-month", "2024-10"],
        ["--source-clock-evidence", "anchor=evidence.json"],
        ["--source-supplement-sha256", "a" * 64],
    ],
)
def test_clock_controls_cannot_silently_alter_legacy_mode(
    runner: ModuleType, tmp_path: Path, extra: list[str]
) -> None:
    with pytest.raises(SystemExit):
        runner._arguments(["--output-dir", str(tmp_path / "output"), *extra])


@pytest.mark.parametrize("drift", [None, "policy", "evidence", "supplement"])
def test_clock_producer_threads_policy_and_rejects_input_drift(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str | None
) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text("fixture evidence")
    path = tmp_path / "policy.json"
    policy = clock_policy(sha256_path(evidence))
    path.write_text(policy.to_json())
    supplement_path = tmp_path / "supplement.json"
    supplement_path.write_text("fixture supplemental manifest")
    supplement_sha256 = sha256_path(supplement_path)
    repository = Path(__file__).resolve().parents[3]
    output = tmp_path / "output"
    args = runner._arguments(
        [
            "--selection-mode",
            "clock-reference-month",
            "--reference-month",
            "2024-10",
            "--source-clock-policy",
            str(path),
            "--source-clock-evidence",
            f"anchor={evidence}",
            "--source-supplement-manifest",
            str(supplement_path),
            "--source-supplement-sha256",
            supplement_sha256,
            "--output-dir",
            str(output),
            "--plan",
            str(repository / "configs" / "probes" / "fbs_tick_continuity_v1.json"),
        ]
    )
    monkeypatch.setattr(runner, "_git_identity", lambda: {"head": "head", "status": ""})
    monkeypatch.setattr(runner, "_code_hashes", lambda: {"code.py": "b" * 64})
    monkeypatch.setattr(
        runner, "load_thresholds", lambda _: SimpleNamespace(price_reversion_ticks=5)
    )
    monkeypatch.setattr(runner, "version", lambda _: "fixture")
    monkeypatch.setattr(
        runner, "sha256_path", lambda p: sha256_path(p) if p.is_file() else "a" * 64
    )
    monkeypatch.setattr(
        runner, "discover_probe_artifacts", lambda *_args, **_kwargs: reference_artifacts()
    )
    supplement_calls: list[dict[str, object]] = []

    def fake_load_supplement(manifest_path: Path, **kwargs: object) -> tuple[ProbeArtifact, ...]:
        supplement_calls.append(kwargs)
        if sha256_path(manifest_path) != kwargs["expected_manifest_sha256"]:
            raise ValueError("supplement manifest changed")
        return friday_supplements()

    supplement_module = ModuleType("tradebot.data.source_supplement")
    monkeypatch.setattr(
        supplement_module, "load_supplement_artifacts", fake_load_supplement, raising=False
    )
    monkeypatch.setitem(sys.modules, "tradebot.data.source_supplement", supplement_module)
    monkeypatch.setattr(
        runner,
        "import_raw_artifact",
        lambda item, **_kwargs: SimpleNamespace(
            artifact=item, files=(tmp_path / f"{item.request.session_date}.parquet",)
        ),
    )
    monkeypatch.setattr(
        runner,
        "file_manifest",
        lambda *_args, **_kwargs: (FileDigest(path="clean/file.parquet", sha256="e" * 64),),
    )
    calls: list[dict[str, object]] = []

    def fake_rebuild(*_args: object, **kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        if drift is not None:
            {"policy": path, "evidence": evidence, "supplement": supplement_path}[drift].write_text(
                "changed"
            )
        return SimpleNamespace(
            corpus_id="f" * 64,
            clean_tick_files=(tmp_path / "tick.parquet",),
            clean_bar_files=(tmp_path / "bar.parquet",),
            quality=(),
            bar_rows_by_timeframe=(("1m", 1),),
        )

    monkeypatch.setattr("tradebot.data.corpus.rebuild_from_raw", fake_rebuild)
    if drift is not None:
        with pytest.raises(
            ValueError, match=r"policy/evidence changed|supplement manifest changed"
        ):
            runner.run(args)
        assert len(calls) == (2 if drift == "supplement" else 1)
        assert not (output / "report.json").exists()
        return
    report = runner.run(args)
    assert len(calls) == 2
    assert calls[0]["source_clock"] == calls[1]["source_clock"] == policy
    assert report["reproducibility_status"] == "PASSED"
    assert report["gate_approved"] is False
    assert report["source_clock_inputs_unchanged"] is True
    assert report["source_supplement_inputs_unchanged"] is True
    assert len(supplement_calls) == 2
    assert all(call["expected_manifest_sha256"] == supplement_sha256 for call in supplement_calls)
    assert all(call["artifact_root"] == supplement_path.parent for call in supplement_calls)
    assert report["selection"]["acquisition_snapshot"]["completed_checkpoints"] == 25
    assert report["reference_month"]["acceptance_status"] == "INDETERMINATE"
    assert "target_primary_ticks" not in report["reference_month"]
    chunks = report["selection"]["chunks"]
    assert len(chunks) == 30
    assert all(item["role"] == "ACQUISITION_LABEL_CHUNK" for item in chunks)
    assert all("canonical_close_date" not in item and "rows" not in item for item in chunks)
    assert report["selection"]["source_clock"]["identity"] == policy.identity
