"""Purpose-authorized snapshot consumption with full, explicit completion checks.

This is an operator-pinned trust workflow, not an arbitrary-Python sandbox. It
does not create approvals, train a strategy, or calculate economic results.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

from tradebot.research.authorization import (
    ApprovedSnapshot,
    AuthorizationError,
    ResearchPurpose,
    ResearchScope,
    TrustedReleaseRegistry,
    authorize_snapshot,
)
from tradebot.research.feed import ReplayBar, SnapshotBarFeed, SnapshotSpec

# These are elapsed-duration bars supported by the existing research CLI. A
# safely named SnapshotSpec timeframe is not itself a supported duration.
_TIMEFRAMES = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


class ApprovedSnapshotStream(Iterator[ReplayBar]):
    """Factory-issued, single-pass stream over exactly one authorized snapshot.

    Merely yielding the last row does not complete this stream: the caller must
    request EOF so the underlying reader and authorization metadata reverify.
    """

    __slots__ = (
        "_approved",
        "_closed",
        "_completed",
        "_failed",
        "_iterator",
        "_known_at",
        "_root",
        "_seconds",
        "_seen",
    )

    _approved: ApprovedSnapshot
    _closed: bool
    _completed: bool
    _failed: bool
    _iterator: Iterator[ReplayBar] | None
    _known_at: datetime
    _root: Path
    _seconds: int
    _seen: int

    def __init__(self) -> None:
        raise TypeError("ApprovedSnapshotStream is issued only by open_approved_snapshot")

    @property
    def spec(self) -> SnapshotSpec:
        """Return the exact immutable selected-file manifest approved for this run."""
        return self._approved.spec

    @property
    def scope(self) -> ResearchScope:
        """Return the approved source, venue, instruments, timeframe and interval."""
        return self._approved.scope

    @property
    def purpose(self) -> ResearchPurpose:
        """Return the exact authorized research purpose, never an inferred purpose."""
        return self._approved.purpose

    @property
    def release_sha256(self) -> str:
        """Return the hash of the verified release, not an approval assertion."""
        return self._approved.release_sha256

    @property
    def registry_sha256(self) -> str:
        """Return the independently pinned registry identity used for this stream."""
        return self._approved.registry_sha256

    @property
    def known_at(self) -> datetime:
        """Return the explicit UTC evidence and observation availability cutoff."""
        return self._known_at

    @property
    def completed(self) -> bool:
        """True only after nonempty full consumption and successful EOF verification."""
        return self._completed

    @property
    def consumed_records(self) -> int:
        """Count validated rows yielded by this stream, including all consumers."""
        return self._seen

    def _ensure_usable(self) -> None:
        if self._failed:
            raise AuthorizationError("approved snapshot stream has failed")
        if self._closed and not self._completed:
            raise AuthorizationError("approved snapshot stream is closed and incomplete")

    def _abort(self, error: BaseException) -> None:
        self._failed = True
        self._completed = False
        try:
            self.close()
        except BaseException as cleanup_error:
            error.add_note(f"snapshot iterator cleanup also failed: {cleanup_error!r}")

    def validate_request(
        self,
        *,
        instruments: tuple[str, ...],
        timeframe_seconds: int,
        purpose: ResearchPurpose,
    ) -> None:
        """Verify a pristine stream's purpose/configuration before any consumption.

        Repeated checks before the first read are permitted. Once iteration has
        started, a new consumer cannot claim the full snapshot while reading
        only its suffix. Use verify_completed for final evidence verification.
        """
        self._ensure_usable()
        try:
            if self._iterator is not None or self._seen != 0 or self._completed:
                raise AuthorizationError("consumer requires a pristine, unconsumed snapshot")
            expected = tuple(f"{self.scope.venue}/{name}" for name in self.scope.instruments)
            if type(purpose) is not ResearchPurpose or purpose is not self.purpose:
                raise AuthorizationError("request purpose differs from approved purpose")
            if type(instruments) is not tuple or instruments != expected:
                raise AuthorizationError("request instruments differ from approved scope")
            if type(timeframe_seconds) is not int or timeframe_seconds != self._seconds:
                raise AuthorizationError("request timeframe differs from approved scope")
            self._approved.verify_unchanged()
        except BaseException as error:
            self._abort(error)
            raise

    def verify_completed(self, *, observed_records: int | None = None) -> None:
        """Verify full EOF, optional consumer row accounting, and unchanged evidence.

        A driver can bind its own processed count to the stream's total, so an
        interleaved reader cannot consume rows behind that driver's accounting.
        """
        self._ensure_usable()
        try:
            if not self._completed:
                raise AuthorizationError("approved snapshot consumption is not complete")
            if observed_records is not None and (
                type(observed_records) is not int
                or observed_records < 0
                or observed_records != self._seen
            ):
                raise AuthorizationError(
                    "consumer observed-record count differs from full snapshot"
                )
            self._approved.verify_unchanged()
        except BaseException as error:
            self._abort(error)
            raise

    def __iter__(self) -> ApprovedSnapshotStream:
        return self

    def __next__(self) -> ReplayBar:
        self._ensure_usable()
        if self._completed:
            raise StopIteration
        try:
            if self._iterator is None:
                # Authorization and metadata checks precede even root resolution
                # in SnapshotBarFeed.__init__, not merely its first payload read.
                self._approved.verify_unchanged()
                self._iterator = iter(SnapshotBarFeed(self._root, self.spec).records())
            try:
                row = next(self._iterator)
            except StopIteration:
                self._approved.verify_unchanged()
                if self._seen == 0:
                    raise AuthorizationError("approved snapshot is empty") from None
                self._completed = True
            else:
                self._validate_record(row)
                self._seen += 1
                return row
        except BaseException as error:
            self._abort(error)
            raise
        raise StopIteration

    def _validate_record(self, row: ReplayBar) -> None:
        if not isinstance(row, ReplayBar):
            raise AuthorizationError("snapshot yielded a non-ReplayBar record")
        expected = tuple(f"{self.scope.venue}/{name}" for name in self.scope.instruments)
        bar = row.bar
        if row.source != self.scope.source:
            raise AuthorizationError("record source differs from approved scope")
        if bar.instrument not in expected:
            raise AuthorizationError("record venue or instrument differs from approved scope")
        if bar.ts_close - bar.ts_open != timedelta(seconds=self._seconds):
            raise AuthorizationError("record duration differs from approved timeframe")
        if bar.ts_open < self.scope.start_utc or bar.ts_close > self.scope.end_utc:
            raise AuthorizationError("record interval lies outside approved scope")
        if max(bar.ts_event, bar.ts_recv, bar.available_at) > self._known_at:
            raise AuthorizationError("record availability exceeds the known_at cutoff")

    def close(self) -> None:
        """Close without draining; a partial or failed stream remains incomplete."""
        if self._closed:
            return
        self._closed = True
        iterator, self._iterator = self._iterator, None
        closer = getattr(iterator, "close", None)
        if callable(closer):
            try:
                closer()
            except BaseException:
                self._failed = True
                self._completed = False
                raise


def open_approved_snapshot(
    *,
    root: Path,
    spec: SnapshotSpec,
    purpose: ResearchPurpose,
    trusted_registry: TrustedReleaseRegistry | None,
    requested_scope: ResearchScope,
    release_path: Path,
    evidence_root: Path,
    known_at: datetime,
) -> ApprovedSnapshotStream:
    """Authorize eagerly, then issue a lazy stream bound to that exact snapshot.

    No caller-supplied iterable or feed factory is accepted. Production reads
    always use SnapshotBarFeed with its existing full preflight and EOF hashes.
    """
    approved = authorize_snapshot(
        spec,
        purpose=purpose,
        trusted_registry=trusted_registry,
        requested_scope=requested_scope,
        release_path=release_path,
        evidence_root=evidence_root,
        known_at=known_at,
    )
    seconds = _TIMEFRAMES.get(approved.scope.timeframe)
    if seconds is None:
        raise AuthorizationError("approved snapshot timeframe is unsupported by research")
    stream = object.__new__(ApprovedSnapshotStream)
    stream._approved = approved
    stream._closed = False
    stream._completed = False
    stream._failed = False
    stream._iterator = None
    stream._known_at = approved.known_at
    stream._root = root
    stream._seconds = seconds
    stream._seen = 0
    return stream


def run_approved_snapshot[T](
    consumer: Callable[[ApprovedSnapshotStream], T],
    *,
    root: Path,
    spec: SnapshotSpec,
    purpose: ResearchPurpose,
    trusted_registry: TrustedReleaseRegistry | None,
    requested_scope: ResearchScope,
    release_path: Path,
    evidence_root: Path,
    known_at: datetime,
) -> T:
    """Return a consumer result only after complete approved snapshot consumption.

    The consumer owns computation, not approval. This boundary rejects partial
    consumption instead of draining unused records and mislabeling it complete.
    It cannot roll back external side effects performed by a supplied callable.
    """
    stream = open_approved_snapshot(
        root=root,
        spec=spec,
        purpose=purpose,
        trusted_registry=trusted_registry,
        requested_scope=requested_scope,
        release_path=release_path,
        evidence_root=evidence_root,
        known_at=known_at,
    )
    try:
        result = consumer(stream)
        if not stream.completed:
            raise AuthorizationError("consumer did not complete the approved snapshot")
        stream.verify_completed()
        return result
    except BaseException as error:
        stream._abort(error)
        raise
    finally:
        stream.close()
