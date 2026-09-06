"""Capture an official Fed release and exercise the actual calendar import/store/query path.

This is a bounded integration example, not a historical vintage archive or a
complete Gate-1 calendar. An archive page's declared release time is retained as
data; the newly retrieved fields are available only at this run's receipt time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from tradebot.data.calendar import CalendarFieldVintage, CalendarStore, historical_field

FED_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20241107a.htm"
FBS_OCTOBER_URL = "https://fbs.com/news/trading-schedule-changes-in-october"
FBS_CONDITIONS_URL = "https://fbs.com/trading/conditions?euRedirect=true"
FBS_HOURS_URL = "https://fbs.com/trading/trading-hours?lang=en"
SOURCE_URLS = (FED_URL, FBS_OCTOBER_URL, FBS_CONDITIONS_URL, FBS_HOURS_URL)
MAX_SOURCE_BYTES = 4 * 1024 * 1024


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._hidden = max(0, self._hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def visible_text(payload: bytes) -> str:
    parser = _VisibleText()
    parser.feed(payload.decode("utf-8-sig"))
    return " ".join(" ".join(parser.parts).split())


@dataclass(frozen=True, slots=True)
class FedRelease:
    declared_release_at: datetime
    lower_percent: Decimal
    upper_percent: Decimal
    change_percentage_points: Decimal


def _fraction(text: str) -> Decimal:
    whole, fraction = text.split("-", maxsplit=1) if "-" in text else ("0", text)
    if "/" in fraction:
        numerator, denominator = fraction.split("/")
        return Decimal(whole) + Decimal(numerator) / Decimal(denominator)
    return Decimal(text)


def parse_fed_release(payload: bytes) -> FedRelease:
    """Extract numeric facts from this identified release; source drift fails closed."""
    text = visible_text(payload)
    if "Federal Reserve issues FOMC statement" not in text or "November 07, 2024" not in text:
        raise ValueError("source is not the identified November 7, 2024 FOMC release")
    release = re.search(r"For release at (\d{1,2}):(\d{2}) (a|p)\.m\. (EST|EDT)", text)
    change = re.search(
        r"decided to (lower|raise) the target range for the federal funds rate by "
        r"([\d/-]+) percentage points? to ([\d/-]+) to ([\d/-]+) percent",
        text,
    )
    if release is None or change is None:
        raise ValueError("source release-time or rate-range format changed; manual review required")
    hour = int(release[1]) % 12 + (12 if release[3] == "p" else 0)
    local = datetime(2024, 11, 7, hour, int(release[2]), tzinfo=ZoneInfo("America/New_York"))
    if local.tzname() != release[4]:
        raise ValueError("source timezone abbreviation disagrees with historical zone rules")
    lower, upper = _fraction(change[3]), _fraction(change[4])
    if not Decimal(0) <= lower < upper:
        raise ValueError("source target-rate range is invalid")
    magnitude = _fraction(change[2])
    return FedRelease(
        local.astimezone(UTC),
        lower,
        upper,
        -magnitude if change[1] == "lower" else magnitude,
    )


def _value(item: CalendarFieldVintage) -> str | int | bool | None:
    if isinstance(item.value, datetime):
        return item.value.isoformat()
    if isinstance(item.value, Decimal):
        return str(item.value)
    return item.value


def validate_import(
    release: FedRelease, retrieved_at: datetime, work_dir: Path
) -> dict[str, object]:
    """Import real extracted fields, close the database, reopen and query each cutoff."""
    values: tuple[tuple[str, datetime | Decimal], ...] = (
        ("declared_release_at", release.declared_release_at),
        ("target_rate_lower_percent", release.lower_percent),
        ("target_rate_upper_percent", release.upper_percent),
        ("target_rate_change_percentage_points", release.change_percentage_points),
    )
    events = tuple(
        historical_field(
            source="federalreserve.gov",
            record_id="FOMC-2024-11-07",
            field=field,
            vintage="retrieved-source-snapshot",
            seq=seq,
            value=value,
            ts_event=release.declared_release_at,
            retrieved_at=retrieved_at,
            source_citation=FED_URL,
            archived_available_at=None,
        )
        for seq, (field, value) in enumerate(values, start=1)
    )
    path = work_dir / "official-calendar.sqlite3"
    with CalendarStore(path) as store:
        for event in reversed(events):
            store.append(event)
    checks: list[dict[str, object]] = []
    with CalendarStore(path) as store:
        for event in events:
            before = {
                item.field: item
                for item in store.known_at(event.available_at - timedelta(microseconds=1))
            }
            at = {item.field: item for item in store.known_at(event.available_at)}
            after = {
                item.field: item
                for item in store.known_at(event.available_at + timedelta(microseconds=1))
            }
            checks.append(
                {
                    "field": event.field,
                    "absent_before_available_at": event.field not in before,
                    "exact_value_visible_at_available_at": at.get(event.field) == event,
                    "exact_value_visible_after_available_at": after.get(event.field) == event,
                }
            )
        historical = [_value(item) for item in store.known_at(release.declared_release_at)]
        reopened = store.history() == events
    fields = [
        {
            "record_id": event.record_id,
            "field": event.field,
            "vintage": event.vintage,
            "value": _value(event),
            "ts_event": event.ts_event.isoformat(),
            "ts_recv": event.ts_recv.isoformat(),
            "available_at": event.available_at.isoformat(),
            "ingested_at": retrieved_at.isoformat(),
            "source_citation": event.source_citation,
            "quality_flags": list(event.quality_flags),
        }
        for event in events
    ]
    passed = (
        reopened
        and historical == []
        and all(
            row["absent_before_available_at"]
            and row["exact_value_visible_at_available_at"]
            and row["exact_value_visible_after_available_at"]
            for row in checks
        )
    )
    return {
        "source_backed": True,
        "fields": fields,
        "field_cutoff_checks": checks,
        "known_at_declared_release": historical,
        "reopened_store_verified": reopened,
        "all_checks_passed": passed,
        "historical_as_of_status": "UNPROVEN_RETRIEVAL_AVAILABILITY_ONLY",
        "database_file": path.name,
    }


def validate_synthetic_revisions(work_dir: Path) -> dict[str, object]:
    """Separate test data prove revision semantics without inventing official vintages."""
    first_at = datetime(2024, 11, 7, 19, tzinfo=UTC)
    revision_at = first_at + timedelta(days=1)
    first = CalendarFieldVintage(
        source="synthetic-validation-only",
        record_id="not-an-official-release",
        field="actual",
        vintage="first",
        seq=1,
        value=Decimal("1.00"),
        ts_event=first_at,
        ts_recv=first_at,
        available_at=first_at,
        source_citation="synthetic://calendar-revision-mechanics",
    )
    revised = CalendarFieldVintage(
        source=first.source,
        record_id=first.record_id,
        field=first.field,
        vintage="revision",
        seq=2,
        value=Decimal("1.25"),
        ts_event=revision_at,
        ts_recv=revision_at,
        available_at=revision_at,
        source_citation=first.source_citation,
    )
    path = work_dir / "synthetic-calendar.sqlite3"
    with CalendarStore(path) as store:
        store.append(revised)
        store.append(first)
    with CalendarStore(path) as store:
        before_first = store.known_at(first_at - timedelta(microseconds=1)) == ()
        before_revision = store.known_at(revision_at - timedelta(microseconds=1)) == (first,)
        at_revision = store.known_at(revision_at) == (revised,)
        all_kept = store.history() == (first, revised)
    return {
        "source_backed": False,
        "evidence_class": "SYNTHETIC_REVISION_MECHANICS_ONLY",
        "absent_before_first_print": before_first,
        "first_print_visible_before_revision": before_revision,
        "revision_visible_at_own_availability": at_revision,
        "both_vintages_retained": all_kept,
        "all_checks_passed": all((before_first, before_revision, at_revision, all_kept)),
        "database_file": path.name,
    }


def capture_source(url: str, work_dir: Path, root: Path) -> tuple[bytes, dict[str, object]]:
    if url not in SOURCE_URLS:
        raise ValueError("only the identified HTTPS primary sources may be fetched")
    request = Request(  # noqa: S310 - fixed HTTPS allowlist above
        url,
        headers={"User-Agent": "tradebot-source-verification/1.0"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS allowlist above
        body = response.read(MAX_SOURCE_BYTES + 1)
        if len(body) > MAX_SOURCE_BYTES:
            raise ValueError("source response exceeds bounded snapshot size")
        if response.status != 200:
            raise ValueError(f"source returned HTTP {response.status}")
        final_url = response.geturl()
        if (
            urlsplit(final_url).scheme != "https"
            or urlsplit(final_url).hostname != urlsplit(url).hostname
        ):
            raise ValueError("source redirected away from its identified HTTPS primary host")
        retrieved_at = datetime.now(UTC)
        metadata: dict[str, object] = {
            "url": url,
            "final_url": final_url,
            "retrieved_at_utc": retrieved_at.isoformat(),
            "http_status": response.status,
            "http_date": response.headers.get("Date"),
            "http_last_modified": response.headers.get("Last-Modified"),
            "content_type": response.headers.get("Content-Type"),
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
    path = work_dir / f"{metadata['sha256']}.html"
    with path.open("xb") as stream:
        stream.write(body)
    metadata["snapshot_path"] = path.relative_to(root).as_posix()
    return body, metadata


def load_captured_source(report_path: Path, root: Path) -> tuple[bytes, dict[str, object]]:
    """Replay the preserved bytes and recorded receipt time, with hash/path checks."""
    prior = json.loads(report_path.read_text(encoding="utf-8"))
    source: dict[str, object] = prior["source"]
    if source.get("url") != FED_URL:
        raise ValueError("captured report does not identify the selected official source")
    path = (root / str(source["snapshot_path"])).resolve()
    if not path.is_relative_to(root.resolve() / "build"):
        raise ValueError("captured source must remain inside repository build storage")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != source.get("sha256"):
        raise ValueError("captured source hash mismatch")
    # Calendar validation independently checks UTC and forbids future leakage.
    datetime.fromisoformat(str(source["retrieved_at_utc"]))
    return body, source


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/reports/gate1_calendar.json"))
    parser.add_argument("--work-dir", type=Path, default=Path("build/gate1/calendar"))
    parser.add_argument(
        "--replay-report", type=Path, help="verify preserved source without network"
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve()
    work_root = (root / args.work_dir).resolve()
    if not work_root.is_relative_to(root / "build") or not output.is_relative_to(root):
        parser.error("work-dir must be under build/ and output must be inside the repository")
    work_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="source-import-", dir=work_root))
    if args.replay_report is None:
        body, fed_source = capture_source(FED_URL, work_dir, root)
    else:
        body, fed_source = load_captured_source((root / args.replay_report).resolve(), root)
    release = parse_fed_release(body)
    actual = validate_import(
        release,
        datetime.fromisoformat(str(fed_source["retrieved_at_utc"])),
        work_dir,
    )
    synthetic = validate_synthetic_revisions(work_dir)
    context_sources: list[dict[str, object]] = []
    for url in SOURCE_URLS[1:] if args.replay_report is None else ():
        try:
            context_payload, metadata = capture_source(url, work_dir, root)
            text = visible_text(context_payload)
            markers = {
                FBS_OCTOBER_URL: ("2024", "HK50", "October"),
                FBS_CONDITIONS_URL: ("Monday 00:00:00", "Friday 23:59:59", "EET"),
                FBS_HOURS_URL: ("last Sunday in March", "last Sunday in October"),
            }[url]
            metadata["reviewed_source_markers_present"] = {
                marker: marker in text for marker in markers
            }
            context_sources.append(metadata)
        except (URLError, ValueError, OSError) as exc:
            context_sources.append({"url": url, "retrieval_status": "FAILED", "reason": str(exc)})
    report: dict[str, object] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "evidence_class": "BOUNDED_SOURCE_BACKED_CALENDAR_INTEGRATION",
        "status": "PASSED"
        if actual["all_checks_passed"] and synthetic["all_checks_passed"]
        else "FAILED",
        "gate_1_approved": False,
        "source": fed_source,
        "source_capture_mode": "NETWORK" if args.replay_report is None else "OFFLINE_REPLAY",
        "official_source_import": actual,
        "synthetic_revision_checks": synthetic,
        "expected_liquidity_assessment": {
            "instrument_scope": ["FBS-Demo/EURUSD", "FBS-Demo/GBPUSD"],
            "reference_month": "2024-10",
            "status": "INDETERMINATE",
            "approved_entries": 0,
            "sources": context_sources,
            "findings": [
                "Source captures retain the October holiday notice, current trading conditions "
                "and current server-time policy; marker checks expose changed source content.",
                "Current general schedules do not prove their effective policy for these "
                "demo FX symbols in October 2024.",
                "No FX exception in a notice is not evidence that every date had "
                "FULL expected liquidity.",
            ],
            "remaining_evidence": [
                "A dated broker-specific 2024 schedule and holiday policy for EURUSD/GBPUSD, "
                "including DST treatment.",
                "A sourced definition of expected liquid intervals and explicit "
                "FULL/PARTIAL/CLOSED date classification.",
                "Publication/effective timestamps and revision provenance for the intended "
                "historical knowledge cutoff.",
            ],
        },
        "limitations": [
            "One actual official release is imported; this is not broad event/calendar coverage.",
            "The source header declares a release time, not our historical receipt time "
            "or proof of immutable vintages.",
            "No original consensus, previous, revision sequence or actual observed "
            "release-time history was supplied.",
            "Official fields therefore use retrieval availability and AS_OF_UNVERIFIED; "
            "no historical backdating occurs.",
            "Synthetic revisions live in a separate database and are not official "
            "historical evidence.",
            "No expected-liquidity dates were approved or inferred; reference-month "
            "quality remains indeterminate.",
        ],
        "artifacts": [
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(work_dir.glob("*.sqlite3"))
        ],
        "code_and_spec_sha256": {
            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in (
                "scripts/validate_gate1_calendar.py",
                "src/tradebot/data/calendar.py",
                "docs/SPEC.md",
            )
        },
        "reproduce_command": "uv run --no-sync python scripts/validate_gate1_calendar.py",
        "offline_reproduce_command": (
            "uv run --no-sync python scripts/validate_gate1_calendar.py "
            "--replay-report docs/reports/gate1_calendar.json "
            "--output build/gate1/calendar-replay.json"
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output),
                "fields_imported": 4,
                "historical_as_of": "UNPROVEN",
                "approved_liquidity_entries": 0,
            }
        )
    )
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
