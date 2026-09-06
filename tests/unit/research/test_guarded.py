"""Guard behavior with invented approvals and records, never collected market data."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_authorization import make_fixture

import tradebot.research.guarded as guarded
from tradebot.core.types import Bar
from tradebot.data.storage import FileDigest, dataset_id
from tradebot.research.authorization import AuthorizationError, ResearchPurpose, ResearchScope
from tradebot.research.feed import ReplayBar, SnapshotSpec
from tradebot.research.guarded import (
    ApprovedSnapshotStream,
    open_approved_snapshot,
    run_approved_snapshot,
)

BASE = datetime(2020, 1, 1, tzinfo=UTC)
PURPOSE = ResearchPurpose.STRATEGY_TRAINING


def record(
    *, minute: int = 0, instrument: str = "fbs/EURUSD", source: str = "invented"
) -> ReplayBar:
    return ReplayBar(
        Bar(
            instrument=instrument,
            ts_open=BASE + timedelta(minutes=minute),
            ts_event=BASE + timedelta(minutes=minute + 1),
            ts_recv=BASE + timedelta(minutes=minute + 1),
            open=Decimal("1.10"),
            high=Decimal("1.11"),
            low=Decimal("1.09"),
            close=Decimal("1.10"),
            volume=None,
            spread_mean=Decimal("0.0001"),
            n_ticks=10,
        ),
        source,
        minute + 1,
    )


@dataclass
class Harness:
    """Unit seam for successful authorization; real authorization is separately tested."""

    root: Path
    scope: ResearchScope
    spec: SnapshotSpec
    rows: list[ReplayBar] = field(default_factory=lambda: [record()])
    purpose: ResearchPurpose = PURPOSE
    release_sha256: str = "c" * 64
    registry_sha256: str = "d" * 64
    known_at: datetime = BASE + timedelta(days=1)
    calls: list[str] = field(default_factory=list)
    verification_error_at: int | None = None
    verifications: int = 0
    feed_error: BaseException | None = None
    eof_error: BaseException | None = None
    records_error: BaseException | None = None
    close_error: BaseException | None = None

    def verify_unchanged(self) -> None:
        self.calls.append("verify")
        self.verifications += 1
        if self.verifications == self.verification_error_at:
            raise AuthorizationError("metadata changed")

    def kwargs(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "spec": self.spec,
            "purpose": self.purpose,
            "trusted_registry": None,
            "requested_scope": self.scope,
            "release_path": self.root / "release.json",
            "evidence_root": self.root,
            "known_at": BASE + timedelta(days=1),
        }

    def open(self) -> ApprovedSnapshotStream:
        return open_approved_snapshot(**self.kwargs())


def make_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    scope = ResearchScope(
        source="invented",
        venue="fbs",
        instruments=("EURUSD",),
        timeframe="1m",
        start_utc=BASE,
        end_utc=BASE + timedelta(hours=1),
    )
    files = (FileDigest(f"clean/bars/fbs/1m/EURUSD/2020/01/part-{'a' * 64}.parquet", "b" * 64),)
    value = Harness(tmp_path, scope, SnapshotSpec("fbs", "1m", files, dataset_id(files)))

    def authorize(*args: object, **kwargs: object) -> Harness:
        value.calls.append("authorize")
        return value

    class Feed:
        def __init__(self, root: Path, spec: SnapshotSpec) -> None:
            assert root is value.root
            assert spec is value.spec
            value.calls.append("feed")
            if value.feed_error is not None:
                raise value.feed_error

        def records(self) -> Iterator[ReplayBar]:
            value.calls.append("records")
            if value.records_error is not None:
                raise value.records_error

            def generate() -> Iterator[ReplayBar]:
                try:
                    for row in value.rows:
                        value.calls.append("yield")
                        yield row
                    value.calls.append("eof")
                    if value.eof_error is not None:
                        raise value.eof_error
                finally:
                    value.calls.append("closed")
                    if value.close_error is not None:
                        raise value.close_error

            return generate()

    monkeypatch.setattr(guarded, "authorize_snapshot", authorize)
    monkeypatch.setattr(guarded, "SnapshotBarFeed", Feed)
    return value


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return make_harness(tmp_path, monkeypatch)


def test_constructor_is_not_a_caller_approval_shortcut() -> None:
    with pytest.raises(TypeError, match="issued"):
        ApprovedSnapshotStream()


def test_authorization_is_eager_before_any_feed_path_or_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unauthorized call reached payload or consumer")

    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(guarded, "SnapshotBarFeed", forbidden)
    scope = ResearchScope(
        source="invented",
        venue="fbs",
        instruments=("EURUSD",),
        timeframe="1m",
        start_utc=BASE,
        end_utc=BASE + timedelta(days=1),
    )
    files = (FileDigest(f"clean/bars/fbs/1m/EURUSD/2020/01/part-{'a' * 64}.parquet", "b" * 64),)
    kwargs = dict(
        root=tmp_path,
        spec=SnapshotSpec("fbs", "1m", files, dataset_id(files)),
        purpose=PURPOSE,
        trusted_registry=None,
        requested_scope=scope,
        release_path=tmp_path / "missing.json",
        evidence_root=tmp_path,
        known_at=BASE + timedelta(days=2),
    )
    with pytest.raises(AuthorizationError, match="trust"):
        open_approved_snapshot(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(AuthorizationError, match="trust"):
        run_approved_snapshot(forbidden, **kwargs)  # type: ignore[arg-type]


def test_stream_is_lazy_after_eager_authorization_and_readonly(harness: Harness) -> None:
    stream = harness.open()
    assert harness.calls == ["authorize"]
    assert stream.spec is harness.spec
    assert stream.scope is harness.scope
    assert stream.purpose is PURPOSE
    assert stream.release_sha256 == harness.release_sha256
    assert stream.registry_sha256 == harness.registry_sha256
    assert stream.known_at == harness.known_at
    assert not bool(stream.completed)
    assert iter(stream) is stream
    assert harness.calls == ["authorize"]
    for name, value in (
        ("spec", harness.spec),
        ("scope", harness.scope),
        ("purpose", PURPOSE),
        ("completed", True),
    ):
        with pytest.raises(AttributeError):
            setattr(stream, name, value)
    stream.validate_request(instruments=("fbs/EURUSD",), timeframe_seconds=60, purpose=PURPOSE)
    assert harness.calls == ["authorize", "verify"]
    assert next(stream) is harness.rows[0]
    assert not bool(stream.completed)
    assert harness.calls == ["authorize", "verify", "verify", "feed", "records", "yield"]
    with pytest.raises(StopIteration):
        next(stream)
    assert stream.completed
    assert harness.calls[-3:] == ["eof", "closed", "verify"]
    assert list(stream) == []
    stream.close()
    assert stream.completed


@pytest.mark.parametrize(
    "overrides",
    [
        {"instruments": ("fbs/GBPUSD",)},
        {"instruments": ("other/EURUSD",)},
        {"instruments": ("fbs/EURUSD", "fbs/GBPUSD")},
        {"instruments": ["fbs/EURUSD"]},
        {"instruments": ("fbs/EURUSD", "fbs/EURUSD")},
        {"instruments": ()},
        {"timeframe_seconds": 300},
        {"timeframe_seconds": True},
        {"timeframe_seconds": 60.0},
        {"purpose": ResearchPurpose.ECONOMIC_EVALUATION},
        {"purpose": "STRATEGY_TRAINING"},
    ],
)
def test_request_mismatch_fails_before_iterator_and_latches(
    harness: Harness, overrides: dict[str, Any]
) -> None:
    stream = harness.open()
    kwargs: dict[str, Any] = {
        "instruments": ("fbs/EURUSD",),
        "timeframe_seconds": 60,
        "purpose": PURPOSE,
    } | overrides
    with pytest.raises(AuthorizationError):
        stream.validate_request(**kwargs)
    assert "feed" not in harness.calls
    with pytest.raises(AuthorizationError, match="failed"):
        next(stream)
    assert not stream.completed


@pytest.mark.parametrize("where", ["first", "request", "eof"])
def test_metadata_reverification_failure_blocks_and_latches(harness: Harness, where: str) -> None:
    stream = harness.open()
    harness.verification_error_at = 2 if where == "eof" else 1
    with pytest.raises(AuthorizationError, match="metadata changed"):
        if where == "request":
            stream.validate_request(
                instruments=("fbs/EURUSD",), timeframe_seconds=60, purpose=PURPOSE
            )
        else:
            list(stream)
    assert not stream.completed
    if where != "eof":
        assert "feed" not in harness.calls
    else:
        assert "closed" in harness.calls
    with pytest.raises(AuthorizationError, match="failed"):
        next(stream)


@pytest.mark.parametrize(
    "invalid",
    [
        record(source="wrong"),
        record(instrument="other/EURUSD"),
        record(instrument="fbs/GBPUSD"),
        record(minute=-1),
        record(minute=60),
        replace(record(), bar=replace(record().bar, ts_open=BASE - timedelta(seconds=1))),
        replace(record(), bar=replace(record().bar, ts_recv=BASE + timedelta(days=2))),
    ],
)
def test_each_out_of_scope_record_rejects_without_filtering(
    harness: Harness, invalid: ReplayBar
) -> None:
    harness.rows = [record(), invalid, record(minute=1)]
    stream = harness.open()
    assert next(stream) is harness.rows[0]
    with pytest.raises(AuthorizationError):
        next(stream)
    assert harness.calls.count("yield") == 2
    assert harness.calls[-1] == "closed"
    assert not stream.completed
    with pytest.raises(AuthorizationError, match="failed"):
        next(stream)


def test_scope_allows_exact_open_start_and_close_end(harness: Harness) -> None:
    harness.scope = replace(harness.scope, end_utc=BASE + timedelta(minutes=1))
    stream = harness.open()
    assert list(stream) == harness.rows
    assert stream.completed


def test_empty_feed_is_not_completed(harness: Harness) -> None:
    harness.rows = []
    stream = harness.open()
    with pytest.raises(AuthorizationError, match="empty"):
        list(stream)
    assert not stream.completed
    assert harness.calls[-1] == "verify"


@pytest.mark.parametrize("field", ["feed_error", "records_error", "eof_error"])
@pytest.mark.parametrize(
    "error", [ValueError("payload integrity failure"), KeyboardInterrupt("interrupted")]
)
def test_payload_exceptions_latch_and_do_not_complete(
    harness: Harness, field: str, error: BaseException
) -> None:
    setattr(harness, field, error)
    stream = harness.open()
    with pytest.raises(type(error), match=str(error)):
        list(stream)
    assert not stream.completed
    with pytest.raises(AuthorizationError, match="failed"):
        next(stream)


@pytest.mark.parametrize("take", [0, 1, 2])
def test_partial_consumers_fail_without_draining(harness: Harness, take: int) -> None:
    harness.rows = [record(), record(minute=1)]
    retained: list[ApprovedSnapshotStream] = []

    def consume(stream: ApprovedSnapshotStream) -> str:
        retained.append(stream)
        for _ in range(take):
            next(stream)
        return "not a complete result"

    with pytest.raises(AuthorizationError, match="complete"):
        run_approved_snapshot(consume, **harness.kwargs())
    assert harness.calls.count("yield") == take
    assert "eof" not in harness.calls
    assert not retained[0].completed
    if take:
        assert harness.calls[-1] == "closed"
    with pytest.raises(AuthorizationError, match="failed"):
        next(retained[0])


def test_complete_consumer_returns_result(harness: Harness) -> None:
    assert run_approved_snapshot(lambda stream: tuple(stream), **harness.kwargs()) == tuple(
        harness.rows
    )
    assert harness.calls[-1] == "verify"


def test_consumer_error_closes_active_iterator_and_preserves_error(harness: Harness) -> None:
    def consume(stream: ApprovedSnapshotStream) -> None:
        next(stream)
        raise RuntimeError("consumer failure")

    with pytest.raises(RuntimeError, match="consumer failure"):
        run_approved_snapshot(consume, **harness.kwargs())
    assert harness.calls[-1] == "closed"


def test_close_before_start_is_incomplete_idempotent_and_does_not_open_feed(
    harness: Harness,
) -> None:
    stream = harness.open()
    stream.close()
    stream.close()
    assert harness.calls == ["authorize"]
    assert not stream.completed
    with pytest.raises(AuthorizationError, match="closed"):
        next(stream)


def test_close_after_one_record_closes_without_marking_completion(harness: Harness) -> None:
    stream = harness.open()
    next(stream)
    stream.close()
    assert harness.calls[-1] == "closed"
    assert not stream.completed


def test_consumer_metadata_mutation_after_eof_cannot_return_result(harness: Harness) -> None:
    def consume(stream: ApprovedSnapshotStream) -> str:
        list(stream)
        harness.verification_error_at = harness.verifications + 1
        return "should not escape"

    with pytest.raises(AuthorizationError, match="metadata changed"):
        run_approved_snapshot(consume, **harness.kwargs())


def test_real_authority_token_and_fake_payload_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the payload reader is replaced; all invented trust receipts are verified."""
    setup = make_fixture(tmp_path)
    registry = setup.pin()
    rows = [record(instrument=f"{setup.scope.venue}/EURUSD", source=setup.scope.source)]
    calls: list[str] = []

    class Feed:
        def __init__(self, root: Path, spec: SnapshotSpec) -> None:
            assert root == setup.root
            assert spec == setup.spec
            calls.append("feed")

        def records(self) -> Iterator[ReplayBar]:
            calls.append("records")
            return iter(rows)

    monkeypatch.setattr(guarded, "SnapshotBarFeed", Feed)
    stream = open_approved_snapshot(
        root=setup.root,
        spec=setup.spec,
        purpose=PURPOSE,
        trusted_registry=registry,
        requested_scope=setup.scope,
        release_path=setup.root / "release.json",
        evidence_root=setup.root,
        known_at=BASE + timedelta(days=61),
    )
    assert calls == []
    assert stream.release_sha256 == setup.authorize(registry).release_sha256
    stream.validate_request(
        instruments=(f"{setup.scope.venue}/EURUSD",),
        timeframe_seconds=60,
        purpose=PURPOSE,
    )
    assert calls == []
    assert list(stream) == rows
    assert calls == ["feed", "records"]
    assert stream.completed
    assert not (setup.root / setup.spec.files[0].path).exists()


