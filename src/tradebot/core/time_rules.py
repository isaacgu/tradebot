"""DST-aware session and FX trading-day rules."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from importlib.resources import files
from typing import Literal
from zoneinfo import ZoneInfo

from tradebot.core.errors import AmbiguousLocalTimeError, NonexistentLocalTimeError
from tradebot.core.timestamps import require_utc


@cache
def _bundled_zone(key: str) -> ZoneInfo:
    """Load an IANA zone from the dependency-locked ``tzdata`` wheel."""
    resource = files("tzdata.zoneinfo").joinpath(*key.split("/"))
    with resource.open("rb") as zone_file:
        return ZoneInfo.from_file(zone_file, key=key)


NEW_YORK = _bundled_zone("America/New_York")
FX_BOUNDARY = time(17, 0)


def local_time_utc(
    local_date: date,
    local_time: time,
    zone: str,
    *,
    fold: Literal[0, 1] | None = None,
) -> datetime:
    """Convert a local civil time to UTC, rejecting gaps and unresolved folds."""
    timezone = _bundled_zone(zone)
    naive = datetime.combine(local_date, local_time.replace(tzinfo=None))
    candidates: dict[int, datetime] = {}
    for candidate_fold in (0, 1):
        aware = naive.replace(tzinfo=timezone, fold=candidate_fold)
        round_trip = aware.astimezone(UTC).astimezone(timezone)
        if round_trip.replace(tzinfo=None) == naive and round_trip.fold == candidate_fold:
            candidates[candidate_fold] = aware
    if not candidates:
        raise NonexistentLocalTimeError(f"{naive.isoformat()} does not exist in {zone}")
    unique_instants = {value.astimezone(UTC) for value in candidates.values()}
    if len(unique_instants) > 1 and fold is None:
        raise AmbiguousLocalTimeError(f"{naive.isoformat()} is ambiguous in {zone}; set fold")
    selected_fold = fold if fold is not None else next(iter(candidates))
    if selected_fold not in candidates:
        raise NonexistentLocalTimeError(
            f"fold={selected_fold} is not valid for {naive.isoformat()} in {zone}"
        )
    return candidates[selected_fold].astimezone(UTC)


def is_fx_market_open(ts: datetime) -> bool:
    """Return whether UTC *ts* lies in Sunday 17:00-Friday 17:00 New York time."""
    local = require_utc(ts).astimezone(NEW_YORK)
    weekday = local.weekday()
    if weekday == 5:
        return False
    if weekday == 6:
        return local.timetz().replace(tzinfo=None) >= FX_BOUNDARY
    if weekday == 4:
        return local.timetz().replace(tzinfo=None) < FX_BOUNDARY
    return True


def fx_trading_day_start(ts: datetime) -> datetime:
    """Return the most recent valid FX session-day start at 17:00 New York.

    This answers "which session start most recently preceded *ts*", which for a
    closed-market input is the start of a session that has ALREADY CLOSED. It is
    therefore not a membership test and MUST NOT be used to bucket ticks — use
    :func:`fx_session_bounds`, which returns ``None`` when the market is closed.
    """
    local = require_utc(ts).astimezone(NEW_YORK)
    boundary_date = local.date()
    if local.timetz().replace(tzinfo=None) < FX_BOUNDARY:
        boundary_date -= timedelta(days=1)
    # Session-day starts exist Sunday through Thursday. During the Friday-close
    # weekend, retain the Thursday start of the most recently completed session
    # rather than inventing Friday or Saturday trading intervals.
    while boundary_date.weekday() in (4, 5):
        boundary_date -= timedelta(days=1)
    return local_time_utc(boundary_date, FX_BOUNDARY, "America/New_York")


def fx_session_bounds(ts: datetime) -> tuple[datetime, datetime] | None:
    """Return the half-open ``[start, end)`` FX session containing UTC *ts*, else None.

    This is the ONLY function a bar builder may bucket with. The interval is
    half-open, so a tick stamped exactly at 17:00 New York belongs to the NEW
    session and no instant belongs to two sessions. Returns ``None`` when the
    market is closed, so a caller cannot silently attribute a weekend tick to a
    session that already ended.
    """
    if not is_fx_market_open(ts):
        return None
    start = fx_trading_day_start(ts)
    # The end is the next 17:00 New York in local terms, resolved through zoneinfo
    # rather than by adding 24 hours, so DST transitions inside the session are
    # carried by the zone rather than assumed away.
    next_date = start.astimezone(NEW_YORK).date() + timedelta(days=1)
    end = local_time_utc(next_date, FX_BOUNDARY, "America/New_York")
    return start, end
