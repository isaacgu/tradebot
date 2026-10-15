"""Extraction and causal import checks; snippets here are explicitly test fixtures."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def validator() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "validate_gate1_calendar.py"
    spec = importlib.util.spec_from_file_location("validate_gate1_calendar", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FIXTURE_HTML = b"""<html><body><p>November 07, 2024</p>
<h3>Federal Reserve issues FOMC statement</h3><p>For release at 2:00 p.m. EST</p>
<p>The Committee decided to lower the target range for the federal funds rate by
1/4 percentage point to 4-1/2 to 4-3/4 percent.</p></body></html>"""


def test_source_parser_derives_exact_rate_fields_and_zone_conversion(validator: ModuleType) -> None:
    parsed = validator.parse_fed_release(FIXTURE_HTML)
    assert parsed.declared_release_at == datetime(2024, 11, 7, 19, tzinfo=UTC)
    assert parsed.lower_percent == Decimal("4.5")
    assert parsed.upper_percent == Decimal("4.75")
    assert parsed.change_percentage_points == Decimal("-0.25")


@pytest.mark.parametrize(
    "original,replacement",
    [
        (b"2:00 p.m. EST", b"time unavailable"),
        (b"4-1/2 to 4-3/4", b"5-1/2 to 4-3/4"),
        (b"November 07, 2024", b"July 07, 2024"),
    ],
)
def test_source_format_or_identity_drift_is_rejected(
    validator: ModuleType,
    original: bytes,
    replacement: bytes,
) -> None:
    with pytest.raises(ValueError):
        validator.parse_fed_release(FIXTURE_HTML.replace(original, replacement))


def test_real_import_validation_never_backdates_fixture_availability(
    validator: ModuleType,
    tmp_path: Path,
) -> None:
    observed = datetime(2026, 9, 4, 20, tzinfo=UTC)
    parsed = validator.parse_fed_release(FIXTURE_HTML)
    report = validator.validate_import(parsed, observed, tmp_path)
    assert report["all_checks_passed"]
    assert report["known_at_declared_release"] == []
    assert len(report["fields"]) == 4
    assert all(row["available_at"] == observed.isoformat() for row in report["fields"])
    assert all("AS_OF_UNVERIFIED" in row["quality_flags"] for row in report["fields"])
    assert report["reopened_store_verified"]


def test_synthetic_revision_check_is_separately_labelled(
    validator: ModuleType, tmp_path: Path
) -> None:
    result = validator.validate_synthetic_revisions(tmp_path)
    assert result["source_backed"] is False
    assert result["all_checks_passed"]
    assert result["first_print_visible_before_revision"]


def test_offline_replay_checks_source_hash_and_uses_recorded_receipt(
    validator: ModuleType,
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "build" / "source.html"
    snapshot.parent.mkdir()
    snapshot.write_bytes(FIXTURE_HTML)
    source = {
        "url": validator.FED_URL,
        "snapshot_path": "build/source.html",
        "sha256": hashlib.sha256(FIXTURE_HTML).hexdigest(),
        "retrieved_at_utc": "2026-09-04T20:00:00+00:00",
    }
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"source": source}))
    body, metadata = validator.load_captured_source(report, tmp_path)
    assert body == FIXTURE_HTML
    assert metadata["retrieved_at_utc"] == source["retrieved_at_utc"]
    snapshot.write_bytes(FIXTURE_HTML + b"modified")
    with pytest.raises(ValueError, match="hash"):
        validator.load_captured_source(report, tmp_path)
    source["snapshot_path"] = "../outside.html"
    report.write_text(json.dumps({"source": source}))
    with pytest.raises(ValueError, match="storage"):
        validator.load_captured_source(report, tmp_path)
