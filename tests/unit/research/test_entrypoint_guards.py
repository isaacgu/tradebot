"""Public entry points cannot turn unapproved real inputs into strategy decisions."""

import json
import sys
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_guarded import Harness, make_harness, record

from tradebot.data.storage import FileDigest, dataset_id
from tradebot.research import __main__ as cli
from tradebot.research.authorization import AuthorizationError, ResearchPurpose
from tradebot.research.demo import synthetic_setup
from tradebot.research.engine import ReplayConfig, iter_decisions
from tradebot.research.feed import ReplayBar, SnapshotSpec
from tradebot.research.guarded import ApprovedSnapshotStream, run_approved_snapshot
from tradebot.research.report import ReplayProvenance, publish_replay


class ExplosiveRecords:
    def __iter__(self) -> Iterator[ReplayBar]:
        raise AssertionError("unapproved iterator must not be acquired")


def _real_config() -> ReplayConfig:
    return ReplayConfig(("fbs/EURUSD",), 60)


def _spec() -> SnapshotSpec:
    files = (
        FileDigest("clean/bars/fbs/1m/EURUSD/2024/01/part-" + "a" * 64 + ".parquet", "1" * 64),
    )
    return SnapshotSpec("fbs", "1m", files, dataset_id(files))


@pytest.mark.parametrize("purpose", [None, *ResearchPurpose])
def test_raw_real_engine_rejected_eagerly_before_iterator(
    purpose: ResearchPurpose | None,
) -> None:
    with pytest.raises(AuthorizationError):
        iter_decisions(ExplosiveRecords(), _real_config(), purpose=purpose)


@pytest.mark.parametrize("purpose", [None, *ResearchPurpose])
def test_unapproved_publisher_does_not_create_output(
    tmp_path: Path, purpose: ResearchPurpose | None
) -> None:
    spec = _spec()
    provenance = ReplayProvenance(
        spec.dataset_id, "immutable_clean_snapshot", spec.files, "UNCOMMITTED"
    )
    output = tmp_path / "output"
    with pytest.raises(AuthorizationError):
        publish_replay(
            ExplosiveRecords(), _real_config(), provenance, output_root=output, purpose=purpose
        )
    assert not output.exists()


