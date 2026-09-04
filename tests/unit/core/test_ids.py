from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tradebot.core.ids import new_client_order_id


def test_client_order_id_is_attributable_deterministic_and_sequence_unique() -> None:
    timestamp = datetime(2025, 3, 17, 12, 0, 0, 123000, tzinfo=UTC)
    first = new_client_order_id("paper", "hello", "GBP/USD", timestamp, run_id="run-1", sequence=7)
    retry = new_client_order_id("paper", "hello", "GBP/USD", timestamp, run_id="run-1", sequence=7)
    second = new_client_order_id("paper", "hello", "GBP/USD", timestamp, run_id="run-1", sequence=8)

    assert first.startswith("paper-hello-GBPUSD-1742212800123-7-")
    assert len(first.rsplit("-", maxsplit=1)[1]) == 32
    assert first == retry
    assert first != second


def test_client_order_id_rejects_empty_components() -> None:
    with pytest.raises(ValueError, match="strategy_id"):
        new_client_order_id(
            "paper",
            "---",
            "GBP/USD",
            datetime(2025, 1, 1, tzinfo=UTC),
            run_id="run-1",
            sequence=0,
        )


@pytest.mark.parametrize("sequence", [-1, True])
def test_client_order_id_rejects_invalid_sequence(sequence: object) -> None:
    with pytest.raises(ValueError, match="sequence"):
        new_client_order_id(
            "paper",
            "hello",
            "GBP/USD",
            datetime(2025, 1, 1, tzinfo=UTC),
            run_id="run-1",
            sequence=sequence,  # type: ignore[arg-type]
        )
