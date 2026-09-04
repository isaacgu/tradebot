"""Timestamp validation shared by core events and clocks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tradebot.core.errors import InvalidTimestampError


def require_utc(value: datetime, *, field: str = "timestamp") -> datetime:
    """Return *value* normalized to UTC, rejecting naïve or non-UTC inputs."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTimestampError(f"{field} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise InvalidTimestampError(f"{field} must be expressed in UTC")
    return value.astimezone(UTC)
