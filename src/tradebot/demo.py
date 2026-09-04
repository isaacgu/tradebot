"""Gate-0 causal wiring demo; this is not a performance backtest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os

# Subprocess is limited to fixed, read-only local Git provenance queries.
import subprocess  # nosec B404
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

from tradebot.core.bus import EventBus
from tradebot.core.clock import ReadClock, SimClock, WallClock
from tradebot.core.config import RuntimeConfig, config_hash, load_runtime_config
from tradebot.core.ports import DataFeed, StrategyContext
from tradebot.core.types import Bar, Forecast, VolumeKind
from tradebot.monitoring.logging import configure_json_logging, run_logger
from tradebot.monitoring.metrics import CoreMetrics

SYNTHETIC_DATASET_ID = "synthetic-gate0-v1"
SYNTHETIC_SEED = 0
SYNTHETIC_BASE_TS = datetime(2020, 1, 2, 10, tzinfo=UTC)
MANIFEST_SCHEMA_VERSION = 2

# The comparator is published inside the sealed manifest, and it is derived from the
# same tuple the trace payload iterates, so the declared field list cannot drift from
# the fields actually hashed. ts_recv is deliberately ABSENT: the two wirings run
# different clocks and their receipt stamps differ by years by construction.
_TRACE_EXTRACTORS: tuple[tuple[str, Callable[[Forecast], str | float]], ...] = (
    ("strategy_id", lambda forecast: forecast.strategy_id),
    ("instrument", lambda forecast: forecast.instrument),
    ("ts_event", lambda forecast: forecast.ts_event.isoformat()),
    ("value", lambda forecast: forecast.value),
)
TRACE_FIELDS: tuple[str, ...] = tuple(name for name, _ in _TRACE_EXTRACTORS)


class DemoResult(TypedDict):
    """Canonical logical result for one environment wiring."""

    mode: str
    strategy_class: str
    strategy_id: str
    instrument: str
    clock_class: str
    feed_class: str
    forecast_values: list[float]
    trace_sha256: str
    events_processed: int
    config_hash: str
    git_sha: str


class Gate0Manifest(TypedDict):
    """Reproducible smoke-test evidence emitted by ``make demo``."""

    schema_version: int
    evidence_class: str
    dataset_id: str
    random_seed: int
    fixture_base_ts: str
    trace_fields: list[str]
    costs_modelled: bool
    pnl_reported: bool
    execution_enabled: bool
    availability_parity_demonstrated: bool
    code_parity: bool
    results: list[DemoResult]


@dataclass(frozen=True, slots=True)
class Gate0Run:
    """A completed Gate-0 demo: the manifest plus the registries the run populated."""

    manifest: Gate0Manifest
    metrics: tuple[tuple[str, CoreMetrics], ...]


@dataclass(slots=True)
class _DemoObserver:
    """Measure bus outcomes and optionally forward them to Prometheus metrics."""

    metrics: CoreMetrics | None
    processed_count: int = 0

    def processed(self, event_name: str) -> None:
        """Count a successfully dispatched event after optional metric recording."""
        if self.metrics is not None:
            self.metrics.processed(event_name)
        self.processed_count += 1

    def rejected(self, event_name: str, reason: str) -> None:
        """Forward a rejected event only when metrics are enabled."""
        if self.metrics is not None:
            self.metrics.rejected(event_name, reason)

    def failed(self, event_name: str, error: BaseException) -> None:
        """Forward a failed dispatch only when metrics are enabled."""
        if self.metrics is not None:
            self.metrics.failed(event_name, error)


class HelloStrategy:
    """Non-tradable P0 fixture that maps a closed bar direction to a forecast."""

    id = "hello"
    warmup_bars = 0

    def __init__(self, instrument: str) -> None:
        self.instruments: tuple[str, ...] = (instrument,)

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> Sequence[Forecast]:
        """Emit +10, -10, or 0 from the completed bar only."""
        direction = float((bar.close > bar.open) - (bar.close < bar.open))
        return (
            Forecast(
                strategy_id=self.id,
                instrument=bar.instrument,
                ts_event=bar.ts_close,
                ts_recv=ctx.clock.now(),
                value=10.0 * direction,
            ),
        )

    def state(self) -> Mapping[str, object]:
        """Return empty state because the fixture is stateless."""
        return {}

    def restore(self, state: Mapping[str, object]) -> None:
        """Accept only the fixture's empty checkpoint."""
        if state:
            raise ValueError("HelloStrategy has no restorable state")


@dataclass(frozen=True, slots=True)
class HistoricalSyntheticFeed:
    """Pull-shaped deterministic feed used by the backtest wiring."""

    events: tuple[Bar, ...]

    def bars(self) -> Iterator[Bar]:
        """Yield bars in fixed source order."""
        yield from self.events


