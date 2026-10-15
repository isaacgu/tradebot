"""Bounded, read-only display of reference-month diagnostic reports.

Checksums establish saved-report integrity, not source-data validity or approval.
This consumer never opens a corpus, evaluates a strategy, or authorizes training.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, cast

from prometheus_client import CollectorRegistry, Gauge

DEFAULT_REFERENCE_REPORT = "build/gate1/source-clock-reference-acceptance-20260906-v1/report.json"
MAX_REPORT_BYTES = 1024 * 1024
_IDENTITY_PATHS = (
    "docs/SPEC.md",
    "scripts/evaluate_reference_month.py",
    "src/tradebot/data/reference_acceptance.py",
)


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    return cast(dict[str, Any], value)


def _read(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(MAX_REPORT_BYTES + 1)
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("report exceeds display bound")
    return content


def _digest(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid digest")
    return value


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000_000:
        raise ValueError("invalid minute count")
    return value


def _label(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_./-]{1,80}", value) is None:
        raise ValueError("invalid scope label")
    return value


def reference_status(report_path: Path, repository: Path) -> dict[str, Any]:
    """Preserve unavailable/invalid states without inventing zero measurements."""
    status: dict[str, Any] = {
        "report_state": 0,
        "report_path": "outside repository",
        "reason": "report_missing",
        "training_workflow": "NO_FITTING_JOB_IN_THIS_OBSERVER",
        "gate_approval": "not asserted by monitoring",
    }
    try:
        root = repository.resolve()
        path = report_path if report_path.is_absolute() else root / report_path
        path = path.resolve()
        relative = path.relative_to(root).as_posix()
        if len(relative) > 512 or any(ord(char) < 32 for char in relative):
            raise ValueError("invalid report path")
        status["report_path"] = relative
        content = _read(path)
    except FileNotFoundError:
        return status
    except (OSError, ValueError):
        return {**status, "report_state": 3, "reason": "invalid_report_path_or_size"}
    try:
        sidecar = path.with_name(path.stem + ".sha256.json").resolve()
        sidecar.relative_to(root)
        checksum = _object(json.loads(_read(sidecar)))
    except FileNotFoundError:
        return {**status, "report_state": 1, "reason": "checksum_missing"}
    except (OSError, ValueError, TypeError, RecursionError):
        return {**status, "report_state": 3, "reason": "invalid_checksum"}
    try:
        recorded = _object(checksum.get(path.name))
        digest = hashlib.sha256(content).hexdigest()
        if _digest(recorded.get("sha256")) != digest or _count(recorded.get("bytes")) != len(
            content
        ):
            raise ValueError("report checksum mismatch")
        report = _object(json.loads(content))
        if (
            type(report.get("schema_version")) is not int
            or report["schema_version"] != 1
            or report.get("evidence_class") != "REFERENCE_MONTH_ACCEPTANCE_EVALUATION"
            or report.get("gate_approved") is not False
            or report.get("training_enabled") is not False
        ):
            raise ValueError("unsupported diagnostic contract")
        result = _object(report.get("result"))
        state = result.get("status")
        if (
            state not in ("PASSED", "FAILED", "INDETERMINATE")
            or report.get("status") != state
            or result.get("gate_approved") is not False
            or result.get("human_identities_authenticated") is not False
            or type(result.get("approval_binding_verified")) is not bool
            or (state == "PASSED" and not result["approval_binding_verified"])
        ):
            raise ValueError("inconsistent diagnostic status")
        criterion = _object(result.get("criterion"))
        if criterion != {
            "comparison": "STRICTLY_LESS_THAN",
            "denominator": "EXPECTED_LIQUID_MINUTE_BINS",
            "overlap_treatment": "UNION_EACH_MINUTE_COUNTED_ONCE",
            "threshold_fraction": "0.001",
            "threshold_percent": "0.1",
        }:
            raise ValueError("unsupported counting criterion")
        numerator = _count(result.get("counted_flagged_union"))
        denominator = _count(result.get("expected_liquid_minutes"))
        if numerator > denominator:
            raise ValueError("flagged minutes exceed expected minutes")
        measured: dict[str, int | float] = {}
        fraction = result.get("flagged_fraction")
        strict = result.get("strict_less_than_0_1_percent")
        if denominator:
            if not isinstance(fraction, str) or type(strict) is not bool:
                raise ValueError("missing measured fraction")
            with localcontext() as context:
                context.prec = 28
                calculated = Decimal(numerator) / Decimal(denominator)
            if Decimal(fraction) != calculated or strict != (calculated < Decimal("0.001")):
                raise ValueError("fraction contradicts counts")
            if state == "PASSED" and not strict:
                raise ValueError("passing status contradicts threshold")
            measured = {"flagged_fraction": float(calculated), "threshold_satisfied": int(strict)}
        elif fraction is not None or strict is not None or state != "INDETERMINATE":
            raise ValueError("empty denominator cannot establish acceptance")
        stamp = datetime.fromisoformat(str(report.get("generated_at_utc")))
        if stamp.utcoffset() != timedelta(0):
            raise ValueError("report time must be UTC")
        identity = _object(report.get("code_and_spec_sha256"))
        if set(identity) != set(_IDENTITY_PATHS):
            raise ValueError("missing producer identity")
        current = True
        for name in _IDENTITY_PATHS:
            expected = _digest(identity[name])
            source = (root / name).resolve()
            source.relative_to(root)
            matches = hashlib.sha256(_read(source)).hexdigest() == expected
            if name == "docs/SPEC.md" and not matches:
                raise ValueError("report specification differs")
            current &= matches
        scope = _object(result.get("scope"))
        return {
            **status,
            "report_state": 2,
            "reason": "verified_diagnostic_report",
            "report_sha256": digest,
            "report_timestamp_seconds": stamp.timestamp(),
            "report_code_current": int(current),
            "acceptance_status": state,
            "acceptance_state": {"INDETERMINATE": 0, "FAILED": 1, "PASSED": 2}[state],
            "counted_flagged_minutes": numerator,
            "expected_liquid_minutes": denominator,
            "threshold_fraction": 0.001,
            "training_enabled": 0,
            "source": _label(scope.get("source")),
            "instrument": _label(scope.get("instrument")),
            "reference_month": _label(scope.get("reference_month")),
            **measured,
        }
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, RecursionError):
        return {**status, "report_state": 3, "reason": "invalid_diagnostic_report"}


def add_reference_metrics(registry: CollectorRegistry, status: dict[str, Any]) -> None:
    """Export only verified report measurements; missing values stay absent."""
    Gauge(
        "tradebot_reference_report_state",
        "Selected diagnostic report: 0 missing, 1 checksum missing, 2 verified, 3 invalid.",
        ("report_path",),
        registry=registry,
    ).labels(status["report_path"]).set(status["report_state"])
    if status["report_state"] != 2:
        return
    for field in (
        "report_timestamp_seconds",
        "report_code_current",
        "acceptance_state",
        "counted_flagged_minutes",
        "expected_liquid_minutes",
        "threshold_fraction",
        "threshold_satisfied",
        "training_enabled",
    ):
        if field in status:
            Gauge(
                "tradebot_reference_" + field,
                f"Saved diagnostic report {field}; not gate or training approval.",
                registry=registry,
            ).set(status[field])
    if "flagged_fraction" in status:
        Gauge(
            "tradebot_reference_flagged_fraction",
            "Union of flagged expected minutes divided by expected liquid minutes.",
            ("numerator", "denominator"),
            registry=registry,
        ).labels(status["counted_flagged_minutes"], status["expected_liquid_minutes"]).set(
            status["flagged_fraction"]
        )
    labels = ("report_path", "report_sha256", "source", "instrument", "reference_month")
    Gauge(
        "tradebot_reference_report_info",
        "Selected diagnostic report identity; source data are not revalidated by monitoring.",
        labels,
        registry=registry,
    ).labels(*(status[label] for label in labels)).set(1)