def test_real_authority_metadata_mutation_prevents_fake_payload_constructor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = make_fixture(tmp_path)
    registry = setup.pin()
    stream = open_approved_snapshot(
        root=setup.root,
        spec=setup.spec,
        purpose=PURPOSE,
        trusted_registry=registry,
        requested_scope=setup.scope,
        release_path=setup.root / "release.json",
        evidence_root=setup.root,
        known_at=BASE + timedelta(days=61),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("modified evidence reached the payload reader")

    monkeypatch.setattr(guarded, "SnapshotBarFeed", forbidden)
    (setup.root / "gate2.json").write_bytes(b"{}\n")
    with pytest.raises(AuthorizationError):
        next(stream)
    assert not stream.completed


@pytest.mark.parametrize(
    "field", ["release_sha256", "registry_sha256", "known_at", "consumed_records"]
)
def test_authority_identity_properties_are_readonly(harness: Harness, field: str) -> None:
    with pytest.raises(AttributeError):
        setattr(harness.open(), field, "changed")


def test_non_record_payload_rejects_and_closes(harness: Harness) -> None:
    harness.rows = [object()]  # type: ignore[list-item]
    stream = harness.open()
    with pytest.raises(AuthorizationError, match="non-ReplayBar"):
        next(stream)
    assert not stream.completed
    assert harness.calls[-1] == "closed"


@pytest.mark.parametrize("complete", [False, True])
def test_consumed_stream_cannot_start_new_consumer(harness: Harness, complete: bool) -> None:
    stream = harness.open()
    if complete:
        list(stream)
    else:
        next(stream)
    with pytest.raises(AuthorizationError, match="pristine"):
        stream.validate_request(instruments=("fbs/EURUSD",), timeframe_seconds=60, purpose=PURPOSE)
    assert not stream.completed
    with pytest.raises(AuthorizationError, match="failed"):
        next(stream)


def test_repeated_entry_checks_are_allowed_before_first_read(harness: Harness) -> None:
    stream = harness.open()
    for _ in range(2):
        stream.validate_request(instruments=("fbs/EURUSD",), timeframe_seconds=60, purpose=PURPOSE)
    assert harness.calls == ["authorize", "verify", "verify"]
    assert list(stream) == harness.rows
    stream.verify_completed()
    stream.close()
    stream.verify_completed()
    assert stream.completed


@pytest.mark.parametrize("take", [0, 1])
def test_completion_check_rejects_incomplete_stream_and_latches(
    harness: Harness, take: int
) -> None:
    stream = harness.open()
    if take:
        next(stream)
    with pytest.raises(AuthorizationError, match="not complete"):
        stream.verify_completed()
    assert not stream.completed
    with pytest.raises(AuthorizationError, match="failed"):
        next(stream)


def test_completion_check_rejects_post_eof_metadata_changes(harness: Harness) -> None:
    stream = harness.open()
    list(stream)
    harness.verification_error_at = harness.verifications + 1
    with pytest.raises(AuthorizationError, match="metadata changed"):
        stream.verify_completed()
    assert not stream.completed


def test_consumed_count_tracks_only_validated_yields(harness: Harness) -> None:
    harness.rows = [record(), record(minute=1), record(minute=2, source="wrong")]
    stream = harness.open()
    assert stream.consumed_records == 0
    next(stream)
    assert stream.consumed_records == 1
    next(stream)
    assert stream.consumed_records == 2
    with pytest.raises(AuthorizationError):
        next(stream)
    assert stream.consumed_records == 2
    assert not stream.completed


def test_cleanup_failure_preserves_primary_error_and_latches(harness: Harness) -> None:
    harness.rows = [record(source="wrong")]
    harness.close_error = RuntimeError("cleanup problem")
    stream = harness.open()
    with pytest.raises(AuthorizationError, match="source") as caught:
        next(stream)
    assert "cleanup problem" in caught.value.__notes__[0]
    assert not stream.completed
    with pytest.raises(AuthorizationError, match="failed"):
        next(stream)


def test_explicit_close_failure_latches_incomplete(harness: Harness) -> None:
    harness.close_error = RuntimeError("cleanup problem")
    stream = harness.open()
    next(stream)
    with pytest.raises(RuntimeError, match="cleanup problem"):
        stream.close()
    assert not stream.completed
    with pytest.raises(AuthorizationError, match="failed"):
        next(stream)


@pytest.mark.parametrize("count", [-1, 0, 2, True, 1.0, "1"])
def test_completion_count_mismatch_or_wrong_type_latches_failed(
    harness: Harness, count: Any
) -> None:
    stream = harness.open()
    list(stream)
    with pytest.raises(AuthorizationError, match="observed-record count"):
        stream.verify_completed(observed_records=count)
    assert not stream.completed
    with pytest.raises(AuthorizationError, match="failed"):
        stream.verify_completed()


def test_matching_completion_count_preserves_verified_completion(harness: Harness) -> None:
    stream = harness.open()
    rows = list(stream)
    stream.verify_completed(observed_records=len(rows))
    assert stream.completed
