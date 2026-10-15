"""Raw-layer sequencing and overlap splicing for tick sources (SPEC 4.2, ADR-0006).

Two obligations meet here.

`seq` must be unique and monotone within `(source, instrument)`, synthesised from
arrival position because sources rarely supply one, and **stable across re-ingests
of the same range** — otherwise SPEC 4.6's byte-identical rebuild and NN-10's
bit-for-bit reproduction both fail. Stability is achieved by never re-assigning: a
re-fetch that covers stored ground appends nothing, so existing sequence numbers are
untouched.

Re-fetch overlap is resolved by aligning positions, **never by hashing values**. A
feed that repeats an identical quote is completely normal, so filtering a fetch
against a set of seen value-hashes silently drops real ticks — corrupting tick-count
volume (SPEC 4.3), the completeness report (4.4 #8) and the rebuild test. Aligning
sequences instead keeps two identical consecutive quotes distinct, because they
occupy distinct positions in both the stored tail and the new fetch.

This module owns sequencing only. Persistence — the Parquet layout in SPEC 4.2 —
lands with the source decision in ADR-0007, since what is written depends on what is
fetched.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradebot.core.clock import ReadClock
from tradebot.core.errors import IngestAlignmentError
from tradebot.core.types import Tick
from tradebot.data.normalize import TickObservation, normalize_tick

_Identity = tuple[datetime, Decimal, Decimal, int | None, int | None]


def _identity(tick: Tick) -> _Identity:
    """Return the SOURCE-derived identity of *tick*, used for positional alignment.

    Deliberately excludes ``ts_recv``, ``available_at`` and ``quality_flags``. A live
    gap-recovery re-fetch stamps a NEW local receipt time for the very same tick, so
    comparing on ``ts_recv`` would make an event fail to match itself and the splice
    would duplicate it. Only what the source actually said is compared. ``seq`` is
    excluded for the same reason — we assign it, so it cannot be evidence of identity.
    """
    return (tick.ts_event, tick.bid, tick.ask, tick.bid_size, tick.ask_size)


def overlap_length(stored_tail: Sequence[Tick], fetched: Sequence[Tick]) -> int:
    """Return the largest ``k`` where the last ``k`` stored equal the first ``k`` fetched.

    The **largest** such ``k`` is the correct choice: with a stored tail of three
    identical quotes and a fetch of the same three followed by a fourth, a smaller
    ``k`` would re-append quotes already held. Taking the maximum overlap is what
    makes a re-fetch idempotent.

    Quadratic in the tail length in the worst case, which is deliberate and fine:
    tails are bounded by the alignment window, and correctness here is worth more
    than an index.
    """
    limit = min(len(stored_tail), len(fetched))
    for size in range(limit, 0, -1):
        if all(
            _identity(stored_tail[-size + offset]) == _identity(fetched[offset])
            for offset in range(size)
        ):
            return size
    return 0


@dataclass(frozen=True, slots=True, kw_only=True)
class RawTick:
    """One tick as the raw layer stores it: the observation plus its provenance.

    ``ingested_at`` is the wall-clock time of the ingest run and is AUDIT ONLY
    (SPEC 4.2). It is structurally prevented from reaching the bus: only ``tick`` is
    an ``Event``, and it does not carry this field. An adapter that stamped a
    published event from the ingest clock would make a historical replay fail loudly,
    which is correct and must not be "fixed" by loosening the gate.
    """

    tick: Tick
    source: str
    instrument: str
    seq: int
    run_id: str
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What one fetch contributed: the new rows, and how much was already held."""

    appended: tuple[RawTick, ...]
    overlap: int


class TickIngester:
    """Append-only sequencer for one ``(source, instrument)`` tick stream."""

    __slots__ = ("_clock", "_instrument", "_next_seq", "_source", "_tail", "_tail_window")

    def __init__(
        self,
        *,
        source: str,
        instrument: str,
        clock: ReadClock,
        tail_window: int = 1024,
        start_seq: int = 0,
    ) -> None:
        if tail_window < 1:
            raise ValueError("tail_window must be at least 1")
        if start_seq < 0:
            raise ValueError("start_seq cannot be negative")
        self._source = source
        self._instrument = instrument
        self._clock = clock
        self._tail_window = tail_window
        self._tail: deque[Tick] = deque(maxlen=tail_window)
        self._next_seq = start_seq

    @property
    def next_seq(self) -> int:
        """Return the sequence number the next appended tick will receive."""
        return self._next_seq

    def ingest(self, observations: Sequence[TickObservation], *, run_id: str) -> IngestResult:
        """Normalize *observations*, splice against the retained tail, and sequence.

        The clock is read ONCE per call, so every row of one fetch carries the same
        ``ingested_at`` — a fetch is one audit event, not many.

        Raises :class:`IngestAlignmentError` when the overlap consumed the entire
        retained tail while material remains to append. That means a longer overlap
        cannot be ruled out, so appending could silently duplicate stored rows;
        refusing is the fail-closed answer, and the fix is a larger ``tail_window``
        than the biggest re-fetch overlap the source can produce.
        """
        for observation in observations:
            if observation.instrument != self._instrument:
                raise ValueError(
                    f"observation for {observation.instrument!r} sent to the "
                    f"{self._source}/{self._instrument} ingester"
                )

        fetched = tuple(normalize_tick(observation) for observation in observations)
        if not fetched:
            return IngestResult(appended=(), overlap=0)

        overlap = overlap_length(self._tail, fetched)
        remainder = fetched[overlap:]
        if remainder and overlap == self._tail_window:
            raise IngestAlignmentError(
                f"re-fetch for {self._source}/{self._instrument} matched the whole "
                f"{self._tail_window}-tick alignment window, so a longer overlap cannot be "
                "ruled out; refusing to append rather than risk duplicating stored rows"
            )

        ingested_at = self._clock.now()
        appended: list[RawTick] = []
        for tick in remainder:
            appended.append(
                RawTick(
                    tick=tick,
                    source=self._source,
                    instrument=self._instrument,
                    seq=self._next_seq,
                    run_id=run_id,
                    ingested_at=ingested_at,
                )
            )
            self._next_seq += 1
            self._tail.append(tick)
        return IngestResult(appended=tuple(appended), overlap=overlap)
