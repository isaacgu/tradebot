"""Definition preparation is deterministic policy scaffolding, not acceptance."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import ModuleType
from urllib.request import FTPHandler, HTTPHandler, HTTPSHandler, Request
from urllib.response import addinfourl

import pytest

from tradebot.data.calendar import ExpectedLiquidityCalendar, LiquidityStatus
from tradebot.data.reference_acceptance import FlagTreatment, read_policy


@pytest.fixture
def runner() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts/prepare_reference_definition.py"
    spec = importlib.util.spec_from_file_location("prepare_reference_definition_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_draft_dates_minutes_and_context_are_complete(runner: ModuleType) -> None:
    prepared = datetime(2026, 9, 5, 11, tzinfo=UTC)
    calendar = runner.build_calendar(prepared)
    days = [row for row in calendar.entries if row.session_date.month == 10]
    assert len(calendar.entries) == 34
    assert len(days) == 31
    assert len({row.session_date for row in days}) == 31
    assert sum(row.expected_seconds / 60 for row in days) == 4410
    assert sum(row.status == LiquidityStatus.FULL for row in days) == 21
    assert all(row.instrument == "FBS-Demo/EURUSD" for row in calendar.entries)
    for holiday in (date(2024, 10, 3), date(2024, 10, 14)):
        row = calendar.lookup("FBS-Demo/EURUSD", holiday, known_at=prepared)
        assert row is not None and row.status == LiquidityStatus.CLOSED
        assert row.expected_intervals == ()


def test_timezone_conversion_uses_london_dst_not_fixed_utc(runner: ModuleType) -> None:
    prepared = datetime(2026, 9, 5, 11, tzinfo=UTC)
    calendar = runner.build_calendar(prepared)
    before = calendar.lookup("FBS-Demo/EURUSD", date(2024, 10, 25), known_at=prepared)
    after = calendar.lookup("FBS-Demo/EURUSD", date(2024, 10, 28), known_at=prepared)
    assert before is not None and after is not None
    assert before.expected_intervals == (
        (
            datetime(2024, 10, 25, 12, tzinfo=UTC),
            datetime(2024, 10, 25, 15, 30, tzinfo=UTC),
        ),
    )
    assert after.expected_intervals == (
        (
            datetime(2024, 10, 28, 13, tzinfo=UTC),
            datetime(2024, 10, 28, 16, 30, tzinfo=UTC),
        ),
    )


def test_draft_never_backdates_knowledge_and_expires(runner: ModuleType) -> None:
    prepared = datetime(2026, 9, 5, 11, tzinfo=UTC)
    calendar = runner.build_calendar(prepared)
    day = date(2024, 10, 1)
    assert (
        calendar.lookup("FBS-Demo/EURUSD", day, known_at=datetime(2024, 10, 2, tzinfo=UTC)) is None
    )
    assert (
        calendar.lookup("FBS-Demo/EURUSD", day, known_at=prepared - timedelta(microseconds=1))
        is None
    )
    assert calendar.lookup("FBS-Demo/EURUSD", day, known_at=prepared) is not None
    assert calendar.lookup("FBS-Demo/EURUSD", day, known_at=prepared + timedelta(days=30)) is None
    with pytest.raises(ValueError, match="UTC"):
        runner.build_calendar(datetime(2026, 9, 5))


def _fixture_root(runner: ModuleType, monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    real_root = runner.ROOT
    for name in (
        "docs/SPEC.md",
        "configs/calendars/reference_month_policy_draft.json",
        "docs/reports/reference_definition_proposal.md",
        "uv.lock",
    ):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((real_root / name).read_bytes())
    monkeypatch.setattr(runner, "ROOT", root)


def test_preparation_is_immutable_unapproved_and_does_not_read_prices(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture_root(runner, monkeypatch, tmp_path)
    output = tmp_path / "build/proposal"
    report = runner.prepare(output)
    assert report["status"] == "DRAFT_REQUIRES_HUMAN_REVIEW"
    assert report["human_approvals"] == []
    assert report["price_data_read"] is False
    assert report["acceptance_rate_computed"] is False
    assert (
        report["gate_approved"]
        is report["training_enabled"]
        is report["execution_enabled"]
        is False
    )
    assert {row["status"] for row in report["sources"].values()} == {"NOT_CAPTURED"}
    assert len(ExpectedLiquidityCalendar.read(output / "calendar-draft.json").entries) == 34
    policy = read_policy(output / "policy-draft.json")
    assert policy.status == "DRAFT_UNAPPROVED"
    rules = {rule.name: rule.treatment for rule in policy.rules}
    assert len(rules) == 14
    assert rules["TS_RECV_IMPUTED"] == FlagTreatment.EXCLUDE_FROM_NUMERATOR
    assert rules["BACKFILLED"] == FlagTreatment.EXCLUDE_FROM_NUMERATOR
    assert rules["QUALITY_WARMUP"] == FlagTreatment.INDETERMINATE_IF_PRESENT
    assert rules["GAP_CALENDAR_UNKNOWN"] == FlagTreatment.INDETERMINATE_IF_PRESENT
    assert rules["PRICE_OUTLIER"] == FlagTreatment.COUNT_AS_FLAGGED
    assert policy.threshold == "0.001"
    assert policy.missing_expected_bar_treatment == "COUNT_AS_FLAGGED"
    with pytest.raises(FileExistsError):
        runner.prepare(output)


def test_changed_base_or_spec_and_external_output_fail_closed(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _fixture_root(runner, monkeypatch, root)
    with pytest.raises(ValueError, match="under build"):
        runner.prepare(tmp_path / "external")
    spec = root / "docs/SPEC.md"
    original = spec.read_bytes()
    spec.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SPEC"):
        runner.prepare(root / "build/spec-changed")
    spec.write_bytes(original)
    policy = root / "configs/calendars/reference_month_policy_draft.json"
    policy.write_bytes(b"changed")
    with pytest.raises(ValueError, match="base policy"):
        runner.prepare(root / "build/policy-changed")


def test_unavailable_sources_are_not_converted_to_evidence(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture_root(runner, monkeypatch, tmp_path)

    def unavailable(url: str, directory: Path) -> None:
        raise OSError("source unavailable")

    monkeypatch.setattr(runner, "capture_source", unavailable)
    report = runner.prepare(tmp_path / "build/unavailable", capture_sources=True)
    assert {row["status"] for row in report["sources"].values()} == {"UNAVAILABLE"}
    assert report["human_approvals"] == []


def test_source_capture_rejects_arbitrary_urls_before_io(
    runner: ModuleType, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="allowlist"):
        runner.capture_source("http://example.invalid", tmp_path)


class _SourceResponse(addinfourl):
    msg: str


def _fake_source_transport(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, tuple[int, str | None, bytes]],
) -> list[str]:
    """Keep real urllib redirect dispatch, replacing only its network transport."""
    attempted: list[str] = []

    def respond(_handler: object, request: Request) -> addinfourl:
        attempted.append(request.full_url)
        assert getattr(request, "timeout", None) == 30
        code, location, body = routes.get(request.full_url, (200, None, b"outside source"))
        headers = Message()
        headers["Content-Type"] = "application/pdf" if body.startswith(b"%PDF-") else "text/html"
        if location is not None:
            headers["Location"] = location
        response = _SourceResponse(BytesIO(body), headers, request.full_url, code)
        response.msg = "OK" if code == 200 else "Moved"
        return response

    monkeypatch.setattr(HTTPSHandler, "https_open", respond)
    monkeypatch.setattr(HTTPHandler, "http_open", respond)
    monkeypatch.setattr(FTPHandler, "ftp_open", respond)
    return attempted


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1/private",
        "https://off-source.invalid/evidence",
        "//off-source.invalid/evidence",
        "https://www.gov.uk:8443/evidence",
        "https://user:password@www.gov.uk/evidence",
    ],
)
def test_redirect_rejected_before_destination_request(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int, target: str
) -> None:
    url = runner.SOURCE_URLS["uk_holidays"]
    attempted = _fake_source_transport(monkeypatch, {url: (code, target, b"")})
    with pytest.raises(ValueError, match="HTTPS"):
        runner.capture_source(url, tmp_path)
    assert attempted == [url]
    assert list(tmp_path.iterdir()) == []


def test_return_to_source_chain_cannot_hide_a_forbidden_hop(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = runner.SOURCE_URLS["uk_holidays"]
    outside = "https://off-source.invalid/return"
    returned = "https://www.gov.uk/returned"
    attempted = _fake_source_transport(
        monkeypatch,
        {url: (302, outside, b""), outside: (302, returned, b""), returned: (200, None, b"source")},
    )
    with pytest.raises(ValueError, match="HTTPS"):
        runner.capture_source(url, tmp_path)
    assert attempted == [url]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("body", [b"<html>primary source</html>", b"%PDF-fixture"])
@pytest.mark.parametrize("redirect", [False, True])
def test_direct_and_same_origin_redirected_capture_remain_usable(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: bytes,
    redirect: bool,
) -> None:
    url = runner.SOURCE_URLS["uk_holidays"]
    final = "https://www.gov.uk/current" if redirect else url
    routes: dict[str, tuple[int, str | None, bytes]] = {final: (200, None, body)}
    if redirect:
        routes[url] = (302, "/current", b"")
    attempted = _fake_source_transport(monkeypatch, routes)
    evidence = runner.capture_source(url, tmp_path)
    assert attempted == ([url, final] if redirect else [url])
    assert evidence["status"] == "CAPTURED_NOT_ADJUDICATED"
    assert evidence["final_url"] == final
    assert (tmp_path / evidence["snapshot"]).read_bytes() == body
    assert evidence["bytes"] == len(body)
    assert evidence["historical_available_at"] is None


def test_forbidden_redirect_is_unavailable_not_capture_evidence(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fixture_root(runner, monkeypatch, tmp_path)
    routes: dict[str, tuple[int, str | None, bytes]] = {
        url: (302, "http://127.0.0.1/private", b"") for url in runner.SOURCE_URLS.values()
    }
    attempted = _fake_source_transport(monkeypatch, routes)
    report = runner.prepare(tmp_path / "build/blocked-redirects", capture_sources=True)
    assert attempted == list(runner.SOURCE_URLS.values())
    assert {row["status"] for row in report["sources"].values()} == {"UNAVAILABLE"}
    assert list((tmp_path / "build/blocked-redirects/sources").iterdir()) == []
    assert report["human_approvals"] == []


def test_inputs_changed_during_capture_cannot_create_an_approved_draft(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture_root(runner, monkeypatch, tmp_path)

    def mutate(url: str, directory: Path) -> dict[str, str]:
        path = tmp_path / "configs/calendars/reference_month_policy_draft.json"
        policy = json.loads(path.read_bytes())
        policy["status"] = "APPROVED"
        path.write_text(json.dumps(policy))
        return {"status": "TEST_FIXTURE"}

    monkeypatch.setattr(runner, "capture_source", mutate)
    output = tmp_path / "build/changed-input"
    with pytest.raises(ValueError, match="inputs changed"):
        runner.prepare(output, capture_sources=True)
    assert not (output / "proposal.json").exists()
    assert not (output / "policy-draft.json").exists()


def test_main_publishes_draft_only(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture_root(runner, monkeypatch, tmp_path)
    output = tmp_path / "build/cli"
    assert runner.main(["--output-dir", str(output)]) == 0
    report = json.loads((output / "proposal.json").read_bytes())
    assert report["training_enabled"] is False


def test_output_replaced_during_capture_does_not_redirect_definition_writes(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _fixture_root(runner, monkeypatch, root)
    output = root / "build/proposal"
    outside = tmp_path / "outside"
    outside.mkdir()
    swapped = False

    def swap(url: str, directory: Path) -> dict[str, str]:
        nonlocal swapped
        if not swapped:
            output.rename(root / "build/preserved-original")
            output.symlink_to(outside, target_is_directory=True)
            swapped = True
        return {"status": "TEST_FIXTURE"}

    monkeypatch.setattr(runner, "capture_source", swap)
    with pytest.raises(ValueError, match="replaced or redirected"):
        runner.prepare(output, capture_sources=True)
    assert list(outside.iterdir()) == []


def test_redirected_build_root_cannot_write_into_raw(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture_root(runner, monkeypatch, tmp_path)
    raw = tmp_path / "raw"
    raw.mkdir()
    (tmp_path / "build").symlink_to(raw, target_is_directory=True)
    with pytest.raises(ValueError, match="under build"):
        runner.prepare(tmp_path / "build/proposal")
    assert list(raw.iterdir()) == []
