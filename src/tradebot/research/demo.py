"""Fixed synthetic paths for demonstrating engineering decisions, never market evidence."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradebot.core.types import Bar, VolumeKind
from tradebot.data.storage import FileDigest, dataset_id
from tradebot.research.engine import ReplayConfig
from tradebot.research.feed import ReplayBar
from tradebot.research.report import ReplayProvenance, canonical_bytes


def synthetic_records() -> tuple[ReplayBar, ...]:
    """Return 160 one-minute bars per pair, with fixed warmup and quality-reset cases."""
    start = datetime(2024, 1, 8, 10, tzinfo=UTC)
    prices = {"Synthetic/EURUSD": Decimal("1.10"), "Synthetic/GBPUSD": Decimal("1.27")}
    rows: list[ReplayBar] = []
    for index in range(160):
        for offset, instrument in enumerate(sorted(prices)):
            opened = prices[instrument]
            # Unequal step sizes make volatility non-zero. Opposite pair directions
            # exercise independent state; all values are deliberately invented.
            direction = 1 if offset == 0 else -1
            step = Decimal((index % 5 + 1) * direction) / Decimal(100_000)
            closed = opened + step
            ts_open = start + timedelta(minutes=index)
            ts_close = ts_open + timedelta(minutes=1)
            bar = Bar(
                instrument=instrument,
                ts_open=ts_open,
                ts_event=ts_close,
                ts_recv=ts_close + timedelta(milliseconds=250),
                open=opened,
                high=max(opened, closed) + Decimal("0.00005"),
                low=min(opened, closed) - Decimal("0.00005"),
                close=closed,
                volume=100 + index % 7,
                volume_kind=VolumeKind.TICK_COUNT,
                n_ticks=100 + index % 7,
                spread_mean=Decimal("0.00010"),
                quality_flags=("TS_RECV_IMPUTED",) if index == 80 else (),
            )
            rows.append(ReplayBar(bar, "synthetic-v1", index * 2 + offset))
            prices[instrument] = closed
    return tuple(rows)


def synthetic_setup(git_sha: str) -> tuple[tuple[ReplayBar, ...], ReplayConfig, ReplayProvenance]:
    """Identify the exact fixture bytes, explicit pair list and uncalibrated config."""
    records = synthetic_records()
    manifest = (
        FileDigest(
            "synthetic-decision-fixture-v1.json",
            hashlib.sha256(canonical_bytes(records)).hexdigest(),
        ),
    )
    config = ReplayConfig(("Synthetic/EURUSD", "Synthetic/GBPUSD"), 60)
    provenance = ReplayProvenance(dataset_id(manifest), "synthetic", manifest, git_sha)
    return records, config, provenance
