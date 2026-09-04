"""Prometheus client adapter with an injected local registry."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, generate_latest


class CoreMetrics:
    """Observe bus outcomes without starting a network listener."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        self._processed = Counter(
            "tradebot_events_processed_total",
            "Events dispatched successfully.",
            ("event_type",),
            registry=self.registry,
        )
        self._rejected = Counter(
            "tradebot_events_rejected_total",
            "Events rejected before dispatch.",
            ("event_type", "reason"),
            registry=self.registry,
        )
        self._failed = Counter(
            "tradebot_event_dispatch_failures_total",
            "Subscriber failures that halted dispatch.",
            ("event_type", "error_type"),
            registry=self.registry,
        )

    def processed(self, event_name: str) -> None:
        """Increment the successful event counter."""
        self._processed.labels(event_type=event_name).inc()

    def rejected(self, event_name: str, reason: str) -> None:
        """Increment the pre-dispatch rejection counter."""
        self._rejected.labels(event_type=event_name, reason=reason).inc()

    def failed(self, event_name: str, error: BaseException) -> None:
        """Increment the fail-closed dispatch counter."""
        self._failed.labels(event_type=event_name, error_type=type(error).__name__).inc()

    def render(self) -> bytes:
        """Return the registry in Prometheus text exposition format."""
        return generate_latest(self.registry)

    def canonical(self) -> str:
        """Return a byte-stable record of the registry for hashed gate evidence.

        Built CONSTRUCTIVELY from named fields, never by filtering lines out of
        ``render()``. A subtractive drop-list in the hashed-evidence path is the
        surface a future engineer widens to make CI green (SPEC 12.1 #6): dropping
        every ``#`` line, for instance, would erase all trace of the zero-sample
        rejection and dispatch-failure families, which are the two safety-relevant
        ones. Families with no samples are therefore recorded explicitly. Only
        ``*_created`` samples are skipped, because the client stamps wall-clock
        time into them and they can never be byte-stable.
        """
        records: list[str] = []
        for family in self.registry.collect():
            emitted = False
            for sample in family.samples:
                if sample.name.endswith("_created"):
                    continue
                labels = ",".join(f"{key}={value}" for key, value in sorted(sample.labels.items()))
                records.append(
                    f"{family.name}|{family.type}|{sample.name}|{labels}|{sample.value!r}"
                )
                emitted = True
            if not emitted:
                records.append(f"{family.name}|{family.type}|<no-samples>||")
        return "\n".join(sorted(records)) + "\n"
