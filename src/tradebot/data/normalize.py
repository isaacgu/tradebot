"""The single source-to-event normalizer, shared by historical and live feeds.

ADR-0006 requires ONE implementation with two callers: a historical ingester and a
live adapter that normalize differently would break NN-1 code parity at the point it
matters most, because the backtest would then consume events the live path could not
have produced.

The normalizer's whole job is to turn what a source actually said into the platform's
checkable availability contract, without ever inventing or rewriting an observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradebot.core.timestamps import require_utc
from tradebot.core.types import QualityFlag, Tick


@dataclass(frozen=True, slots=True, kw_only=True)
class TickObservation:
    """One tick exactly as a source presented it, before platform normalization.

    ``ts_recv`` is ``None`` when the source carries no receipt stamp at all — the
    normal case for a historical archive. It is NOT a way to ask for a default.
    """

    instrument: str
    ts_event: datetime
    ts_recv: datetime | None
    bid: Decimal
    ask: Decimal
    bid_size: int | None = None
    ask_size: int | None = None
    backfilled: bool = False
    source_flags: tuple[str, ...] = ()


def availability_key(ts_event: datetime, ts_recv: datetime) -> datetime:
    """Return ``max(ts_event, ts_recv)``: when the platform could first have acted.

    A point-in-time record with a per-field ``available_at`` extends this maximum
    (SPEC 4.2), but such a record MUST first be decomposed into one event per
    ``(record, field, vintage)``: taking a maximum across a multi-field row hides
    the first print until the last revision lands, and taking a minimum leaks.
    """
    return max(
        require_utc(ts_event, field="ts_event"),
        require_utc(ts_recv, field="ts_recv"),
    )


def normalize_tick(observation: TickObservation) -> Tick:
    """Return the delivered ``Tick`` for *observation*, with quality flags attached.

    Three rules, all from ADR-0006:

    * **A missing receipt stamp is imputed as exactly ``ts_event``**, flagged
      ``TS_RECV_IMPUTED``. No per-source latency knob exists — a second latency
      parameter would double-count against the single execution-latency parameter
      in SPEC 6.2, and would mis-model reality anyway, since feed latency is
      near-zero most of the time and spikes precisely at news and session opens.
    * **A present receipt stamp is never modified.** Not clamped, not offset. It is
      what SPEC 4.5's staleness watchdog measures, so a stamp nudged into the future
      would make the watchdog count late — eroding an NN-4 kill switch.
    * **Skew is recorded, not corrected.** ``ts_event`` ahead of ``ts_recv`` means
      the venue's clock reads ahead of ours; that is ordinary cross-clock skew, so
      it is flagged and measured rather than treated as corruption.

    Flags are sorted and de-duplicated so that re-ingesting the same source bytes
    yields byte-identical output (NN-10, SPEC 4.6).
    """
    ts_event = require_utc(observation.ts_event, field="ts_event")
    flags: set[str] = set(observation.source_flags)

    if observation.ts_recv is None:
        ts_recv = ts_event
        flags.add(QualityFlag.TS_RECV_IMPUTED)
    else:
        ts_recv = require_utc(observation.ts_recv, field="ts_recv")

    if ts_event > ts_recv:
        flags.add(QualityFlag.CLOCK_SKEW)
    if observation.backfilled:
        flags.add(QualityFlag.BACKFILLED)

    return Tick(
        instrument=observation.instrument,
        ts_event=ts_event,
        ts_recv=ts_recv,
        available_at=availability_key(ts_event, ts_recv),
        bid=observation.bid,
        ask=observation.ask,
        bid_size=observation.bid_size,
        ask_size=observation.ask_size,
        quality_flags=tuple(sorted(flags)),
    )
