"""Measured failures dominate missing context; retrospective semantics stay explicit.

The partial-retracement fixtures document the existing implementation, not an
economic adjudication of source quotes or an approved interpretation of "reverts".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from tradebot.data.bars import FixedInterval
from tradebot.data.quality import (
    CleanTickRecord,
    DataQualityFlag,
    QualityCheckStatus,
    QualityInput,
    QualityThresholds,
    TickQualityPipeline,
)

_START = datetime(2024, 10, 21, 10, tzinfo=UTC)


def _pipeline() -> TickQualityPipeline:
    return TickQualityPipeline(
        instrument="EURUSD",
        source="Synthetic",
        session_boundary=FixedInterval(timedelta(minutes=1)),
        thresholds=QualityThresholds(
            minimum_history=2,
            price_sigma=Decimal("2"),
            price_reversion_ticks=2,
        ),
    )


def _quote(
    seq: int,
    mid: Decimal,
    *,
    spread: Decimal = Decimal("0.02"),
    instant: datetime | None = None,
) -> QualityInput:
    return QualityInput(
        instrument="EURUSD",
        source="Synthetic",
        seq=seq,
        ts_event=instant or _START + timedelta(milliseconds=seq * 100),
        bid=mid - spread / 2,
        ask=mid + spread / 2,
    )


def _run(pipeline: TickQualityPipeline, inputs: list[QualityInput]) -> tuple[CleanTickRecord, ...]:
    rows: list[CleanTickRecord] = []
    for item in inputs:
        rows.extend(pipeline.process(item))
    rows.extend(pipeline.finish())
    return tuple(rows)


def test_measured_spread_failure_dominates_calendar_and_warmup_indeterminacy() -> None:
    pipeline = _pipeline()
    rows = _run(
        pipeline,
        [_quote(seq, Decimal("100")) for seq in range(3)]
        + [_quote(3, Decimal("100"), spread=Decimal("0.22"))],
    )
    summary = pipeline.summary()
    assert DataQualityFlag.SPREAD_OUTLIER in rows[-1].quality_flags
    assert not any(row.retrospective_flags for row in rows)
    assert all(row.eligible_for_bars for row in rows)
    assert summary.calendar_status is QualityCheckStatus.INDETERMINATE
    assert dict(summary.flag_counts)[DataQualityFlag.QUALITY_WARMUP] > 0
    assert summary.quality_status is QualityCheckStatus.FAILED


def test_spread_equality_at_the_frozen_multiple_does_not_fail() -> None:
    pipeline = _pipeline()
    rows = _run(
        pipeline,
        [_quote(seq, Decimal("100")) for seq in range(3)]
        + [_quote(3, Decimal("100"), spread=Decimal("0.20"))],
    )
    assert DataQualityFlag.SPREAD_OUTLIER not in rows[-1].quality_flags
    assert pipeline.summary().quality_status is QualityCheckStatus.INDETERMINATE


@pytest.mark.parametrize(
    ("history", "jump", "partial"),
    [
        (("100", "101", "100", "101"), "104", "102.5"),
        (("100", "99", "100", "99"), "96", "97.5"),
    ],
)
def test_current_band_reentry_semantics_allow_only_partial_retracement(
    history: tuple[str, ...], jump: str, partial: str
) -> None:
    mids = [Decimal(value) for value in history]
    baseline, candidate, confirmation = mids[-1], Decimal(jump), Decimal(partial)
    returns = [(right - left) / left for left, right in pairwise(mids)]
    mean = sum(returns, Decimal(0)) / len(returns)
    variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / len(returns)
    volatility = variance.sqrt()
    band = Decimal("2") * volatility * baseline
    assert abs(candidate - baseline) > band
    assert Decimal(0) < abs(confirmation - baseline) < band
    assert (candidate - baseline) * (confirmation - baseline) > 0

    pipeline = _pipeline()
    rows = _run(
        pipeline,
        [_quote(seq, mid) for seq, mid in enumerate([*mids, candidate, confirmation])],
    )
    assert DataQualityFlag.PRICE_OUTLIER in rows[4].retrospective_flags
    assert DataQualityFlag.PRICE_OUTLIER not in rows[4].quality_flags
    assert rows[4].eligible_for_bars
    assert rows[4].available_at == rows[4].ts_event


def test_a_return_after_the_source_row_horizon_does_not_confirm_candidate() -> None:
    pipeline = _pipeline()
    mids = [Decimal(value) for value in ("100", "101", "100", "101", "104", "105", "106", "101")]
    rows = _run(pipeline, [_quote(seq, mid) for seq, mid in enumerate(mids)])
    assert DataQualityFlag.PRICE_OUTLIER not in rows[4].retrospective_flags


@pytest.mark.parametrize("invalid_kind", ["locked", "crossed", "regressed"])
def test_invalid_future_quotes_do_not_confirm_reversion(invalid_kind: str) -> None:
    pipeline = _pipeline()
    mids = [Decimal(value) for value in ("100", "101", "100", "101", "104")]
    candidate = [_quote(seq, mid) for seq, mid in enumerate(mids)]
    spread = {"locked": Decimal(0), "crossed": Decimal("-0.02")}.get(invalid_kind, Decimal("0.02"))
    instant = _START if invalid_kind == "regressed" else None
    rows = _run(
        pipeline,
        [
            *candidate,
            _quote(5, Decimal("101"), spread=spread, instant=instant),
            _quote(6, Decimal("105")),
        ],
    )
    assert not rows[5].eligible_for_bars
    assert DataQualityFlag.PRICE_OUTLIER not in rows[4].retrospective_flags