@dataclass(frozen=True, slots=True)
class PaperSyntheticFeed:
    """Live-shaped finite feed fixture used without broker/order execution."""

    events: tuple[Bar, ...]

    def bars(self) -> Iterator[Bar]:
        """Yield already-closed synthetic bars in arrival order without sleeping."""
        yield from self.events


def _synthetic_bars(instrument: str) -> tuple[Bar, ...]:
    # Deliberately NOT wall-relative. The bus rejects any event whose ts_event is in
    # the future, so a fixture that lets `make demo` exit zero must consist entirely
    # of already-closed bars; moving the base to "now - 3 min" would change only the
    # margin, never the rejection path. It would also break NN-10, because the trace
    # hash covers ts_event. The paper wiring therefore cannot exercise the
    # availability guard, which is why availability_parity_demonstrated is false.
    base = SYNTHETIC_BASE_TS
    prices = (
        (Decimal("1.30000"), Decimal("1.30100"), Decimal("1.29950"), Decimal("1.30080")),
        (Decimal("1.30080"), Decimal("1.30120"), Decimal("1.29980"), Decimal("1.30020")),
        (Decimal("1.30020"), Decimal("1.30060"), Decimal("1.29990"), Decimal("1.30020")),
    )
    bars: list[Bar] = []
    for index, (open_price, high, low, close) in enumerate(prices):
        ts_open = base + timedelta(minutes=index)
        ts_close = ts_open + timedelta(minutes=1)
        bars.append(
            Bar(
                instrument=instrument,
                ts_open=ts_open,
                ts_event=ts_close,
                ts_recv=ts_close,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=100 + index,
                volume_kind=VolumeKind.TICK_COUNT,
                spread_mean=Decimal("0.00010"),
                n_ticks=100 + index,
            )
        )
    return tuple(bars)


def _trace_payload(forecasts: Sequence[Forecast]) -> list[dict[str, str | float]]:
    return [{name: extract(item) for name, extract in _TRACE_EXTRACTORS} for item in forecasts]


def _trace_hash(forecasts: Sequence[Forecast]) -> str:
    payload = json.dumps(_trace_payload(forecasts), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_pipeline(
    config: RuntimeConfig,
    feed: DataFeed,
    clock: ReadClock,
    prepare_event: Callable[[Bar], None],
    git_sha: str,
) -> tuple[DemoResult, CoreMetrics | None]:
    metrics = CoreMetrics() if config.metrics.enabled else None
    observer = _DemoObserver(metrics)
    bus = EventBus(clock, observer)
    strategy = HelloStrategy(config.instrument)
    context = StrategyContext(clock)
    forecasts: list[Forecast] = []

    def on_bar(bar: Bar) -> None:
        for forecast in strategy.on_bar(bar, context):
            bus.publish(forecast)

    bus.subscribe(Bar, on_bar)
    bus.subscribe(Forecast, forecasts.append)
    for bar in feed.bars():
        prepare_event(bar)
        bus.publish(bar)

    result = DemoResult(
        mode=config.environment,
        strategy_class=type(strategy).__name__,
        strategy_id=strategy.id,
        instrument=config.instrument,
        clock_class=type(clock).__name__,
        feed_class=type(feed).__name__,
        forecast_values=[item.value for item in forecasts],
        trace_sha256=_trace_hash(forecasts),
        events_processed=observer.processed_count,
        config_hash=config_hash(config),
        git_sha=git_sha,
    )
    return result, metrics


def _run_backtest(config: RuntimeConfig, git_sha: str) -> tuple[DemoResult, CoreMetrics | None]:
    bars = _synthetic_bars(config.instrument)
    clock = SimClock(bars[0].ts_open)
    return _run_pipeline(
        config,
        HistoricalSyntheticFeed(bars),
        clock,
        lambda bar: clock.advance_to(max(bar.ts_event, bar.ts_recv)),
        git_sha,
    )


def _run_paper(config: RuntimeConfig, git_sha: str) -> tuple[DemoResult, CoreMetrics | None]:
    bars = _synthetic_bars(config.instrument)
    clock = WallClock()
    return _run_pipeline(config, PaperSyntheticFeed(bars), clock, lambda _bar: None, git_sha)


def build_gate0_manifest(
    backtest_path: Path,
    paper_path: Path,
    *,
    git_sha: str,
) -> Gate0Run:
    """Run both P0 wirings and return their manifest plus the populated registries."""
    backtest_config = load_runtime_config(backtest_path)
    paper_config = load_runtime_config(paper_path)
    if backtest_config.environment != "backtest" or paper_config.environment != "paper":
        raise ValueError("Gate-0 manifest requires backtest and paper configs in that order")
    backtest, backtest_metrics = _run_backtest(backtest_config, git_sha)
    paper, paper_metrics = _run_paper(paper_config, git_sha)

    # Code parity only (NN-1(a)): one strategy class, one bus, one pipeline function,
    # with only the feed and the clock swapped. The metrics digests are deliberately
    # NOT part of this predicate — counter values are an observability artifact, not
    # a statement about decision equivalence. Behavioural parity (SPEC 6.8) is a
    # Gate-4 obligation and is not claimed anywhere here.
    code_parity = (
        backtest["trace_sha256"] == paper["trace_sha256"]
        and backtest["forecast_values"] == paper["forecast_values"]
    )
    manifest = Gate0Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        evidence_class="smoke-demo-only-not-performance-evidence",
        dataset_id=SYNTHETIC_DATASET_ID,
        random_seed=SYNTHETIC_SEED,
        fixture_base_ts=SYNTHETIC_BASE_TS.isoformat(),
        trace_fields=list(TRACE_FIELDS),
        costs_modelled=False,
        pnl_reported=False,
        execution_enabled=backtest_config.execution_enabled or paper_config.execution_enabled,
        availability_parity_demonstrated=False,
        code_parity=code_parity,
        results=[backtest, paper],
    )
    metrics = tuple(
        (result["mode"], populated)
        for result, populated in ((backtest, backtest_metrics), (paper, paper_metrics))
        if populated is not None
    )
    return Gate0Run(manifest=manifest, metrics=metrics)


