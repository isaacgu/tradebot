"""Attributable external identifier helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from tradebot.core.timestamps import require_utc

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _slug(value: str, field: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "", value)
    if not slug:
        raise ValueError(f"{field} must contain an ASCII letter or digit")
    return slug


def _epoch_milliseconds(ts: datetime) -> int:
    delta = require_utc(ts) - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


def new_client_order_id(
    environment: str,
    strategy_id: str,
    instrument: str,
    ts: datetime,
    *,
    run_id: str,
    sequence: int,
) -> str:
    """Return the deterministic idempotency key for one logical order intent."""
    environment_slug = _slug(environment, "environment")
    strategy_slug = _slug(strategy_id, "strategy_id")
    instrument_slug = _slug(instrument, "instrument")
    _slug(run_id, "run_id")
    if type(sequence) is not int or sequence < 0:
        raise ValueError("sequence must be a non-negative integer")
    timestamp = require_utc(ts)
    identity = "\x1f".join(
        (environment, strategy_id, instrument, timestamp.isoformat(), run_id, str(sequence))
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return "-".join(
        (
            environment_slug,
            strategy_slug,
            instrument_slug,
            str(_epoch_milliseconds(timestamp)),
            str(sequence),
            digest,
        )
    )
