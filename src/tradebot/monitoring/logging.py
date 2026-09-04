"""Structured JSON logging configured without secret-bearing config dumps."""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog
from structlog.stdlib import BoundLogger


def configure_json_logging(level: str = "INFO") -> None:
    """Configure process-wide JSON logging at *level*."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def run_logger(*, run_id: str, mode: str, config_hash: str, git_sha: str) -> BoundLogger:
    """Return a logger carrying mandatory run provenance fields."""
    return cast(
        BoundLogger,
        structlog.get_logger("tradebot").bind(
            run_id=run_id,
            mode=mode,
            config_hash=config_hash,
            git_sha=git_sha,
        ),
    )