def write_manifest(manifest: Gate0Manifest, output: Path) -> str:
    """Write canonical Gate-0 JSON evidence and return its SHA-256 digest."""
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    checksum_path = output.with_suffix(f"{output.suffix}.sha256")
    checksum_path.write_text(f"{digest}  {output.name}\n", encoding="utf-8", newline="\n")
    return digest


def metrics_paths(output: Path, mode: str) -> tuple[Path, Path]:
    """Return the raw exposition and canonical-digest sidecar paths for *mode*.

    Names are composed directly rather than through ``with_suffix``, which would
    treat ``.metrics-<mode>`` as the suffix and strip it, collapsing both modes
    onto one file.
    """
    base = f"{output.stem}.metrics-{mode}"
    return output.parent / f"{base}.prom", output.parent / f"{base}.canonical.sha256"


def write_metrics_sidecars(run: Gate0Run, output: Path) -> dict[str, str]:
    """Write per-mode exposition and canonical digests; return digest by mode.

    The raw ``.prom`` carries a wall-clock ``_created`` stamp and is therefore an
    artifact for humans and CI upload only; it is NEVER hashed as evidence. The
    canonical digest is the reproducible one (SPEC 10.6, ADR-0005).
    """
    digests: dict[str, str] = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    for mode, metrics in run.metrics:
        raw_path, digest_path = metrics_paths(output, mode)
        raw_path.write_bytes(metrics.render())
        canonical = metrics.canonical()
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        digest_path.write_text(f"{digest}  {raw_path.name}\n", encoding="utf-8", newline="\n")
        digests[mode] = digest
    return digests


def resolve_git_sha() -> str:
    """Return CI or local git identity, else an explicit uncommitted marker."""
    supplied = os.environ.get("GIT_SHA") or os.environ.get("GITHUB_SHA")
    if supplied:
        return supplied
    try:
        status = subprocess.run(  # nosec B603, B607
            ["git", "status", "--porcelain", "--untracked-files=normal"],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if status.stdout.strip():
            return "UNCOMMITTED"
        completed = subprocess.run(  # nosec B603, B607
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "UNCOMMITTED"
    candidate = completed.stdout.strip().lower()
    if len(candidate) not in (40, 64) or any(
        character not in "0123456789abcdef" for character in candidate
    ):
        return "UNCOMMITTED"
    return candidate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backtest-config", type=Path, default=Path("configs/env/backtest.yaml"))
    parser.add_argument("--paper-config", type=Path, default=Path("configs/env/paper.yaml"))
    parser.add_argument("--output", type=Path, default=Path("build/gate0/demo-manifest.json"))
    return parser


def main() -> None:
    """Run both finite demo modes, emit evidence, and fail if logical traces differ."""
    args = _parser().parse_args()
    backtest_config = load_runtime_config(args.backtest_config)
    configure_json_logging(backtest_config.logging.level)
    git_sha = resolve_git_sha()
    logger = run_logger(
        run_id=backtest_config.run_id,
        mode="gate0",
        config_hash=config_hash(backtest_config),
        git_sha=git_sha,
    )
    logger.info("gate0_demo_started", evidence_class="smoke_only")
    run = build_gate0_manifest(
        args.backtest_config,
        args.paper_config,
        git_sha=git_sha,
    )
    digest = write_manifest(run.manifest, args.output)
    metrics_digests = write_metrics_sidecars(run, args.output)
    logger.info(
        "gate0_demo_finished",
        code_parity=run.manifest["code_parity"],
        manifest_sha256=digest,
        metrics_sha256=metrics_digests,
        output=str(args.output),
    )
    if not run.manifest["code_parity"]:
        raise SystemExit("Gate-0 logical traces differ")


if __name__ == "__main__":
    main()
