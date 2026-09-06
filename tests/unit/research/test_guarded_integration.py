"""Invented payloads through real authorization, Parquet consumption and publication.

Every approval, trust pin and source byte is generated only under pytest's temporary
directory. No collected data, production trust configuration or broker is used.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_authorization import KNOWN, ROLES, Fixture, make_fixture

from tradebot.data.storage import (
    CLEAN_BAR_SCHEMA,
    ImmutableParquetWriter,
    clean_bar_path,
    dataset_id,
    file_manifest,
    sha256_path,
)
from tradebot.research.authorization import (
    AuthorizationError,
    ResearchPurpose,
    TrustedReleaseRegistry,
)
from tradebot.research.engine import ReplayConfig
from tradebot.research.feed import SnapshotBarFeed, SnapshotSpec
from tradebot.research.guarded import ApprovedSnapshotStream, open_approved_snapshot
from tradebot.research.report import ReplayProvenance, canonical_bytes, publish_replay

_ROWS = 80


def _payload_fixture(root: Path, purpose: ResearchPurpose) -> Fixture:
    """Bind actual invented bytes, not the authorization unit fixture's placeholder SHA."""
    fixture = make_fixture(root)
    fixture.purpose = purpose
    rows: list[dict[str, object]] = []
    for index in range(_ROWS):
        opened = fixture.scope.start_utc + timedelta(minutes=index)
        closed = opened + timedelta(minutes=1)
        close = Decimal("1.10000") + Decimal(index) * Decimal("0.00010")
        rows.append(
            {
                "instrument": "EURUSD",
                "ts_open": opened,
                "ts_close": closed,
                "ts_recv": closed,
                "available_at": closed,
                "open": close - Decimal("0.00005"),
                "high": close + Decimal("0.00020"),
                "low": close - Decimal("0.00020"),
                "close": close,
                "volume": None,
                "volume_kind": None,
                "n_ticks": 10,
                "spread_mean": Decimal("0.00020"),
                "spread_max": Decimal("0.00020"),
                "bid_close": close - Decimal("0.00010"),
                "ask_close": close + Decimal("0.00010"),
                "source": fixture.scope.source,
                "seq": index + 1,
                "quality_flags": [],
            }
        )
    source_bytes = canonical_bytes({"kind": "invented-test-source", "rows": rows})
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    (root / "invented-source.json").write_bytes(source_bytes)
    path = clean_bar_path(
        root,
        venue=fixture.scope.venue,
        timeframe=fixture.scope.timeframe,
        instrument="EURUSD",
        month=fixture.scope.start_utc.date(),
        corpus_id=source_sha,
    )
    with ImmutableParquetWriter(
        path,
        CLEAN_BAR_SCHEMA,
        identity={
            "tradebot.kind": "clean-bar",
            "tradebot.venue": fixture.scope.venue,
            "tradebot.timeframe": fixture.scope.timeframe,
            "tradebot.instrument": "EURUSD",
            "tradebot.month": "2020-01",
            "tradebot.corpus_id": source_sha,
            "tradebot.source": fixture.scope.source,
        },
    ) as writer:
        writer.write_rows(rows)
    files = file_manifest((path,), relative_to=root)
    fixture.spec = SnapshotSpec(
        fixture.scope.venue, fixture.scope.timeframe, files, dataset_id(files)
    )
    parents = [
        {
            "id": "invented-EURUSD-2020-01",
            "sha256": source_sha,
            "eligibility": "APPROVED_FOR_PURPOSE",
        }
    ]
    lineage = [{"file": asdict(files[0]), "source_partitions": parents}]
    fixture.release["purpose"] = str(purpose)
    fixture.release["snapshot"] = {"schema_version": 1, **asdict(fixture.spec)}
    fixture.release["lineage"] = lineage
    for role in (*ROLES, "candidate", "producer_inventory"):
        fixture.set_evidence(role, "purpose", str(purpose))
        fixture.set_evidence(role, "dataset_id", fixture.spec.dataset_id)
    fixture.set_evidence("admission", "partitions", parents)
    fixture.set_evidence(
        "candidate", "partitions", [{"id": parents[0]["id"], "sha256": source_sha}]
    )
    fixture.set_evidence("producer_inventory", "lineage", lineage)
    return fixture


def _open(fixture: Fixture, registry: TrustedReleaseRegistry) -> ApprovedSnapshotStream:
    return open_approved_snapshot(
        root=fixture.root,
        spec=fixture.spec,
        purpose=fixture.purpose,
        trusted_registry=registry,
        requested_scope=fixture.scope,
        release_path=fixture.root / "release.json",
        evidence_root=fixture.root,
        known_at=KNOWN,
    )


