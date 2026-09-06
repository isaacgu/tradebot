"""Append-only external-field vintages and point-in-time liquidity expectations.

Calendar dates are expectations, never inputs to market/bar-boundary functions.
An economic release's scheduled time is a *field value*: ``ts_event`` describes
publication of that field, so an announced future release can be known today.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Self

from tradebot.core.timestamps import require_utc

type CalendarValue = str | int | bool | Decimal | datetime | None


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _stamp(value: datetime) -> str:
    return require_utc(value).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True, kw_only=True)
class CalendarFieldVintage:
    """One immutable publication, compatible with the event bus timestamp contract."""

    source: str
    record_id: str
    field: str
    vintage: str
    seq: int
    value: CalendarValue
    ts_event: datetime
    ts_recv: datetime
    available_at: datetime
    source_citation: str
    ingested_at: datetime | None = None
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("source", "record_id", "field", "vintage", "source_citation"):
            _text(getattr(self, name), name)
        if type(self.seq) is not int or self.seq < 0:
            raise ValueError("seq must be a non-negative integer")
        for name in ("ts_event", "ts_recv", "available_at"):
            require_utc(getattr(self, name), field=name)
        if self.available_at < max(self.ts_event, self.ts_recv):
            raise ValueError("available_at must not precede either event or receipt time")
        if self.ingested_at is not None:
            require_utc(self.ingested_at, field="ingested_at")
        if isinstance(self.value, datetime):
            require_utc(self.value, field="value")
        elif isinstance(self.value, Decimal):
            if not self.value.is_finite():
                raise ValueError("calendar Decimal values must be finite")
        elif self.value is not None and type(self.value) not in (str, int, bool):
            raise TypeError("calendar values must be immutable scalars; use Decimal, not float")
        if not isinstance(self.quality_flags, tuple):
            raise TypeError("quality_flags must be a tuple")
        for flag in self.quality_flags:
            _text(flag, "quality flag")


def historical_field(
    *,
    source: str,
    record_id: str,
    field: str,
    vintage: str,
    seq: int,
    value: CalendarValue,
    ts_event: datetime,
    retrieved_at: datetime,
    source_citation: str,
    archived_available_at: datetime | None,
    archive_citation: str | None = None,
) -> CalendarFieldVintage:
    """Use archived publication evidence, or conservatively admit only at retrieval.

    A current webpage is not a vintage archive. The caller must supply an explicit
    archived timestamp AND citation to replay knowledge before retrieval. Receipt
    imputation is labelled; ``ingested_at`` retains the actual audit timestamp.
    """
    require_utc(retrieved_at, field="retrieved_at")
    if archived_available_at is None:
        receipt = retrieved_at
        flags = ("AS_OF_UNVERIFIED",)
        citation = source_citation
    else:
        if not archive_citation or not archive_citation.strip():
            raise ValueError("historical availability requires an archive citation")
        receipt = require_utc(archived_available_at, field="archived_available_at")
        if receipt > retrieved_at:
            raise ValueError("archive publication cannot follow retrieval")
        flags = ("TS_RECV_IMPUTED",)
        citation = archive_citation
    return CalendarFieldVintage(
        source=source,
        record_id=record_id,
        field=field,
        vintage=vintage,
        seq=seq,
        value=value,
        ts_event=ts_event,
        ts_recv=receipt,
        available_at=max(require_utc(ts_event), receipt),
        source_citation=citation,
        ingested_at=retrieved_at,
        quality_flags=flags,
    )


def _encode(item: CalendarFieldVintage) -> str:
    value: CalendarValue = item.value
    kind = type(value).__name__
    if isinstance(value, datetime):
        value = _stamp(value)
    elif isinstance(value, Decimal):
        value = str(value)
    return json.dumps(
        {
            "source": item.source,
            "record_id": item.record_id,
            "field": item.field,
            "vintage": item.vintage,
            "seq": item.seq,
            "value": value,
            "value_type": kind,
            "ts_event": _stamp(item.ts_event),
            "ts_recv": _stamp(item.ts_recv),
            "available_at": _stamp(item.available_at),
            "source_citation": item.source_citation,
            "ingested_at": _stamp(item.ingested_at) if item.ingested_at is not None else None,
            "quality_flags": list(item.quality_flags),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode(payload: str) -> CalendarFieldVintage:
    row = json.loads(payload)
    kind = row.pop("value_type")
    if kind == "datetime":
        row["value"] = datetime.fromisoformat(row["value"])
    elif kind == "Decimal":
        row["value"] = Decimal(row["value"])
    for name in ("ts_event", "ts_recv", "available_at", "ingested_at"):
        if row[name] is not None:
            row[name] = datetime.fromisoformat(row[name])
    row["quality_flags"] = tuple(row["quality_flags"])
    return CalendarFieldVintage(**row)


class CalendarStore:
    """Local append-only storage; duplicate delivery is idempotent, revisions append.

    Source sequences are supplied by adapters and cannot be reused for another
    event. Retrieval order is independent of insertion order. The store never
    fabricates sequences or timestamps and never aggregates a multi-field row.
    """

    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(path)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS field_vintages (
                source TEXT NOT NULL, record_id TEXT NOT NULL, field TEXT NOT NULL,
                vintage TEXT NOT NULL, seq INTEGER NOT NULL, available_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(source, record_id, field, vintage), UNIQUE(source, seq)
            );
            CREATE INDEX IF NOT EXISTS field_availability ON field_vintages(available_at);
            CREATE TRIGGER IF NOT EXISTS no_vintage_updates BEFORE UPDATE ON field_vintages
                BEGIN SELECT RAISE(ABORT, 'immutable calendar vintage'); END;
            CREATE TRIGGER IF NOT EXISTS no_vintage_deletes BEFORE DELETE ON field_vintages
                BEGIN SELECT RAISE(ABORT, 'immutable calendar vintage'); END;
            """
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def append(self, item: CalendarFieldVintage) -> bool:
        """Return whether inserted; reject attempted in-place revisions."""
        payload = _encode(item)
        identity = (item.source, item.record_id, item.field, item.vintage)
        with self._db:
            old = self._db.execute(
                "SELECT payload FROM field_vintages WHERE source=? AND record_id=? "
                "AND field=? AND vintage=?",
                identity,
            ).fetchone()
            if old is not None:
                if old[0] != payload:
                    raise ValueError("immutable calendar vintage identity already exists")
                return False
            try:
                self._db.execute(
                    "INSERT INTO field_vintages VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (*identity, item.seq, _stamp(item.available_at), payload),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("source sequence is already assigned to another vintage") from exc
        return True

    def history(self, *, known_at: datetime | None = None) -> tuple[CalendarFieldVintage, ...]:
        """All visible vintages in deterministic (availability, source, seq) order."""
        cutoff = _stamp(known_at) if known_at is not None else "9999"
        rows = self._db.execute(
            "SELECT payload FROM field_vintages WHERE available_at <= ? "
            "ORDER BY available_at, source, seq",
            (cutoff,),
        )
        return tuple(_decode(row[0]) for row in rows)

    def known_at(self, instant: datetime) -> tuple[CalendarFieldVintage, ...]:
        """Latest visible vintage per source/record/field, never cross-source merging."""
        latest: dict[tuple[str, str, str], CalendarFieldVintage] = {}
        for item in self.history(known_at=instant):
            latest[item.source, item.record_id, item.field] = item
        return tuple(
            sorted(
                latest.values(),
                key=lambda item: (
                    item.available_at,
                    item.source,
                    item.seq,
                ),
            )
        )


class LiquidityStatus(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True, kw_only=True)
class LiquidityDay:
    """A sourced calendar vintage, with explicit half-open expected UTC intervals."""

    instrument: str
    session_date: date
    status: LiquidityStatus
    source: str
    source_citation: str
    effective_at: datetime
    available_at: datetime
    valid_until: datetime
    expected_intervals: tuple[tuple[datetime, datetime], ...]

    def __post_init__(self) -> None:
        for name in ("instrument", "source", "source_citation"):
            _text(getattr(self, name), name)
        if type(self.session_date) is not date:
            raise TypeError("session_date must be a date")
        if not isinstance(self.status, LiquidityStatus):
            raise TypeError("status must be FULL, PARTIAL or CLOSED")
        for name in ("effective_at", "available_at", "valid_until"):
            require_utc(getattr(self, name), field=name)
        if self.valid_until <= max(self.effective_at, self.available_at):
            raise ValueError("valid_until must follow effective_at and available_at")
        if not isinstance(self.expected_intervals, tuple):
            raise TypeError("expected_intervals must be immutable")
        if self.status == LiquidityStatus.CLOSED and self.expected_intervals:
            raise ValueError("CLOSED dates cannot contain expected intervals")
        if self.status != LiquidityStatus.CLOSED and not self.expected_intervals:
            raise ValueError("FULL and PARTIAL dates require explicit expected intervals")
        previous_end: datetime | None = None
        for interval in self.expected_intervals:
            if not isinstance(interval, tuple) or len(interval) != 2:
                raise ValueError("an expected interval is an immutable (start, end) pair")
            start, end = interval
            require_utc(start, field="interval start")
            require_utc(end, field="interval end")
            if end <= start:
                raise ValueError("expected intervals must have positive duration")
            if previous_end is not None and start < previous_end:
                raise ValueError("expected intervals must be sorted and non-overlapping")
            previous_end = end

    @property
    def expected_seconds(self) -> float:
        return sum((end - start).total_seconds() for start, end in self.expected_intervals)

    def expects(self, instant: datetime) -> bool:
        require_utc(instant)
        return any(start <= instant < end for start, end in self.expected_intervals)


class ExpectedLiquidityCalendar:
    """Expectation-layer lookup; missing, not-yet-effective, or expired means unknown."""

    def __init__(self, entries: Iterable[LiquidityDay]) -> None:
        unique: dict[tuple[str, date, datetime, datetime], LiquidityDay] = {}
        for entry in entries:
            key = (entry.instrument, entry.session_date, entry.available_at, entry.effective_at)
            if key in unique and unique[key] != entry:
                raise ValueError("ambiguous liquidity calendar vintage")
            unique[key] = entry
        self.entries = tuple(
            sorted(
                unique.values(),
                key=lambda entry: (
                    entry.instrument,
                    entry.session_date,
                    entry.available_at,
                    entry.effective_at,
                ),
            )
        )

    def lookup(self, instrument: str, day: date, *, known_at: datetime) -> LiquidityDay | None:
        require_utc(known_at, field="known_at")
        visible = [
            entry
            for entry in self.entries
            if entry.instrument == instrument
            and entry.session_date == day
            and entry.available_at <= known_at
            and entry.effective_at <= known_at
        ]
        if not visible:
            return None
        latest = max(visible, key=lambda entry: (entry.available_at, entry.effective_at))
        # An expired revision must not fall back to an older, more permissive entry.
        return latest if known_at < latest.valid_until else None

    def write(self, path: Path) -> None:
        """Write a new immutable snapshot; refuse to overwrite an existing calendar."""
        rows = [
            {
                "instrument": entry.instrument,
                "session_date": entry.session_date.isoformat(),
                "status": entry.status.value,
                "source": entry.source,
                "source_citation": entry.source_citation,
                "effective_at": _stamp(entry.effective_at),
                "available_at": _stamp(entry.available_at),
                "valid_until": _stamp(entry.valid_until),
                "expected_intervals": [
                    [_stamp(start), _stamp(end)] for start, end in entry.expected_intervals
                ],
            }
            for entry in self.entries
        ]
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"schema_version": 1, "entries": rows}, sort_keys=True) + "\n")

    @classmethod
    def read(cls, path: Path) -> Self:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if set(payload) != {"schema_version", "entries"} or payload["schema_version"] != 1:
            raise ValueError("invalid liquidity calendar schema")
        entries: list[LiquidityDay] = []
        for row in payload["entries"]:
            row["session_date"] = date.fromisoformat(row["session_date"])
            row["status"] = LiquidityStatus(row["status"])
            for name in ("effective_at", "available_at", "valid_until"):
                row[name] = datetime.fromisoformat(row[name])
            row["expected_intervals"] = tuple(
                (datetime.fromisoformat(start), datetime.fromisoformat(end))
                for start, end in row["expected_intervals"]
            )
            entries.append(LiquidityDay(**row))
        return cls(entries)
