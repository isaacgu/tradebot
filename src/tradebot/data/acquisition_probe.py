"""Pure planning and evidence analysis for bounded FX tick acquisition.

The Windows MT5 script is intentionally kept out of this module.  This code accepts
plain Python values, so acquisition planning, response validation, repeatability and
hashing remain testable on every CI host.

This is source-viability evidence, not a Gate-1 quality verdict.  In particular, an
empty completed session is recorded as an observation.  Without the expected-liquidity
calendar required by SPEC 2.4 it cannot be labelled either a broker defect or a valid
market closure.

Hashes are *semantic*: they cover every source tick field in source order using a
canonical, exact Decimal representation.  Repeated identical ticks are therefore
retained as repeated records.  Acquisition metadata such as fetch time and elapsed
duration is deliberately excluded, so two fetches of the same source response compare
equal.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import cast
from zoneinfo import ZoneInfo

PLAN_SCHEMA_VERSION = 1
SOURCE_VIABILITY_PURPOSE = "source_viability_not_gate_evidence"
FX_SESSION_ZONE_NAME = "America/New_York"
FX_SESSION_ZONE = ZoneInfo(FX_SESSION_ZONE_NAME)
FX_SESSION_OPEN = time(17)
FX_SESSION_WEEKDAYS = frozenset({6, 0, 1, 2, 3})  # Sunday through Thursday.

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
CANONICAL_TICK_HEADER = b"tradebot.source-ticks.semantic.v1\n"
_DATASET_HASH_DOMAIN = b"tradebot.acquisition-dataset.v1\n"


def _nonempty(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be str")
    if not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value


def _identifier(value: str, field: str) -> str:
    value = _nonempty(value, field)
    if _ID.fullmatch(value) is None:
        raise ValueError(f"{field} must contain only letters, digits, '.', '_' or '-' ")
    return value


def _exact_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field} must be int")
    return value


def _finite_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")
    return value


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be UTC-aware")
    normalized = value.astimezone(UTC)
    if normalized.microsecond % 1000:
        raise ValueError(f"{field} must be aligned to millisecond source precision")
    return normalized


def _epoch_milliseconds(value: datetime) -> int:
    delta = value - _EPOCH
    return ((delta.days * 86_400 + delta.seconds) * 1000) + delta.microseconds // 1000


def _iso_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def is_fx_session_date(session_date: date) -> bool:
    """Return whether *session_date* opens an ordinary Sunday--Thursday FX session."""
    if type(session_date) is not date:
        raise TypeError("session_date must be date")
    return session_date.weekday() in FX_SESSION_WEEKDAYS


def fx_session_bounds(session_date: date) -> tuple[datetime, datetime]:
    """Return the half-open 17:00-New-York session identified by its open date.

    Each boundary is converted independently through the IANA timezone.  No fixed UTC
    offset or assumed 24-hour duration is used.
    """
    if not is_fx_session_date(session_date):
        raise ValueError(f"{session_date.isoformat()} is not a Sunday-through-Thursday session")
    local_open = datetime.combine(session_date, FX_SESSION_OPEN, tzinfo=FX_SESSION_ZONE)
    local_close = datetime.combine(
        session_date + timedelta(days=1), FX_SESSION_OPEN, tzinfo=FX_SESSION_ZONE
    )
    return local_open.astimezone(UTC), local_close.astimezone(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class SymbolSpec:
    """One logical instrument mapped to one exact broker symbol."""

    logical: str
    broker: str

    def __post_init__(self) -> None:
        _identifier(self.logical, "logical symbol")
        _identifier(self.broker, "broker symbol")


@dataclass(frozen=True, slots=True, kw_only=True)
class AcquisitionWindow:
    """A requested span expressed in FX session-open dates, end exclusive."""

    id: str
    purpose: str
    start_session_date: date
    end_session_date_exclusive: date

    def __post_init__(self) -> None:
        _identifier(self.id, "window id")
        _nonempty(self.purpose, "window purpose")
        if type(self.start_session_date) is not date:
            raise TypeError("start_session_date must be date")
        if type(self.end_session_date_exclusive) is not date:
            raise TypeError("end_session_date_exclusive must be date")
        if self.start_session_date >= self.end_session_date_exclusive:
            raise ValueError("window start_session_date must precede its exclusive end")
        if not any(self.iter_session_dates()):
            raise ValueError(f"window {self.id!r} contains no Sunday-through-Thursday session")

    def iter_session_dates(self) -> tuple[date, ...]:
        """Return tradable session-open dates; Friday and Saturday remain closures."""
        days: list[date] = []
        cursor = self.start_session_date
        while cursor < self.end_session_date_exclusive:
            if is_fx_session_date(cursor):
                days.append(cursor)
            cursor += timedelta(days=1)
        return tuple(days)


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkRequest:
    """One exact-symbol, one-session, UTC half-open acquisition request."""

    logical_symbol: str
    broker_symbol: str
    window_id: str
    session_date: date
    index_in_window: int
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _identifier(self.logical_symbol, "logical symbol")
        _identifier(self.broker_symbol, "broker symbol")
        _identifier(self.window_id, "window id")
        if type(self.session_date) is not date:
            raise TypeError("session_date must be date")
        if type(self.index_in_window) is not int:
            raise TypeError("index_in_window must be int")
        if self.index_in_window < 0:
            raise ValueError("index_in_window cannot be negative")
        start = _utc(self.start, "chunk start")
        end = _utc(self.end, "chunk end")
        expected_start, expected_end = fx_session_bounds(self.session_date)
        if (start, end) != (expected_start, expected_end):
            raise ValueError("chunk bounds must be the session date's 17:00 New York boundaries")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def chunk_id(self) -> str:
        """Return the stable, path-like identity used in manifests and hashes."""
        return f"{self.logical_symbol}/{self.window_id}/{self.session_date.isoformat()}"


@dataclass(frozen=True, slots=True, kw_only=True)
class AcquisitionPlan:
    """Strict source-viability plan parsed from the user-visible JSON contract."""

    schema_version: int
    probe_id: str
    source: str
    symbols: tuple[SymbolSpec, ...]
    repeat_fetches: int
    chunk_sessions: int
    purpose: str
    windows: tuple[AcquisitionWindow, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != PLAN_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {PLAN_SCHEMA_VERSION}")
        _identifier(self.probe_id, "probe_id")
        _identifier(self.source, "source")
        if type(self.symbols) is not tuple or not self.symbols:
            raise ValueError("symbols must be a non-empty tuple")
        if tuple(sorted(self.symbols, key=lambda item: item.logical)) != self.symbols:
            raise ValueError("symbols must be in logical-symbol order")
        if len({item.logical for item in self.symbols}) != len(self.symbols):
            raise ValueError("logical symbols must be unique")
        if len({item.broker for item in self.symbols}) != len(self.symbols):
            raise ValueError("exact broker symbols must be unique")
        if type(self.repeat_fetches) is not int or self.repeat_fetches < 2:
            raise ValueError("repeat_fetches must be at least 2")
        if type(self.chunk_sessions) is not int or self.chunk_sessions != 1:
            raise ValueError("chunk_sessions must be exactly 1")
        if self.purpose != SOURCE_VIABILITY_PURPOSE:
            raise ValueError(f"purpose must be {SOURCE_VIABILITY_PURPOSE!r}")
        if type(self.windows) is not tuple or not self.windows:
            raise ValueError("windows must be a non-empty tuple")
        ordered = tuple(sorted(self.windows, key=lambda item: (item.start_session_date, item.id)))
        if ordered != self.windows:
            raise ValueError("windows must be ordered by start_session_date then id")
        if len({window.id for window in self.windows}) != len(self.windows):
            raise ValueError("window ids must be unique")
        for previous, current in zip(self.windows, self.windows[1:], strict=False):
            if current.start_session_date < previous.end_session_date_exclusive:
                raise ValueError(f"windows {previous.id!r} and {current.id!r} overlap")

    @property
    def chunks(self) -> tuple[ChunkRequest, ...]:
        """Return requests in stable logical-symbol/window/session order."""
        chunks: list[ChunkRequest] = []
        for symbol in self.symbols:
            for window in self.windows:
                for index, session_date in enumerate(window.iter_session_dates()):
                    start, end = fx_session_bounds(session_date)
                    chunks.append(
                        ChunkRequest(
                            logical_symbol=symbol.logical,
                            broker_symbol=symbol.broker,
                            window_id=window.id,
                            session_date=session_date,
                            index_in_window=index,
                            start=start,
                            end=end,
                        )
                    )
        return tuple(chunks)

    @property
    def plan_hash(self) -> str:
        """Return SHA-256 of the canonical plan, including closure dates."""
        return plan_hash(self)


_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "probe_id",
        "source",
        "symbols",
        "repeat_fetches",
        "chunk_sessions",
        "purpose",
        "windows",
    }
)
_WINDOW_KEYS = frozenset({"id", "purpose", "start_session_date", "end_session_date_exclusive"})


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{field} keys must be strings")
    return cast(Mapping[str, object], value)


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], field: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise ValueError(f"{field} keys invalid; missing={missing}, unknown={unknown}")


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be str")
    return value


def _date(value: object, field: str) -> date:
    text = _string(value, field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    if text != parsed.isoformat():
        raise ValueError(f"{field} must use canonical YYYY-MM-DD form")
    return parsed


def parse_plan(payload: Mapping[str, object]) -> AcquisitionPlan:
    """Parse the strict JSON-shaped acquisition plan; unknown keys fail closed."""
    root = _mapping(payload, "plan")
    _exact_keys(root, _PLAN_KEYS, "plan")

    symbols_value = _mapping(root["symbols"], "symbols")
    if not symbols_value:
        raise ValueError("symbols must be non-empty")
    symbols = tuple(
        sorted(
            (
                SymbolSpec(
                    logical=_identifier(logical, "logical symbol"),
                    broker=_string(broker, "broker symbol"),
                )
                for logical, broker in symbols_value.items()
            ),
            key=lambda item: item.logical,
        )
    )

    windows_value = root["windows"]
    if type(windows_value) is not list:
        raise TypeError("windows must be a list")
    windows: list[AcquisitionWindow] = []
    for index, raw_window in enumerate(cast(list[object], windows_value)):
        window = _mapping(raw_window, f"windows[{index}]")
        _exact_keys(window, _WINDOW_KEYS, f"windows[{index}]")
        windows.append(
            AcquisitionWindow(
                id=_string(window["id"], f"windows[{index}].id"),
                purpose=_string(window["purpose"], f"windows[{index}].purpose"),
                start_session_date=_date(
                    window["start_session_date"], f"windows[{index}].start_session_date"
                ),
                end_session_date_exclusive=_date(
                    window["end_session_date_exclusive"],
                    f"windows[{index}].end_session_date_exclusive",
                ),
            )
        )

    return AcquisitionPlan(
        schema_version=_exact_int(root["schema_version"], "schema_version"),
        probe_id=_string(root["probe_id"], "probe_id"),
        source=_string(root["source"], "source"),
        symbols=symbols,
        repeat_fetches=_exact_int(root["repeat_fetches"], "repeat_fetches"),
        chunk_sessions=_exact_int(root["chunk_sessions"], "chunk_sessions"),
        purpose=_string(root["purpose"], "purpose"),
        windows=tuple(windows),
    )


def plan_hash(plan: AcquisitionPlan) -> str:
    """Return the stable hash of *plan* independent of JSON mapping insertion order."""
    payload = {
        "schema_version": plan.schema_version,
        "probe_id": plan.probe_id,
        "source": plan.source,
        "symbols": {item.logical: item.broker for item in plan.symbols},
        "repeat_fetches": plan.repeat_fetches,
        "chunk_sessions": plan.chunk_sessions,
        "purpose": plan.purpose,
        "session_timezone": FX_SESSION_ZONE_NAME,
        "session_open": FX_SESSION_OPEN.strftime("%H:%M"),
        "windows": [
            {
                "id": window.id,
                "purpose": window.purpose,
                "start_session_date": window.start_session_date.isoformat(),
                "end_session_date_exclusive": window.end_session_date_exclusive.isoformat(),
            }
            for window in plan.windows
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceTick:
    """All semantic fields returned for one MT5 tick, without cleaning or filtering.

    Bad market values are intentionally accepted when they remain finite.  Zero or
    negative sides, locked/crossed quotes and inconsistent second/millisecond stamps
    are evidence for the metrics below, not constructor errors.
    """

    time: int
    time_msc: int
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    flags: int
    volume_real: Decimal

    def __post_init__(self) -> None:
        for field in ("time", "time_msc", "volume", "flags"):
            _exact_int(getattr(self, field), field)
        for field in ("bid", "ask", "last", "volume_real"):
            _finite_decimal(getattr(self, field), field)


def encode_source_tick(tick: SourceTick) -> bytes:
    """Encode one tick as a canonical ASCII line without fetch metadata."""
    fields = (
        str(tick.time),
        str(tick.time_msc),
        _canonical_decimal(tick.bid),
        _canonical_decimal(tick.ask),
        _canonical_decimal(tick.last),
        str(tick.volume),
        str(tick.flags),
        _canonical_decimal(tick.volume_real),
    )
    return ("\t".join(fields) + "\n").encode("ascii")


def canonical_tick_lines(ticks: Sequence[SourceTick]) -> Iterator[bytes]:
    """Yield the domain header and canonical rows without joining them in memory."""
    yield CANONICAL_TICK_HEADER
    for tick in ticks:
        yield encode_source_tick(tick)


def canonical_tick_bytes(ticks: Sequence[SourceTick]) -> bytes:
    """Return canonical bytes for small callers; persistence should stream the lines."""
    return b"".join(canonical_tick_lines(ticks))


def semantic_tick_sha256(ticks: Sequence[SourceTick]) -> str:
    """Hash all semantic source fields in their returned order."""
    digest = hashlib.sha256()
    for line in canonical_tick_lines(ticks):
        digest.update(line)
    return digest.hexdigest()


def _nearest_rank(values: Sequence[int], quantile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _nearest_rank_decimal(values: Sequence[Decimal], quantile: float) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _canonical_optional_decimal(value: Decimal | None) -> str | None:
    return None if value is None else _canonical_decimal(value)


def _nearest_rank_histogram(counts: Mapping[Decimal, int], quantile: float) -> Decimal | None:
    total = sum(counts.values())
    if total == 0:
        return None
    target = max(1, math.ceil(quantile * total))
    cumulative = 0
    for value, count in sorted(counts.items()):
        cumulative += count
        if cumulative >= target:
            return value
    raise RuntimeError("spread histogram count changed while it was read")


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkMetrics:
    """Descriptive observations for one completed request; no acceptance threshold.

    ``active_minutes`` counts UTC minute buckets containing at least one tick.  Spread
    values are exact canonical Decimal strings (plain notation, no insignificant
    trailing zeroes), so ``dataclasses.asdict`` is directly JSON-serialisable without
    converting financial values through binary float.
    """

    tick_count: int
    active_minutes: int
    earliest_time_msc: int | None
    latest_time_msc: int | None
    leading_silence_milliseconds: int | None
    trailing_silence_milliseconds: int | None
    timestamp_regressions: int
    same_millisecond_transitions: int
    exact_adjacent_duplicates: int
    time_field_mismatches: int
    positive_intertick_gaps: int
    max_intertick_gap_milliseconds: int | None
    p50_intertick_gap_milliseconds: int | None
    p95_intertick_gap_milliseconds: int | None
    p99_intertick_gap_milliseconds: int | None
    both_sides_positive: int
    bid_nonpositive: int
    ask_nonpositive: int
    locked_quotes: int
    crossed_quotes: int
    positive_spread_quotes: int
    positive_spread_min: str | None
    positive_spread_p50: str | None
    positive_spread_p95: str | None
    positive_spread_p99: str | None
    positive_spread_max: str | None
    positive_spread_counts: tuple[tuple[str, int], ...]
    negative_volume: int
    negative_volume_real: int
    flag_counts: tuple[tuple[int, int], ...]
    bid_flag_mask: int | None
    ask_flag_mask: int | None
    bid_update_flagged: int | None
    ask_update_flagged: int | None
    both_update_flagged: int | None
    neither_update_flagged: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkEvidence:
    """Stable content identity and descriptive metrics for one request."""

    request: ChunkRequest
    semantic_sha256: str
    metrics: ChunkMetrics

    @property
    def chunk_id(self) -> str:
        return self.request.chunk_id


def _validate_masks(bid_flag_mask: int | None, ask_flag_mask: int | None) -> None:
    if (bid_flag_mask is None) != (ask_flag_mask is None):
        raise ValueError("bid_flag_mask and ask_flag_mask must be supplied together")
    if bid_flag_mask is None:
        return
    if type(bid_flag_mask) is not int or type(ask_flag_mask) is not int:
        raise TypeError("flag masks must be int")
    if bid_flag_mask <= 0 or ask_flag_mask <= 0:
        raise ValueError("flag masks must be positive")
    if bid_flag_mask & ask_flag_mask:
        raise ValueError("bid and ask flag masks must not overlap")


def analyse_chunk(
    request: ChunkRequest,
    ticks: Sequence[SourceTick],
    *,
    bid_flag_mask: int | None = None,
    ask_flag_mask: int | None = None,
) -> ChunkEvidence:
    """Validate half-open membership and describe all returned source ticks.

    Quote defects are counted and retained in the semantic hash.  Only structural
    response errors (a tick outside the requested half-open interval, or invalid flag
    masks) raise.
    """
    _validate_masks(bid_flag_mask, ask_flag_mask)
    start_msc = _epoch_milliseconds(request.start)
    end_msc = _epoch_milliseconds(request.end)
    outside = [
        (index, tick.time_msc)
        for index, tick in enumerate(ticks)
        if not start_msc <= tick.time_msc < end_msc
    ]
    if outside:
        index, stamp = outside[0]
        raise ValueError(
            f"tick {index} at {stamp} is outside chunk {request.chunk_id}'s "
            f"half-open interval [{start_msc}, {end_msc})"
        )

    stamps = [tick.time_msc for tick in ticks]
    deltas = [current - previous for previous, current in pairwise(stamps)]
    positive_gaps = [delta for delta in deltas if delta > 0]
    earliest = min(stamps, default=None)
    latest = max(stamps, default=None)

    both_sides_positive = 0
    bid_nonpositive = 0
    ask_nonpositive = 0
    locked = 0
    crossed = 0
    positive_spread = 0
    positive_spreads: list[Decimal] = []
    for tick in ticks:
        bid_ok = tick.bid > 0
        ask_ok = tick.ask > 0
        bid_nonpositive += not bid_ok
        ask_nonpositive += not ask_ok
        if bid_ok and ask_ok:
            both_sides_positive += 1
            if tick.ask == tick.bid:
                locked += 1
            elif tick.ask < tick.bid:
                crossed += 1
            else:
                positive_spread += 1
                positive_spreads.append(tick.ask - tick.bid)

    spread_counts = Counter(positive_spreads)

    if bid_flag_mask is None:
        bid_flagged = ask_flagged = both_flagged = neither_flagged = None
    else:
        ask_mask = cast(int, ask_flag_mask)
        bid_flagged = sum(bool(tick.flags & bid_flag_mask) for tick in ticks)
        ask_flagged = sum(bool(tick.flags & ask_mask) for tick in ticks)
        both_flagged = sum(
            bool(tick.flags & bid_flag_mask) and bool(tick.flags & ask_mask) for tick in ticks
        )
        neither_flagged = sum(
            not (tick.flags & bid_flag_mask) and not (tick.flags & ask_mask) for tick in ticks
        )

    metrics = ChunkMetrics(
        tick_count=len(ticks),
        active_minutes=len({stamp // 60_000 for stamp in stamps}),
        earliest_time_msc=earliest,
        latest_time_msc=latest,
        leading_silence_milliseconds=None if earliest is None else earliest - start_msc,
        trailing_silence_milliseconds=None if latest is None else end_msc - latest,
        timestamp_regressions=sum(delta < 0 for delta in deltas),
        same_millisecond_transitions=sum(delta == 0 for delta in deltas),
        exact_adjacent_duplicates=sum(previous == current for previous, current in pairwise(ticks)),
        time_field_mismatches=sum(tick.time != tick.time_msc // 1000 for tick in ticks),
        positive_intertick_gaps=len(positive_gaps),
        max_intertick_gap_milliseconds=max(positive_gaps, default=None),
        p50_intertick_gap_milliseconds=_nearest_rank(positive_gaps, 0.50),
        p95_intertick_gap_milliseconds=_nearest_rank(positive_gaps, 0.95),
        p99_intertick_gap_milliseconds=_nearest_rank(positive_gaps, 0.99),
        both_sides_positive=both_sides_positive,
        bid_nonpositive=bid_nonpositive,
        ask_nonpositive=ask_nonpositive,
        locked_quotes=locked,
        crossed_quotes=crossed,
        positive_spread_quotes=positive_spread,
        positive_spread_min=_canonical_optional_decimal(min(positive_spreads, default=None)),
        positive_spread_p50=_canonical_optional_decimal(
            _nearest_rank_decimal(positive_spreads, 0.50)
        ),
        positive_spread_p95=_canonical_optional_decimal(
            _nearest_rank_decimal(positive_spreads, 0.95)
        ),
        positive_spread_p99=_canonical_optional_decimal(
            _nearest_rank_decimal(positive_spreads, 0.99)
        ),
        positive_spread_max=_canonical_optional_decimal(max(positive_spreads, default=None)),
        positive_spread_counts=tuple(
            (_canonical_decimal(spread), count) for spread, count in sorted(spread_counts.items())
        ),
        negative_volume=sum(tick.volume < 0 for tick in ticks),
        negative_volume_real=sum(tick.volume_real < 0 for tick in ticks),
        flag_counts=tuple(sorted(Counter(tick.flags for tick in ticks).items())),
        bid_flag_mask=bid_flag_mask,
        ask_flag_mask=ask_flag_mask,
        bid_update_flagged=bid_flagged,
        ask_update_flagged=ask_flagged,
        both_update_flagged=both_flagged,
        neither_update_flagged=neither_flagged,
    )
    return ChunkEvidence(
        request=request,
        semantic_sha256=semantic_tick_sha256(ticks),
        metrics=metrics,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RepeatFetchComparison:
    """Exact semantic comparison of two completed fetches for the same request."""

    chunk_id: str
    identical: bool
    first_sha256: str
    second_sha256: str
    first_count: int
    second_count: int
    common_prefix_rows: int
    common_suffix_rows: int
    first_difference_index: int | None


def compare_repeat_fetches(
    request: ChunkRequest,
    first: Sequence[SourceTick],
    second: Sequence[SourceTick],
) -> RepeatFetchComparison:
    """Compare repeated responses without deduplicating repeated quote values."""
    first_evidence = analyse_chunk(request, first)
    second_evidence = analyse_chunk(request, second)
    limit = min(len(first), len(second))
    prefix = 0
    while prefix < limit and first[prefix] == second[prefix]:
        prefix += 1
    identical = len(first) == len(second) and prefix == limit
    suffix = 0
    if not identical:
        while suffix < limit - prefix and first[-1 - suffix] == second[-1 - suffix]:
            suffix += 1
    return RepeatFetchComparison(
        chunk_id=request.chunk_id,
        identical=identical,
        first_sha256=first_evidence.semantic_sha256,
        second_sha256=second_evidence.semantic_sha256,
        first_count=len(first),
        second_count=len(second),
        common_prefix_rows=prefix,
        common_suffix_rows=suffix,
        first_difference_index=None if identical else prefix,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class DatasetSummary:
    """Cross-chunk descriptive summary; ``dataset_sha256`` requires full coverage.

    ``active_minutes`` is the sum of per-chunk instrument-minute observations.  This is
    intentional for a multi-symbol plan: a minute active in both EURUSD and GBPUSD is
    two measured source/instrument minutes, not one market-wide minute.  Spread values
    use the same canonical JSON-safe strings as :class:`ChunkMetrics`.  Cross-session
    gaps are reported separately because they may include scheduled weekend or holiday
    closures and are not, by themselves, outage evidence.
    """

    plan_hash: str
    dataset_sha256: str | None
    complete: bool
    expected_chunks: int
    observed_chunks: int
    missing_chunk_ids: tuple[str, ...]
    empty_chunk_ids: tuple[str, ...]
    total_ticks: int
    active_minutes: int
    earliest_time_msc: int | None
    latest_time_msc: int | None
    timestamp_regressions: int
    same_millisecond_transitions: int
    exact_adjacent_duplicates: int
    time_field_mismatches: int
    max_intrasession_intertick_gap_milliseconds: int | None
    max_cross_session_gap_milliseconds: int | None
    max_observed_intertick_gap_milliseconds: int | None
    both_sides_positive: int
    bid_nonpositive: int
    ask_nonpositive: int
    locked_quotes: int
    crossed_quotes: int
    positive_spread_quotes: int
    positive_spread_min: str | None
    positive_spread_p50: str | None
    positive_spread_p95: str | None
    positive_spread_p99: str | None
    positive_spread_max: str | None
    positive_spread_counts: tuple[tuple[str, int], ...]
    negative_volume: int
    negative_volume_real: int
    flag_counts: tuple[tuple[int, int], ...]
    bid_update_flagged: int | None
    ask_update_flagged: int | None
    both_update_flagged: int | None
    neither_update_flagged: int | None


def _complete_dataset_hash(plan: AcquisitionPlan, ordered: Sequence[ChunkEvidence]) -> str:
    digest = hashlib.sha256()
    digest.update(_DATASET_HASH_DOMAIN)
    digest.update(plan.plan_hash.encode("ascii"))
    digest.update(b"\n")
    for evidence in ordered:
        digest.update(evidence.chunk_id.encode("utf-8"))
        digest.update(b"\t")
        digest.update(evidence.semantic_sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def summarise_dataset(plan: AcquisitionPlan, evidence: Sequence[ChunkEvidence]) -> DatasetSummary:
    """Validate evidence identities, aggregate observations and hash complete corpora.

    Input order is irrelevant.  The plan's stable chunk order controls both aggregation
    and the dataset hash.  Missing work is reported and leaves ``dataset_sha256`` unset;
    an empty *completed* chunk remains valid hash input and appears in ``empty_chunk_ids``.
    """
    expected = {request.chunk_id: request for request in plan.chunks}
    by_id: dict[str, ChunkEvidence] = {}
    for item in evidence:
        if item.chunk_id in by_id:
            raise ValueError(f"duplicate evidence for chunk {item.chunk_id}")
        request = expected.get(item.chunk_id)
        if request is None:
            raise ValueError(f"evidence for unexpected chunk {item.chunk_id}")
        if item.request != request:
            raise ValueError(f"evidence request does not match plan for {item.chunk_id}")
        by_id[item.chunk_id] = item

    missing = tuple(request.chunk_id for request in plan.chunks if request.chunk_id not in by_id)
    ordered = tuple(by_id[request.chunk_id] for request in plan.chunks if request.chunk_id in by_id)
    complete = not missing
    all_metrics = [item.metrics for item in ordered]
    masks = {(metric.bid_flag_mask, metric.ask_flag_mask) for metric in all_metrics}
    if len(masks) > 1:
        raise ValueError("all chunks must use the same bid/ask flag masks")

    earliest_values = [
        metric.earliest_time_msc for metric in all_metrics if metric.earliest_time_msc is not None
    ]
    latest_values = [
        metric.latest_time_msc for metric in all_metrics if metric.latest_time_msc is not None
    ]
    maximum_intrasession_gap = max(
        (
            metric.max_intertick_gap_milliseconds
            for metric in all_metrics
            if metric.max_intertick_gap_milliseconds is not None
        ),
        default=None,
    )
    maximum_cross_session_gap: int | None = None
    cross_regressions = 0
    cross_same_millisecond = 0
    previous_latest: int | None = None
    chain_identity: tuple[str, str] | None = None
    for request in plan.chunks:
        chunk_evidence = by_id.get(request.chunk_id)
        if chunk_evidence is None:
            previous_latest = None
            chain_identity = None
            continue
        current_identity = (request.logical_symbol, request.window_id)
        if current_identity != chain_identity:
            previous_latest = None
            chain_identity = current_identity
        first = chunk_evidence.metrics.earliest_time_msc
        last = chunk_evidence.metrics.latest_time_msc
        if first is not None and previous_latest is not None:
            delta = first - previous_latest
            cross_regressions += delta < 0
            cross_same_millisecond += delta == 0
            if delta > 0:
                maximum_cross_session_gap = (
                    delta
                    if maximum_cross_session_gap is None
                    else max(maximum_cross_session_gap, delta)
                )
        if last is not None:
            previous_latest = last

    flag_counts: Counter[int] = Counter()
    spread_counts: Counter[Decimal] = Counter()
    for metric in all_metrics:
        flag_counts.update(dict(metric.flag_counts))
        spread_counts.update(
            {Decimal(spread): count for spread, count in metric.positive_spread_counts}
        )

    update_counts: tuple[int | None, int | None, int | None, int | None]
    if all_metrics and all(metric.bid_update_flagged is not None for metric in all_metrics):
        update_counts = (
            sum(cast(int, metric.bid_update_flagged) for metric in all_metrics),
            sum(cast(int, metric.ask_update_flagged) for metric in all_metrics),
            sum(cast(int, metric.both_update_flagged) for metric in all_metrics),
            sum(cast(int, metric.neither_update_flagged) for metric in all_metrics),
        )
    else:
        update_counts = (None, None, None, None)

    return DatasetSummary(
        plan_hash=plan.plan_hash,
        dataset_sha256=_complete_dataset_hash(plan, ordered) if complete else None,
        complete=complete,
        expected_chunks=len(expected),
        observed_chunks=len(ordered),
        missing_chunk_ids=missing,
        empty_chunk_ids=tuple(item.chunk_id for item in ordered if item.metrics.tick_count == 0),
        total_ticks=sum(metric.tick_count for metric in all_metrics),
        active_minutes=sum(metric.active_minutes for metric in all_metrics),
        earliest_time_msc=min(earliest_values, default=None),
        latest_time_msc=max(latest_values, default=None),
        timestamp_regressions=(
            sum(metric.timestamp_regressions for metric in all_metrics) + cross_regressions
        ),
        same_millisecond_transitions=(
            sum(metric.same_millisecond_transitions for metric in all_metrics)
            + cross_same_millisecond
        ),
        exact_adjacent_duplicates=sum(metric.exact_adjacent_duplicates for metric in all_metrics),
        time_field_mismatches=sum(metric.time_field_mismatches for metric in all_metrics),
        max_intrasession_intertick_gap_milliseconds=maximum_intrasession_gap,
        max_cross_session_gap_milliseconds=maximum_cross_session_gap,
        max_observed_intertick_gap_milliseconds=max(
            (
                value
                for value in (maximum_intrasession_gap, maximum_cross_session_gap)
                if value is not None
            ),
            default=None,
        ),
        both_sides_positive=sum(metric.both_sides_positive for metric in all_metrics),
        bid_nonpositive=sum(metric.bid_nonpositive for metric in all_metrics),
        ask_nonpositive=sum(metric.ask_nonpositive for metric in all_metrics),
        locked_quotes=sum(metric.locked_quotes for metric in all_metrics),
        crossed_quotes=sum(metric.crossed_quotes for metric in all_metrics),
        positive_spread_quotes=sum(metric.positive_spread_quotes for metric in all_metrics),
        positive_spread_min=_canonical_optional_decimal(min(spread_counts, default=None)),
        positive_spread_p50=_canonical_optional_decimal(
            _nearest_rank_histogram(spread_counts, 0.50)
        ),
        positive_spread_p95=_canonical_optional_decimal(
            _nearest_rank_histogram(spread_counts, 0.95)
        ),
        positive_spread_p99=_canonical_optional_decimal(
            _nearest_rank_histogram(spread_counts, 0.99)
        ),
        positive_spread_max=_canonical_optional_decimal(max(spread_counts, default=None)),
        positive_spread_counts=tuple(
            (_canonical_decimal(spread), count) for spread, count in sorted(spread_counts.items())
        ),
        negative_volume=sum(metric.negative_volume for metric in all_metrics),
        negative_volume_real=sum(metric.negative_volume_real for metric in all_metrics),
        flag_counts=tuple(sorted(flag_counts.items())),
        bid_update_flagged=update_counts[0],
        ask_update_flagged=update_counts[1],
        both_update_flagged=update_counts[2],
        neither_update_flagged=update_counts[3],
    )


def dataset_sha256(plan: AcquisitionPlan, evidence: Sequence[ChunkEvidence]) -> str:
    """Return the complete dataset hash, refusing a partial acquisition."""
    summary = summarise_dataset(plan, evidence)
    if summary.dataset_sha256 is None:
        raise ValueError(f"dataset is incomplete; missing chunks: {summary.missing_chunk_ids}")
    return summary.dataset_sha256
