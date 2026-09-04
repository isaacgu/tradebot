from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from tradebot.core.bus import EventBus
from tradebot.core.clock import SimClock
from tradebot.core.errors import EventDispatchError, LookAheadError
from tradebot.monitoring.metrics import CoreMetrics


@dataclass(frozen=True, slots=True)
class ProbeEvent:
    ts_event: datetime
    ts_recv: datetime
    name: str


def test_metrics_expose_processed_rejected_and_failed_events() -> None:
    now = datetime(2025, 3, 17, 12, tzinfo=UTC)
    metrics = CoreMetrics()
    bus = EventBus(SimClock(now), metrics)
    bus.publish(ProbeEvent(now, now, "ok"))

    future_bus = EventBus(SimClock(now), metrics)
    with pytest.raises(LookAheadError):
        future_bus.publish(ProbeEvent(now + timedelta(seconds=1), now, "future"))

    failed_bus = EventBus(SimClock(now), metrics)

    def fail(_event: ProbeEvent) -> None:
        raise RuntimeError("boom")

    failed_bus.subscribe(ProbeEvent, fail)
    with pytest.raises(EventDispatchError):
        failed_bus.publish(ProbeEvent(now, now, "failed"))

    exposition = metrics.render().decode("utf-8")
    assert 'tradebot_events_processed_total{event_type="ProbeEvent"} 1.0' in exposition
    assert 'reason="ts_event"' in exposition
    assert 'error_type="RuntimeError"' in exposition