def test_synthetic_provenance_cannot_authorize_real_config(tmp_path: Path) -> None:
    _, _, provenance = synthetic_setup("UNCOMMITTED")
    with pytest.raises(AuthorizationError):
        publish_replay(
            ExplosiveRecords(), _real_config(), provenance, output_root=tmp_path / "output"
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("purpose", list(ResearchPurpose))
def test_synthetic_engine_is_explicitly_not_training(purpose: ResearchPurpose) -> None:
    _, config, _ = synthetic_setup("UNCOMMITTED")
    with pytest.raises(AuthorizationError):
        iter_decisions(ExplosiveRecords(), config, purpose=purpose)


@pytest.mark.parametrize("purpose", list(ResearchPurpose))
def test_synthetic_publisher_cannot_claim_training(
    tmp_path: Path, purpose: ResearchPurpose
) -> None:
    _, config, provenance = synthetic_setup("UNCOMMITTED")
    with pytest.raises(AuthorizationError):
        publish_replay(
            ExplosiveRecords(), config, provenance, output_root=tmp_path / "output", purpose=purpose
        )
    assert not (tmp_path / "output").exists()


def test_synthetic_default_remains_engineering_and_nontraining() -> None:
    records, config, _ = synthetic_setup("UNCOMMITTED")
    assert len(tuple(iter_decisions(records, config))) == 320


def test_row_relabelled_into_real_venue_cannot_enter_synthetic_strategy() -> None:
    records, config, _ = synthetic_setup("UNCOMMITTED")
    first = replace(records[0], bar=replace(records[0].bar, instrument="fbs/EURUSD"))
    with pytest.raises(ValueError, match="unconfigured"):
        tuple(iter_decisions((first, *records[1:]), config))


def test_real_snapshot_cli_denied_before_feed_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec()
    manifest = tmp_path / "snapshot.json"
    manifest.write_text(json.dumps({"schema_version": 1, **asdict(spec)}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tradebot.research",
            "--snapshot",
            str(manifest),
            "--root",
            str(tmp_path),
            "--output-root",
            str(tmp_path / "output"),
        ],
    )

    def forbidden_feed(*args: object, **kwargs: object) -> None:
        raise AssertionError("unapproved feed factory must not run")

    monkeypatch.setattr(cli, "SnapshotBarFeed", forbidden_feed)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert not (tmp_path / "output").exists()


def test_denied_publish_preserves_existing_latest(tmp_path: Path) -> None:
    marker = tmp_path / "latest.json"
    marker.write_bytes(b"prior-engineering-pointer")
    _, _, provenance = synthetic_setup("UNCOMMITTED")
    with pytest.raises(AuthorizationError):
        publish_replay(ExplosiveRecords(), _real_config(), provenance, output_root=tmp_path)
    assert marker.read_bytes() == b"prior-engineering-pointer"


@pytest.fixture
def approved_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    value = make_harness(tmp_path, monkeypatch)
    value.rows = [record(minute=minute) for minute in range(3)]
    return value


def test_approved_engine_completes_exact_snapshot(approved_harness: Harness) -> None:
    stream = approved_harness.open()
    decisions = tuple(iter_decisions(stream, _real_config(), purpose=approved_harness.purpose))
    assert len(decisions) == stream.consumed_records == 3
    assert stream.completed
    assert "eof" in approved_harness.calls


@pytest.mark.parametrize("consume_before_driver", [False, True])
def test_prefix_or_lazy_handoff_cannot_publish_suffix(
    approved_harness: Harness, consume_before_driver: bool
) -> None:
    stream = approved_harness.open()
    if consume_before_driver:
        next(stream)
        with pytest.raises(AuthorizationError, match="pristine"):
            iter_decisions(stream, _real_config(), purpose=approved_harness.purpose)
    else:
        driver = iter_decisions(stream, _real_config(), purpose=approved_harness.purpose)
        next(stream)
        with pytest.raises(AuthorizationError, match="pristine"):
            next(driver)
    assert not stream.completed
    with pytest.raises(AuthorizationError):
        next(stream)


def test_interleaved_raw_consumer_latches_failure_at_eof(approved_harness: Harness) -> None:
    stream = approved_harness.open()
    driver = iter_decisions(stream, _real_config(), purpose=approved_harness.purpose)
    next(driver)
    next(stream)
    with pytest.raises(AuthorizationError, match="observed-record count"):
        tuple(driver)
    assert not stream.completed
    with pytest.raises(AuthorizationError):
        stream.verify_completed()


def test_two_drivers_cannot_share_snapshot(approved_harness: Harness) -> None:
    stream = approved_harness.open()
    first = iter_decisions(stream, _real_config(), purpose=approved_harness.purpose)
    second = iter_decisions(stream, _real_config(), purpose=approved_harness.purpose)
    next(first)
    with pytest.raises(AuthorizationError, match="pristine"):
        next(second)
    with pytest.raises(AuthorizationError):
        tuple(first)
    assert not stream.completed


def test_swallowed_interleaving_error_cannot_complete_runner(approved_harness: Harness) -> None:
    def consumer(stream: ApprovedSnapshotStream) -> str:
        driver = iter_decisions(stream, _real_config(), purpose=approved_harness.purpose)
        next(driver)
        next(stream)
        with pytest.raises(AuthorizationError, match="observed-record count"):
            tuple(driver)
        return "not-a-valid-completion"

    with pytest.raises(AuthorizationError, match="did not complete"):
        run_approved_snapshot(consumer, **approved_harness.kwargs())


@pytest.mark.parametrize("wrong_kind", [False, True])
def test_approved_stream_rejects_unrelated_provenance_before_output(
    approved_harness: Harness, tmp_path: Path, wrong_kind: bool
) -> None:
    spec = approved_harness.spec if wrong_kind else _spec()
    provenance = ReplayProvenance(
        spec.dataset_id,
        "synthetic" if wrong_kind else "immutable_clean_snapshot",
        spec.files,
        "UNCOMMITTED",
    )
    with pytest.raises(AuthorizationError, match="provenance"):
        publish_replay(
            approved_harness.open(),
            _real_config(),
            provenance,
            output_root=tmp_path / "output",
            purpose=approved_harness.purpose,
        )
    assert "feed" not in approved_harness.calls
    assert not (tmp_path / "output").exists()


def test_approved_report_binds_authority_without_claiming_training(
    approved_harness: Harness, tmp_path: Path
) -> None:
    spec = approved_harness.spec
    result = publish_replay(
        approved_harness.open(),
        _real_config(),
        ReplayProvenance(spec.dataset_id, "immutable_clean_snapshot", spec.files, "UNCOMMITTED"),
        output_root=tmp_path / "output",
        purpose=approved_harness.purpose,
    )
    report = json.loads((result.directory / "report.json").read_text())
    authority = report["identity"]["authorized_use"]
    assert authority["purpose"] == approved_harness.purpose.value
    assert authority["release_sha256"] == approved_harness.release_sha256
    assert authority["registry_sha256"] == approved_harness.registry_sha256
    assert authority["human_identities_authenticated"] is False
    assert report["bars_processed"] == 3
    assert report["economic_evaluation"] == "NOT_PERFORMED"
    assert report["gate_approvals_claimed"] == []
    assert report["execution_enabled"] is False
    assert any("No strategy fitting" in caveat for caveat in report["caveats"])


class ImpersonatedStream(ApprovedSnapshotStream):
    """Public subclass overrides cannot replace the factory's approval checks."""

    def __init__(self) -> None:
        self.rows = iter([record()])

    def __next__(self) -> ReplayBar:
        return next(self.rows)

    def validate_request(
        self, *, instruments: tuple[str, ...], timeframe_seconds: int, purpose: ResearchPurpose
    ) -> None:
        pass

    def verify_completed(self, *, observed_records: int | None = None) -> None:
        pass


def test_public_subclass_cannot_impersonate_factory_issued_stream() -> None:
    with pytest.raises(AuthorizationError, match="approved snapshot"):
        tuple(
            iter_decisions(
                ImpersonatedStream(), _real_config(), purpose=ResearchPurpose.STRATEGY_TRAINING
            )
        )