def _provenance(fixture: Fixture) -> ReplayProvenance:
    return ReplayProvenance(
        fixture.spec.dataset_id, "immutable_clean_snapshot", fixture.spec.files, "UNCOMMITTED"
    )


def _state(stream: ApprovedSnapshotStream) -> tuple[int, bool]:
    """Read mutable stream progress without treating property values as permanent."""
    return stream.consumed_records, stream.completed


@pytest.mark.parametrize("purpose", list(ResearchPurpose))
def test_real_components_publish_only_the_complete_invented_authorized_snapshot(
    tmp_path: Path, purpose: ResearchPurpose
) -> None:
    fixture = _payload_fixture(tmp_path, purpose)
    registry = fixture.pin()
    config = ReplayConfig((f"{fixture.scope.venue}/EURUSD",), 60)
    output = tmp_path / "engineering-output"
    stream = _open(fixture, registry)
    assert _state(stream) == (0, False)
    published = publish_replay(
        stream, config, _provenance(fixture), output_root=output, purpose=purpose
    )
    assert _state(stream) == (_ROWS, True)
    stream.verify_completed()
    stream.close()

    report = json.loads((published.directory / "report.json").read_bytes())
    assert sha256_path(published.directory / "report.json") == published.report_sha256
    assert sha256_path(published.directory / "decisions.jsonl") == published.decisions_sha256
    assert report["bars_processed"] == _ROWS
    assert report["decisions_by_status"]["forecast"] > 0
    assert report["identity"]["provenance"]["dataset_id"] == fixture.spec.dataset_id
    assert report["identity"]["provenance"]["source_manifest"] == [
        asdict(item) for item in fixture.spec.files
    ]
    use = report["identity"]["authorized_use"]
    assert use["purpose"] == purpose.value
    assert use["release_sha256"] == sha256_path(tmp_path / "release.json")
    assert use["registry_sha256"] == sha256_path(tmp_path / "registry.json")
    assert use["scope"]["source"] == fixture.scope.source
    assert use["human_identities_authenticated"] is False
    assert report["economic_evaluation"] == "NOT_PERFORMED"
    assert report["execution_enabled"] is False
    assert report["pnl_reported"] is False
    assert report["gate_approvals_claimed"] == []
    trace = [
        json.loads(line)
        for line in (published.directory / "decisions.jsonl").read_bytes().splitlines()
    ]
    assert len(trace) == _ROWS
    assert [row["seq"] for row in trace] == list(range(1, _ROWS + 1))
    assert {row["source"] for row in trace} == {fixture.scope.source}
    assert {row["instrument"] for row in trace} == set(config.instruments)
    assert sha256_path(tmp_path / fixture.spec.files[0].path) == fixture.spec.files[0].sha256

    repeated_stream = _open(fixture, registry)
    repeated = publish_replay(
        repeated_stream, config, _provenance(fixture), output_root=output, purpose=purpose
    )
    assert repeated == published
    assert _state(repeated_stream) == (_ROWS, True)
    repeated_stream.close()
    pointer = json.loads((output / "latest.json").read_bytes())
    assert pointer["sha256"] == published.report_sha256


@pytest.mark.parametrize("mismatch", ["authorization_metadata", "unrelated_provenance"])
def test_actual_approved_token_cannot_bypass_metadata_or_provenance_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    fixture = _payload_fixture(tmp_path, ResearchPurpose.STRATEGY_TRAINING)
    stream = _open(fixture, fixture.pin())
    provenance = _provenance(fixture)
    if mismatch == "authorization_metadata":
        evidence = tmp_path / "gate2.json"
        evidence.write_bytes(evidence.read_bytes() + b" ")
    else:
        unrelated = (replace(fixture.spec.files[0], sha256="0" * 64),)
        provenance = ReplayProvenance(
            dataset_id(unrelated), "immutable_clean_snapshot", unrelated, "UNCOMMITTED"
        )

    def forbidden_payload_preflight(*args: object, **kwargs: object) -> None:
        raise AssertionError("denied replay must not inspect the payload")

    monkeypatch.setattr(SnapshotBarFeed, "_verify_file", forbidden_payload_preflight)
    output = tmp_path / "denied-output"
    with pytest.raises(AuthorizationError):
        publish_replay(
            stream,
            ReplayConfig((f"{fixture.scope.venue}/EURUSD",), 60),
            provenance,
            output_root=output,
            purpose=fixture.purpose,
        )
    assert not output.exists()
    assert _state(stream) == (0, False)
    stream.close()
