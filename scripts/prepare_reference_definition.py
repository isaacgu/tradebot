"""Prepare an immutable, unapproved October QA definition; never inspect prices.

This is a project expectation proposal, not an FBS schedule, historical vintage,
settlement calendar, training release, or gate approval. Human decisions remain
separate hash-bound inputs to the acceptance evaluator.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import stat
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from tradebot.core.time_rules import local_time_utc
from tradebot.core.timestamps import require_utc
from tradebot.data.calendar import ExpectedLiquidityCalendar, LiquidityDay, LiquidityStatus
from tradebot.data.reference_acceptance import read_policy
from tradebot.data.storage import sha256_path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URLS = {
    "uk_holidays": "https://www.gov.uk/bank-holidays",
    "us_holidays": "https://www.nctreasurer.gov/documents/files/fod/2024-bank-holidays/open",
    "de_holidays": "https://www.gesetze-im-internet.de/einigvtr/art_2.html",
    "mt5_utc_contract": "https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py",
    "fbs_dst_context": "https://fbs.com/news/trading-schedule-changes-due-to-the-winter-time-shift",
}
MAX_SOURCE_BYTES = 4 * 1024 * 1024
BASE_POLICY_SHA256 = "0a7a4e6e732e36b26b68fda52d3c69f45ac53653f52e9319f614aa3f45fa9296"
SPEC_SHA256 = "dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37"
HOLIDAYS = {
    date(2024, 10, 3): "German Unity Day: national German holiday; project QA exclusion only",
    date(2024, 10, 14): "Columbus Day: Federal Reserve holiday; project QA exclusion only",
}
PROPOSALS = {
    "BACKFILLED": (
        "EXCLUDE_FROM_NUMERATOR",
        "Recovery provenance is retained and never establishes freshness; not a price defect. "
        "This proposed numerator exclusion requires explicit human policy review.",
    ),
    "TS_RECV_IMPUTED": (
        "EXCLUDE_FROM_NUMERATOR",
        "SPEC requires historical receipt imputation to event time with this flag. Retain the "
        "receipt-time limitation and provenance; explicit review is required for exclusion.",
    ),
    "GAP_CALENDAR_UNKNOWN": (
        "INDETERMINATE_IF_PRESENT",
        "Unknown expected-liquidity evidence cannot be classified as either clean or defective.",
    ),
    "QUALITY_WARMUP": (
        "INDETERMINATE_IF_PRESENT",
        "Insufficient detector history is not a pass; supply context without waiving the flag.",
    ),
    "PRICE_OUTLIER": (
        "COUNT_AS_FLAGGED",
        "SPEC 4.4 reverting-price-spike annotation counts in ex-post QA only; never rewrite "
        "causal strategy inputs using its future confirmation.",
    ),
}


def _write_json(path: Path, payload: object) -> str:
    encoded = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _directory_identity(path: Path) -> tuple[int, int]:
    observed = path.lstat()
    if not stat.S_ISDIR(observed.st_mode) or path.resolve() != path:
        raise ValueError("definition output directory was replaced or redirected")
    return observed.st_dev, observed.st_ino


def _check_directory(path: Path, identity: tuple[int, int]) -> None:
    if _directory_identity(path) != identity:
        raise ValueError("definition output directory identity changed")


def build_calendar(prepared_at: datetime) -> ExpectedLiquidityCalendar:
    """Materialize only this proposed scope, with actual preparation availability.

    CLOSED means zero project-QA expected minutes, NOT that FBS stopped quoting.
    FULL means the complete proposed 13:00--16:30 London window, NOT 24h liquidity.
    The 30-day expiry is the draft's review lifetime, not a live trading parameter.
    """
    prepared_at = require_utc(prepared_at, field="prepared_at")
    rows: list[LiquidityDay] = []
    day = date(2024, 9, 29)
    while day <= date(2024, 11, 1):
        excluded = day.weekday() >= 5 or day in HOLIDAYS
        intervals = (
            ()
            if excluded
            else (
                (
                    local_time_utc(day, time(13), "Europe/London"),
                    local_time_utc(day, time(16, 30), "Europe/London"),
                ),
            )
        )
        reason = HOLIDAYS.get(day, "weekend" if excluded else "complete proposed overlap window")
        rows.append(
            LiquidityDay(
                instrument="FBS-Demo/EURUSD",
                session_date=day,
                status=LiquidityStatus.CLOSED if excluded else LiquidityStatus.FULL,
                source="PROJECT_QA_EXPECTATION_DRAFT_NOT_BROKER_SCHEDULE",
                source_citation=(
                    "docs/reports/reference_definition_proposal.md; "
                    "docs/SPEC.md sections 2.1--2.4; "
                    f"{SOURCE_URLS['uk_holidays']}; {SOURCE_URLS['us_holidays']}; "
                    f"{SOURCE_URLS['de_holidays']}; rationale={reason}"
                ),
                effective_at=prepared_at,
                available_at=prepared_at,
                valid_until=prepared_at + timedelta(days=30),
                expected_intervals=intervals,
            )
        )
        day += timedelta(days=1)
    return ExpectedLiquidityCalendar(rows)


def _require_source_origin(candidate: str, original: str) -> None:
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or parsed.hostname != urlsplit(original).hostname
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("source redirected outside its identified HTTPS origin")


class _SourceRedirectHandler(HTTPRedirectHandler):
    """Check each resolved Location before urllib can request its destination."""

    def __init__(self, original: str) -> None:
        self._original = original

    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Request | None:
        _require_source_origin(newurl, self._original)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def capture_source(url: str, directory: Path) -> dict[str, object]:
    """Capture bounded bytes from an identified primary source, not an approval."""
    if url not in SOURCE_URLS.values():
        raise ValueError("source is outside the fixed HTTPS primary-source allowlist")
    directory_identity = _directory_identity(directory)
    request = Request(url, headers={"User-Agent": "tradebot-definition-review/1.0"})  # noqa: S310
    opener = build_opener(_SourceRedirectHandler(url))
    with opener.open(request, timeout=30) as response:
        final = response.geturl()
        _require_source_origin(final, url)
        if response.status != 200:
            raise ValueError(f"source returned HTTP {response.status}")
        body = response.read(MAX_SOURCE_BYTES + 1)
        if len(body) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds the bounded capture size")
        retrieved = datetime.now(UTC)
        digest = hashlib.sha256(body).hexdigest()
        extension = ".pdf" if body.startswith(b"%PDF-") else ".html"
        path = directory / (digest + extension)
        _check_directory(directory, directory_identity)
        with path.open("xb") as stream:
            stream.write(body)
        return {
            "url": url,
            "final_url": final,
            "status": "CAPTURED_NOT_ADJUDICATED",
            "retrieved_at_utc": retrieved.isoformat(),
            "sha256": digest,
            "snapshot": path.name,
            "bytes": len(body),
            "content_type": response.headers.get("Content-Type"),
            "historical_available_at": None,
        }


def prepare(output: Path, *, capture_sources: bool = False) -> dict[str, Any]:
    output = output.resolve()
    build_root = (ROOT / "build").resolve()
    if build_root != ROOT.resolve() / "build" or not output.is_relative_to(build_root):
        raise ValueError("proposal output must be a new directory under build/")
    spec_path = ROOT / "docs/SPEC.md"
    if sha256_path(spec_path) != SPEC_SHA256:
        raise ValueError("frozen SPEC does not match the reviewed definition")
    base = ROOT / "configs/calendars/reference_month_policy_draft.json"
    base_bytes = base.read_bytes()
    if hashlib.sha256(base_bytes).hexdigest() != BASE_POLICY_SHA256:
        raise ValueError("base policy changed; review before proposing a new definition")
    generator_hash = sha256_path(Path(__file__))
    description = ROOT / "docs/reports/reference_definition_proposal.md"
    description_hash = sha256_path(description)
    lock_path = ROOT / "uv.lock"
    lock_hash = sha256_path(lock_path)
    output.mkdir(parents=True, exist_ok=False)
    source_dir = output / "sources"
    source_dir.mkdir()
    output_identity = _directory_identity(output)
    source_identity = _directory_identity(source_dir)
    sources: dict[str, object] = {}
    for name, url in SOURCE_URLS.items():
        if not capture_sources:
            sources[name] = {"url": url, "status": "NOT_CAPTURED"}
            continue
        try:
            sources[name] = capture_source(url, source_dir)
        except (OSError, ValueError) as exc:
            sources[name] = {"url": url, "status": "UNAVAILABLE", "reason": str(exc)}
    prepared_at = datetime.now(UTC)
    pinned_inputs = {
        spec_path: SPEC_SHA256,
        base: BASE_POLICY_SHA256,
        Path(__file__): generator_hash,
        description: description_hash,
        lock_path: lock_hash,
    }
    if any(sha256_path(path) != expected for path, expected in pinned_inputs.items()):
        raise ValueError("definition inputs changed during preparation")
    _check_directory(output, output_identity)
    _check_directory(source_dir, source_identity)
    calendar = build_calendar(prepared_at)
    calendar.write(output / "calendar-draft.json")
    policy = json.loads(base_bytes)
    policy["status"] = "DRAFT_UNAPPROVED"
    policy["policy_id"] = "gate1-eurusd-2024-10-counting-proposal-v1"
    policy["criterion"]["missing_expected_bar_treatment"] = "COUNT_AS_FLAGGED"
    for rule in policy["flag_rules"]:
        if rule["name"] in PROPOSALS:
            rule["treatment"], rule["rationale"] = PROPOSALS[rule["name"]]
    _write_json(output / "policy-draft.json", policy)
    read_policy(output / "policy-draft.json")
    with (output / "calendar-review.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("close_date", "role", "qa_status", "expected_utc_intervals", "reason"))
        for row in calendar.entries:
            writer.writerow(
                (
                    row.session_date.isoformat(),
                    "TARGET" if row.session_date.strftime("%Y-%m") == "2024-10" else "CONTEXT",
                    row.status.value,
                    ";".join(
                        f"{start.isoformat()}/{end.isoformat()}"
                        for start, end in row.expected_intervals
                    ),
                    HOLIDAYS.get(
                        row.session_date,
                        "weekend" if row.session_date.weekday() >= 5 else "proposed overlap",
                    ),
                )
            )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": "DRAFT_REQUIRES_HUMAN_REVIEW",
        "prepared_at_utc": prepared_at.isoformat(),
        "scope": policy["scope"],
        "spec_sha256": SPEC_SHA256,
        "base_policy_sha256": BASE_POLICY_SHA256,
        "generator_sha256": generator_hash,
        "description_sha256": description_hash,
        "uv_lock_sha256": lock_hash,
        "timezone_provider": "dependency-locked tzdata wheel via local_time_utc",
        "tzdata_version": version("tzdata"),
        "sources": sources,
        "artifacts": {
            name: sha256_path(output / name)
            for name in (
                "calendar-draft.json",
                "policy-draft.json",
                "calendar-review.csv",
            )
        },
        "expected_october_minutes_under_proposal": sum(
            int(row.expected_seconds / 60)
            for row in calendar.entries
            if row.session_date.strftime("%Y-%m") == "2024-10"
        ),
        "price_data_read": False,
        "acceptance_rate_computed": False,
        "human_approvals": [],
        "gate_approved": False,
        "training_enabled": False,
        "execution_enabled": False,
        "limitations": [
            "Project QA expectations are proposed choices, not guaranteed FBS quoting hours.",
            "CLOSED refers only to this QA window; it does not assert an FX-market closure.",
            "Holiday facts alone do not prove an exact liquidity interval.",
            "Source retrieval is current knowledge, not evidence available to a 2024 strategy.",
            "Earlier corpus diagnostics were observed; this is not untouched preregistration.",
            "No outcome-based window selection, timestamp correction or threshold relaxation.",
            "This calendar cannot settle broker D1, settlement or full-source viability.",
            "A successful source download is not factual adjudication or human approval.",
        ],
    }
    if any(sha256_path(path) != expected for path, expected in pinned_inputs.items()):
        raise ValueError("definition inputs changed before manifest publication")
    _check_directory(output, output_identity)
    _check_directory(source_dir, source_identity)
    for name, expected in manifest["artifacts"].items():
        if sha256_path(output / name) != expected:
            raise ValueError("definition artifact changed before publication")
    for captured in sources.values():
        if isinstance(captured, dict) and captured.get("status") == "CAPTURED_NOT_ADJUDICATED":
            if sha256_path(source_dir / captured["snapshot"]) != captured["sha256"]:
                raise ValueError("captured source changed before publication")
    _write_json(output / "proposal.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--capture-sources", action="store_true")
    args = parser.parse_args(argv)
    report = prepare(args.output_dir, capture_sources=args.capture_sources)
    print(json.dumps({"status": report["status"], "artifacts": report["artifacts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
