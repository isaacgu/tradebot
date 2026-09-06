"""Hash-bound contracts for the additive Friday source supplement."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tradebot.data.acquisition_probe import (
    CANONICAL_TICK_HEADER,
    SourceTick,
    encode_source_tick,
)
from tradebot.data.source_supplement import (
    BROKER_SYMBOL,
    FETCHES_PER_REQUEST,
    INSTRUMENT,
    MANIFEST_SCHEMA,
    PLAN_SCHEMA,
    SOURCE,
    WINDOW_ID,
    SourceIntervalRequest,
    SourceSupplementError,
    canonical_json_sha256,
    fixed_clock_supplement_requests,
    load_supplement_artifacts,
    parse_plan,
    request_set_sha256,
)
from tradebot.data.storage import sha256_path


def _plan() -> dict[str, object]:
    requests = fixed_clock_supplement_requests()
    return {
        "schema": PLAN_SCHEMA,
        "source": SOURCE,
        "instrument": INSTRUMENT,
        "broker_symbol": BROKER_SYMBOL,
        "fetches_per_request": FETCHES_PER_REQUEST,
        "execution_enabled": False,
        "training_enabled": False,
        "gate_approval": False,
        "input_sha256": {"evidence.json": "a" * 64},
        "requests": [request.to_dict() for request in requests],
        "request_set_sha256": request_set_sha256(requests),
    }


def _canonical_bytes(tick: SourceTick) -> tuple[bytes, str]:
    line = encode_source_tick(tick)
    semantic = hashlib.sha256(CANONICAL_TICK_HEADER + line).hexdigest()
    import io

    result = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=result, mode="wb", mtime=0) as stream:
        stream.write(CANONICAL_TICK_HEADER)
        stream.write(line)
    return result.getvalue(), semantic


def _write_manifest(root: Path) -> tuple[Path, dict[str, Any]]:
    requests = fixed_clock_supplement_requests()
    request_records: list[dict[str, Any]] = []
    for request in requests:
        stamp = int(request.start.timestamp() * 1_000) + 1_000
        tick = SourceTick(
            time=stamp // 1_000,
            time_msc=stamp,
            bid=Decimal("1.10000"),
            ask=Decimal("1.10010"),
            last=Decimal("0.0"),
            volume=0,
            flags=6,
            volume_real=Decimal("0.0"),
        )
        canonical, semantic = _canonical_bytes(tick)
        responses: list[dict[str, object]] = []
        native = f"native:{request.chunk_id}".encode()
        for fetch in range(1, FETCHES_PER_REQUEST + 1):
            stem = f"{request.index_in_window:02d}-fetch-{fetch}"
            canonical_path = root / f"{stem}.tsv.gz"
            native_path = root / f"{stem}.npy"
            canonical_path.write_bytes(canonical)
            native_path.write_bytes(native)
            responses.append(
                {
                    "fetch": fetch,
                    "chunk_id": request.chunk_id,
                    "status": "VERIFIED",
                    "returned_rows": 1,
                    "half_open_rows": 1,
                    "rows_before_start": 0,
                    "rows_exactly_at_end": 0,
                    "rows_after_end": 0,
                    "timestamp_regressions": 0,
                    "time_field_mismatches": 0,
                    "native_roundtrip_equal": True,
                    "returned_order_preserved": True,
                    "timezone_adjustment_applied": False,
                    "mt5_error_code": 1,
                    "canonical_path": canonical_path.name,
                    "canonical_bytes": canonical_path.stat().st_size,
                    "canonical_sha256": sha256_path(canonical_path),
                    "native_path": native_path.name,
                    "native_bytes": native_path.stat().st_size,
                    "native_sha256": sha256_path(native_path),
                    "semantic_sha256": semantic,
                }
            )
        request_records.append(
            {
                "request": request.to_dict(),
                "responses": responses,
                "comparison": {"identical": True, "semantic_sha256": semantic},
                "completed_at_utc": "2026-09-06T12:00:00Z",
            }
        )
    unsigned: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "status": "COMPLETE",
        "source": SOURCE,
        "instrument": INSTRUMENT,
        "broker_symbol": BROKER_SYMBOL,
        "fetches_per_request": FETCHES_PER_REQUEST,
        "planned_requests": len(requests),
        "planned_fetches": len(requests) * FETCHES_PER_REQUEST,
        "execution_enabled": False,
        "training_enabled": False,
        "gate_approval": False,
        "account_identifiers_persisted": False,
        "input_pins_before": True,
        "input_pins_after": True,
        "mt5_session_poisoned": False,
        "run_id": "synthetic-supplement",
        "request_set_sha256": request_set_sha256(requests),
        "account_policy": {
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
        },
        "requests": request_records,
    }
    payload = {
        **unsigned,
        "integrity": {
            "algorithm": "sha256",
            "payload_sha256": canonical_json_sha256(unsigned),
        },
    }
    path = root / "supplement-manifest.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path, payload


def _rewrite_manifest(path: Path, payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "integrity"}
    payload["integrity"] = {
        "algorithm": "sha256",
        "payload_sha256": canonical_json_sha256(unsigned),
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return sha256_path(path)


def test_fixed_requests_are_exact_bounded_friday_carrier_intervals() -> None:
    requests = fixed_clock_supplement_requests()

    assert [request.session_date for request in requests] == [
        date(2024, 10, 4),
        date(2024, 10, 11),
        date(2024, 10, 18),
        date(2024, 10, 25),
        date(2024, 11, 1),
    ]
    assert [request.end - request.start for request in requests] == [
        timedelta(hours=3),
        timedelta(hours=3),
        timedelta(hours=3),
        timedelta(hours=3),
        timedelta(hours=2),
    ]
    assert all(request.start.hour == 21 for request in requests)
    assert all(request.start.tzinfo is UTC for request in requests)
    assert len({request.chunk_id for request in requests}) == len(requests)


def test_interval_request_rejects_non_friday_and_oversized_ranges() -> None:
    with pytest.raises(ValueError, match="Friday"):
        SourceIntervalRequest(
            logical_symbol=INSTRUMENT,
            broker_symbol=BROKER_SYMBOL,
            window_id=WINDOW_ID,
            session_date=date(2024, 10, 3),
            index_in_window=0,
            start=datetime(2024, 10, 3, 21, tzinfo=UTC),
            end=datetime(2024, 10, 4, 0, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="at most three hours"):
        SourceIntervalRequest(
            logical_symbol=INSTRUMENT,
            broker_symbol=BROKER_SYMBOL,
            window_id=WINDOW_ID,
            session_date=date(2024, 10, 4),
            index_in_window=0,
            start=datetime(2024, 10, 4, 20, tzinfo=UTC),
            end=datetime(2024, 10, 5, 0, tzinfo=UTC),
        )


def test_plan_rejects_numeric_safety_flags_and_repeat_count() -> None:
    valid = _plan()
    assert parse_plan(valid) == fixed_clock_supplement_requests()

    numeric_flag = dict(valid)
    numeric_flag["execution_enabled"] = 0
    with pytest.raises(SourceSupplementError, match="safety"):
        parse_plan(numeric_flag)

    numeric_fetches = dict(valid)
    numeric_fetches["fetches_per_request"] = 2.0
    with pytest.raises(SourceSupplementError, match="repeat"):
        parse_plan(numeric_fetches)


def test_loader_returns_only_primary_of_two_identical_hash_bound_responses(
    tmp_path: Path,
) -> None:
    manifest, _ = _write_manifest(tmp_path)
    digest = sha256_path(manifest)

    artifacts = load_supplement_artifacts(
        manifest,
        artifact_root=tmp_path,
        expected_manifest_sha256=digest,
        ordinal_start=100,
    )

    assert len(artifacts) == 5
    assert [artifact.ordinal for artifact in artifacts] == [100, 101, 102, 103, 104]
    assert all(artifact.expected_rows == 1 for artifact in artifacts)
    assert all(artifact.source == SOURCE for artifact in artifacts)
    assert all("fetch-1" in artifact.raw_path.name for artifact in artifacts)
    assert all(isinstance(artifact.request, SourceIntervalRequest) for artifact in artifacts)


def test_loader_rejects_outer_hash_mismatch(tmp_path: Path) -> None:
    manifest, _ = _write_manifest(tmp_path)

    with pytest.raises(SourceSupplementError, match="file checksum"):
        load_supplement_artifacts(
            manifest,
            artifact_root=tmp_path,
            expected_manifest_sha256="0" * 64,
            ordinal_start=0,
        )


def test_loader_rejects_numeric_account_zero_and_nonidentical_native_repeat(
    tmp_path: Path,
) -> None:
    manifest, payload = _write_manifest(tmp_path)
    payload["account_policy"]["positions_before"] = False
    digest = _rewrite_manifest(manifest, payload)
    with pytest.raises(SourceSupplementError, match="account policy"):
        load_supplement_artifacts(
            manifest,
            artifact_root=tmp_path,
            expected_manifest_sha256=digest,
            ordinal_start=0,
        )

    manifest, payload = _write_manifest(tmp_path)
    second = payload["requests"][0]["responses"][1]
    native = tmp_path / second["native_path"]
    native.write_bytes(b"different native response")
    second["native_bytes"] = native.stat().st_size
    second["native_sha256"] = sha256_path(native)
    digest = _rewrite_manifest(manifest, payload)
    with pytest.raises(SourceSupplementError, match="response records differ"):
        load_supplement_artifacts(
            manifest,
            artifact_root=tmp_path,
            expected_manifest_sha256=digest,
            ordinal_start=0,
        )
