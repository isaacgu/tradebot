"""Strict additive source intervals and loader for the October clock supplement.

The original acquisition plan deliberately accepts only Sunday--Thursday canonical
FX sessions.  This module keeps that contract intact and provides a separate,
hash-bound path for five fixed supplemental Friday carrier-label intervals.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, cast

from tradebot.core.timestamps import require_utc
from tradebot.data.acquisition_probe import CANONICAL_TICK_HEADER
from tradebot.data.storage import sha256_path

if TYPE_CHECKING:
    from tradebot.data.corpus import ProbeArtifact


PLAN_SCHEMA = "tradebot.source-clock-supplement-plan.v1"
MANIFEST_SCHEMA = "tradebot.source-clock-supplement-manifest.v1"
SOURCE = "FBS-Demo"
INSTRUMENT = "EURUSD"
BROKER_SYMBOL = "EURUSD"
WINDOW_ID = "october_2024_clock_supplement"
FETCHES_PER_REQUEST = 2
MAX_INTERVAL = timedelta(hours=3)
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class SourceSupplementError(RuntimeError):
    """A supplement plan or completed manifest is not safe to consume."""


def _parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise SourceSupplementError(f"{field} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceSupplementError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    try:
        return require_utc(parsed, field=field)
    except (TypeError, ValueError) as exc:
        raise SourceSupplementError(f"{field} must be an ISO-8601 UTC timestamp") from exc


def _utc_text(value: datetime) -> str:
    return require_utc(value).isoformat().replace("+00:00", "Z")


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _HEX_SHA256.fullmatch(value) is None:
        raise SourceSupplementError(f"{field} must be a lowercase SHA-256")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SourceSupplementError(f"{field} must be an object with string keys")
    return cast(Mapping[str, object], value)


def _strict_equal(actual: object, expected: object) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    return type(actual) is type(expected) and actual == expected


def canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceIntervalRequest:
    """One bounded Friday carrier-label interval, shaped like ``ChunkRequest``."""

    logical_symbol: str
    broker_symbol: str
    window_id: str
    session_date: date
    index_in_window: int
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for value, field in (
            (self.logical_symbol, "logical_symbol"),
            (self.broker_symbol, "broker_symbol"),
            (self.window_id, "window_id"),
        ):
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise ValueError(f"{field} must be a canonical identifier")
        if type(self.session_date) is not date:
            raise TypeError("session_date must be date")
        if type(self.index_in_window) is not int:
            raise TypeError("index_in_window must be int")
        if self.index_in_window < 0:
            raise ValueError("index_in_window cannot be negative")
        start = require_utc(self.start, field="supplement start")
        end = require_utc(self.end, field="supplement end")
        if self.session_date != start.date():
            raise ValueError("session_date must equal the carrier-label start date")
        if self.session_date.weekday() != 4:
            raise ValueError("supplement intervals must start on Friday")
        if not timedelta(0) < end - start <= MAX_INTERVAL:
            raise ValueError("supplement interval must be positive and at most three hours")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def chunk_id(self) -> str:
        return f"{self.logical_symbol}/{self.window_id}/{self.session_date.isoformat()}"

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_symbol": self.logical_symbol,
            "broker_symbol": self.broker_symbol,
            "window_id": self.window_id,
            "session_date": self.session_date.isoformat(),
            "index_in_window": self.index_in_window,
            "start_utc": _utc_text(self.start),
            "end_utc": _utc_text(self.end),
            "chunk_id": self.chunk_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> SourceIntervalRequest:
        item = _mapping(value, "supplement request")
        session_date = item.get("session_date")
        if not isinstance(session_date, str):
            raise SourceSupplementError("request session_date must be YYYY-MM-DD")
        try:
            parsed_date = date.fromisoformat(session_date)
        except ValueError as exc:
            raise SourceSupplementError("request session_date must be YYYY-MM-DD") from exc
        index = item.get("index_in_window")
        if type(index) is not int:
            raise SourceSupplementError("request index_in_window must be int")
        for field in ("logical_symbol", "broker_symbol", "window_id"):
            if not isinstance(item.get(field), str):
                raise SourceSupplementError(f"request {field} must be str")
        try:
            request = cls(
                logical_symbol=cast(str, item["logical_symbol"]),
                broker_symbol=cast(str, item["broker_symbol"]),
                window_id=cast(str, item["window_id"]),
                session_date=parsed_date,
                index_in_window=index,
                start=_parse_utc(item.get("start_utc"), "request start_utc"),
                end=_parse_utc(item.get("end_utc"), "request end_utc"),
            )
        except (TypeError, ValueError) as exc:
            raise SourceSupplementError(str(exc)) from exc
        if item.get("chunk_id") != request.chunk_id:
            raise SourceSupplementError("request chunk_id does not match its fields")
        if set(item) != set(request.to_dict()):
            raise SourceSupplementError("supplement request has unknown or missing fields")
        return request


def fixed_clock_supplement_requests() -> tuple[SourceIntervalRequest, ...]:
    specs = (
        (date(2024, 10, 4), 3),
        (date(2024, 10, 11), 3),
        (date(2024, 10, 18), 3),
        (date(2024, 10, 25), 3),
        (date(2024, 11, 1), 2),
    )
    return tuple(
        SourceIntervalRequest(
            logical_symbol=INSTRUMENT,
            broker_symbol=BROKER_SYMBOL,
            window_id=WINDOW_ID,
            session_date=day,
            index_in_window=index,
            start=datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=21),
            end=datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=21)
            + timedelta(hours=hours),
        )
        for index, (day, hours) in enumerate(specs)
    )


def request_set_sha256(requests: Sequence[SourceIntervalRequest]) -> str:
    return canonical_json_sha256([request.to_dict() for request in requests])


def validate_fixed_requests(requests: Sequence[SourceIntervalRequest]) -> None:
    expected = fixed_clock_supplement_requests()
    if tuple(requests) != expected:
        raise SourceSupplementError("request set is not the fixed reviewed clock supplement")
    dates = [request.session_date for request in requests]
    if len(set(dates)) != len(dates):
        raise SourceSupplementError("supplement requests duplicate a carrier-label start date")
    ordered = sorted(requests, key=lambda item: item.start)
    for previous, current in pairwise(ordered):
        if current.start < previous.end:
            raise SourceSupplementError("supplement requests overlap")


def parse_plan(value: object) -> tuple[SourceIntervalRequest, ...]:
    plan = _mapping(value, "supplement plan")
    if plan.get("schema") != PLAN_SCHEMA:
        raise SourceSupplementError("unsupported supplement plan schema")
    expected = {
        "source": SOURCE,
        "instrument": INSTRUMENT,
        "broker_symbol": BROKER_SYMBOL,
        "fetches_per_request": FETCHES_PER_REQUEST,
        "execution_enabled": False,
        "training_enabled": False,
        "gate_approval": False,
    }
    if any(not _strict_equal(plan.get(key), value) for key, value in expected.items()):
        raise SourceSupplementError("supplement plan source, safety or repeat contract differs")
    raw_requests = plan.get("requests")
    if not isinstance(raw_requests, list):
        raise SourceSupplementError("supplement plan requests must be a list")
    requests = tuple(SourceIntervalRequest.from_dict(item) for item in raw_requests)
    validate_fixed_requests(requests)
    if plan.get("request_set_sha256") != request_set_sha256(requests):
        raise SourceSupplementError("supplement plan request-set hash mismatch")
    pins = _mapping(plan.get("input_sha256"), "supplement plan input_sha256")
    if not pins or any(
        not isinstance(value, str) or _HEX_SHA256.fullmatch(value) is None
        for value in pins.values()
    ):
        raise SourceSupplementError("supplement plan must contain lowercase input hashes")
    return requests


def _inside(root: Path, relative: object, field: str) -> Path:
    if not isinstance(relative, str):
        raise SourceSupplementError(f"{field} must be a relative path")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SourceSupplementError(f"{field} must stay under artifact_root")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise SourceSupplementError(f"{field} escapes artifact_root")
    return resolved


def _semantic_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    rows = 0
    try:
        with gzip.open(path, "rb") as stream:
            header = stream.readline()
            if header != CANONICAL_TICK_HEADER:
                raise SourceSupplementError(f"invalid canonical header: {path}")
            digest.update(header)
            for line in stream:
                if not line.endswith(b"\n") or len(line.removesuffix(b"\n").split(b"\t")) != 8:
                    raise SourceSupplementError(f"invalid canonical row in {path}")
                digest.update(line)
                rows += 1
    except (OSError, EOFError) as exc:
        raise SourceSupplementError(f"cannot decode canonical response {path}") from exc
    return digest.hexdigest(), rows


def _response(
    value: object,
    *,
    artifact_root: Path,
    request: SourceIntervalRequest,
    expected_fetch: int,
) -> tuple[Mapping[str, object], Path, Path]:
    response = _mapping(value, f"response {expected_fetch}")
    if (
        not _strict_equal(response.get("fetch"), expected_fetch)
        or response.get("status") != "VERIFIED"
    ):
        raise SourceSupplementError("response fetch/status mismatch")
    if response.get("chunk_id") != request.chunk_id:
        raise SourceSupplementError("response chunk_id mismatch")
    rows = response.get("returned_rows")
    if type(rows) is not int or rows < 0 or not _strict_equal(response.get("half_open_rows"), rows):
        raise SourceSupplementError("response row count is invalid")
    for field in (
        "rows_before_start",
        "rows_exactly_at_end",
        "rows_after_end",
        "timestamp_regressions",
        "time_field_mismatches",
    ):
        if not _strict_equal(response.get(field), 0):
            raise SourceSupplementError(f"response {field} must be zero")
    if response.get("native_roundtrip_equal") is not True:
        raise SourceSupplementError("native response roundtrip was not verified")
    if response.get("returned_order_preserved") is not True:
        raise SourceSupplementError("native response order was not preserved")
    if response.get("timezone_adjustment_applied") is not False:
        raise SourceSupplementError("capture response must retain source carrier labels")
    if not _strict_equal(response.get("mt5_error_code"), 1):
        raise SourceSupplementError("capture response did not record MT5 success")
    canonical = _inside(artifact_root, response.get("canonical_path"), "canonical_path")
    native = _inside(artifact_root, response.get("native_path"), "native_path")
    for path, prefix in ((canonical, "canonical"), (native, "native")):
        if not path.is_file():
            raise SourceSupplementError(f"{prefix} response is missing: {path}")
        expected_bytes = response.get(f"{prefix}_bytes")
        if type(expected_bytes) is not int or path.stat().st_size != expected_bytes:
            raise SourceSupplementError(f"{prefix} response byte count mismatch")
        if sha256_path(path) != _sha(response.get(f"{prefix}_sha256"), f"{prefix}_sha256"):
            raise SourceSupplementError(f"{prefix} response checksum mismatch")
    semantic, decoded_rows = _semantic_file(canonical)
    if semantic != _sha(response.get("semantic_sha256"), "semantic_sha256"):
        raise SourceSupplementError("canonical semantic checksum mismatch")
    if decoded_rows != rows:
        raise SourceSupplementError("canonical row count mismatch")
    return response, canonical, native


def load_supplement_artifacts(
    manifest_path: Path,
    *,
    artifact_root: Path,
    expected_manifest_sha256: str,
    ordinal_start: int,
) -> tuple[ProbeArtifact, ...]:
    """Load a fully verified two-response supplement as ordinary raw artifacts.

    ``ProbeArtifact`` is imported lazily so this validation module does not create a
    module cycle with the corpus builder that consumes the returned objects.
    """

    expected_manifest_sha256 = _sha(expected_manifest_sha256, "expected_manifest_sha256")
    if type(ordinal_start) is not int or ordinal_start < 0:
        raise SourceSupplementError("ordinal_start must be a non-negative int")
    root = artifact_root.resolve()
    manifest = manifest_path.resolve()
    if not manifest.is_relative_to(root):
        raise SourceSupplementError("manifest_path escapes artifact_root")
    if sha256_path(manifest) != expected_manifest_sha256:
        raise SourceSupplementError("supplement manifest file checksum mismatch")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceSupplementError("cannot parse supplement manifest") from exc
    document = _mapping(payload, "supplement manifest")
    integrity = _mapping(document.get("integrity"), "supplement manifest integrity")
    unsigned = {key: value for key, value in document.items() if key != "integrity"}
    if integrity != {"algorithm": "sha256", "payload_sha256": canonical_json_sha256(unsigned)}:
        raise SourceSupplementError("supplement manifest integrity mismatch")
    if document.get("schema") != MANIFEST_SCHEMA or document.get("status") != "COMPLETE":
        raise SourceSupplementError("supplement manifest is not a completed supported capture")
    expected = {
        "source": SOURCE,
        "instrument": INSTRUMENT,
        "broker_symbol": BROKER_SYMBOL,
        "fetches_per_request": FETCHES_PER_REQUEST,
        "execution_enabled": False,
        "training_enabled": False,
        "gate_approval": False,
        "account_identifiers_persisted": False,
        "input_pins_before": True,
        "input_pins_after": True,
        "mt5_session_poisoned": False,
        "planned_requests": len(fixed_clock_supplement_requests()),
        "planned_fetches": len(fixed_clock_supplement_requests()) * FETCHES_PER_REQUEST,
    }
    if any(not _strict_equal(document.get(key), value) for key, value in expected.items()):
        raise SourceSupplementError("manifest source or safety contract differs")
    account = _mapping(document.get("account_policy"), "account_policy")
    expected_account = {
        "server": SOURCE,
        "is_demo": True,
        "currency": "USD",
        "balance_equals_usd_1000": True,
        "equity_equals_usd_1000": True,
        "margin_equals_zero": True,
        "positions_before": 0,
        "orders_before": 0,
        "positions_after": 0,
        "orders_after": 0,
    }
    if set(account) != set(expected_account) or any(
        not _strict_equal(account.get(key), value) for key, value in expected_account.items()
    ):
        raise SourceSupplementError("capture account policy is not the approved empty demo account")
    request_set = document.get("requests")
    if not isinstance(request_set, list):
        raise SourceSupplementError("manifest requests must be a list")
    requests = tuple(
        SourceIntervalRequest.from_dict(_mapping(item, "manifest request").get("request"))
        for item in request_set
    )
    validate_fixed_requests(requests)
    set_hash = request_set_sha256(requests)
    if document.get("request_set_sha256") != set_hash:
        raise SourceSupplementError("manifest request-set hash mismatch")
    if len(request_set) != len(requests):
        raise SourceSupplementError("manifest request count mismatch")
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise SourceSupplementError("manifest run_id must be non-empty")

    from tradebot.data.corpus import ProbeArtifact

    artifacts: list[ProbeArtifact] = []
    for index, (raw_item, request) in enumerate(zip(request_set, requests, strict=True)):
        item = _mapping(raw_item, "manifest request")
        responses = item.get("responses")
        if not isinstance(responses, list) or len(responses) != FETCHES_PER_REQUEST:
            raise SourceSupplementError("every request must retain exactly two responses")
        primary, primary_path, primary_native_path = _response(
            responses[0], artifact_root=root, request=request, expected_fetch=1
        )
        repeated, repeat_path, repeat_native_path = _response(
            responses[1], artifact_root=root, request=request, expected_fetch=2
        )
        if primary_path == repeat_path or primary_native_path == repeat_native_path:
            raise SourceSupplementError(
                "two canonical and native responses must be retained at distinct paths"
            )
        comparison = _mapping(item.get("comparison"), "repeat comparison")
        if comparison.get("identical") is not True:
            raise SourceSupplementError("supplement repeat responses are not identical")
        if any(
            primary.get(field) != repeated.get(field)
            for field in (
                "returned_rows",
                "semantic_sha256",
                "canonical_sha256",
                "native_sha256",
            )
        ):
            raise SourceSupplementError("two response records differ")
        if comparison.get("semantic_sha256") != primary.get("semantic_sha256"):
            raise SourceSupplementError("repeat comparison semantic hash mismatch")
        completed_at = _parse_utc(item.get("completed_at_utc"), "request completed_at_utc")
        semantic = _sha(primary.get("semantic_sha256"), "semantic_sha256")
        identity = canonical_json_sha256(
            {
                "plan_hash": set_hash,
                "chunk_id": request.chunk_id,
                "semantic_sha256": semantic,
            }
        )
        artifacts.append(
            ProbeArtifact(
                request=request,
                ordinal=ordinal_start + index,
                plan_hash=set_hash,
                source=SOURCE,
                run_id=run_id,
                completed_at=completed_at,
                raw_path=primary_path,
                checkpoint_path=manifest,
                semantic_sha256=semantic,
                compressed_sha256=_sha(primary.get("canonical_sha256"), "canonical_sha256"),
                expected_rows=cast(int, primary["returned_rows"]),
                artifact_id=identity,
            )
        )
    return tuple(artifacts)
