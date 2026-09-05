"""Transactional, append-only ENGINEERING_ONLY preregistrations and attempt evidence.

Every public operation validates the complete event chain and lifecycle transitions.
This detects accidental or partial changes, not an attacker rewriting the database,
head and hashes together. It cannot prove when a person first inspected input data.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from tradebot.research.report import canonical_bytes

_VERSION = 1
_GENESIS = "0" * 64
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_META_SQL = """CREATE TABLE registry_meta (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    head_sha256 TEXT NOT NULL
)"""
_EVENTS_SQL = """CREATE TABLE registry_events (
    seq INTEGER PRIMARY KEY,
    body TEXT NOT NULL,
    event_sha256 TEXT NOT NULL
)"""


class RegistryError(ValueError):
    """Invalid registration, lifecycle transition, or stored audit evidence."""


def _json_native(value: object) -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_native(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_native(item)
        return
    raise RegistryError("declaration must contain only finite JSON-native values and string keys")


def _declaration(value: object) -> dict[str, object]:
    _json_native(value)
    if not isinstance(value, dict) or value.get("evidence_class") != "ENGINEERING_ONLY":
        raise RegistryError("declaration evidence_class must be ENGINEERING_ONLY")
    return cast(dict[str, object], value)


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise RegistryError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise RegistryError(f"{name} must be 1-128 safe ASCII identifier characters")
    return value


def _artifacts(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RegistryError("artifacts must map safe labels to SHA-256 digests")
    for key, item in value.items():
        _identifier(key, "artifact label")
        _digest(item, "artifact digest")
    return cast(dict[str, str], value)


def _metadata(value: object) -> dict[str, object]:
    _json_native(value)
    if not isinstance(value, dict):
        raise RegistryError("attempt metadata must be a JSON-native dictionary")
    return cast(dict[str, object], value)


def _finish_payload(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"status", "artifacts", "error"}:
        raise RegistryError("invalid finish payload")
    status, error = value["status"], value["error"]
    if status not in ("COMPLETED", "FAILED"):
        raise RegistryError("finish status must be COMPLETED or FAILED")
    _artifacts(value["artifacts"])
    if status == "COMPLETED" and error is not None:
        raise RegistryError("COMPLETED attempts cannot have an error")
    if status == "FAILED" and (not isinstance(error, str) or not error.strip()):
        raise RegistryError("FAILED attempts require a nonblank error")
    return cast(dict[str, object], value)


class Registry:
    """Local research ledger; calls serialize through SQLite BEGIN IMMEDIATE.

    Identical registration is idempotent. Attempt identifiers are unique within an
    experiment, and duplicate starts or finishes are rejected, never overwritten.
    STARTED records remain visible after interruption. No financial trial accounting
    or lockbox access is conferred by this engineering-only ledger.
    """

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("registry path must be a pathlib.Path")
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            created = False
        else:
            os.close(descriptor)
            created = True
        # Existing empty/truncated files are invalid evidence, never a new ledger.
        # Concurrent first-time construction can fail closed until initialization
        # commits; callers can safely retry after the creator has finished.
        with self._transaction(allow_initialise=created) as connection:
            self._read_state(connection)

    @contextmanager
    def _transaction(self, *, allow_initialise: bool = False) -> Iterator[sqlite3.Connection]:
        if not allow_initialise and not self.path.is_file():
            raise RegistryError("registry database is missing")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, isolation_level=None, timeout=10.0)
            connection.execute("BEGIN IMMEDIATE")
            objects = connection.execute(
                "SELECT name, type, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if allow_initialise and not objects and version == 0:
                connection.execute(_META_SQL)
                connection.execute(_EVENTS_SQL)
                connection.execute(
                    "INSERT INTO registry_meta VALUES (1, ?, 0, ?)", (_VERSION, _GENESIS)
                )
                connection.execute("PRAGMA user_version = 1")
                objects = connection.execute(
                    "SELECT name, type, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
                version = _VERSION
            expected = {
                ("registry_meta", "table", _META_SQL),
                ("registry_events", "table", _EVENTS_SQL),
            }
            if version != _VERSION or set(objects) != expected:
                raise RegistryError("registry schema does not match version 1")
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            if connection is not None:
                connection.rollback()
            raise RegistryError(f"registry database error: {exc}") from exc
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _read_state(connection: sqlite3.Connection) -> dict[str, dict[str, object]]:
        meta_rows = connection.execute("SELECT * FROM registry_meta").fetchall()
        if len(meta_rows) != 1:
            raise RegistryError("registry metadata row is missing or duplicated")
        singleton, version, count, head = meta_rows[0]
        if singleton != 1 or version != _VERSION or type(count) is not int or count < 0:
            raise RegistryError("invalid registry metadata")
        _digest(head, "head digest")
        previous = _GENESIS
        observed = 0
        experiments: dict[str, dict[str, object]] = {}
        for sequence, body, event_hash in connection.execute(
            "SELECT seq, body, event_sha256 FROM registry_events ORDER BY seq"
        ):
            observed += 1
            if type(sequence) is not int or sequence != observed or not isinstance(body, str):
                raise RegistryError("registry sequence is not contiguous")
            _digest(event_hash, "event digest")
            try:
                event: object = json.loads(body)
                encoded = canonical_bytes(event)
            except (ValueError, TypeError) as exc:
                raise RegistryError("malformed registry event JSON") from exc
            if encoded.decode("utf-8") != body or hashlib.sha256(encoded).hexdigest() != event_hash:
                raise RegistryError("registry event canonical hash mismatch")
            if not isinstance(event, dict) or set(event) != {
                "schema_version",
                "seq",
                "previous_sha256",
                "operation",
                "experiment_id",
                "attempt_id",
                "payload",
            }:
                raise RegistryError("invalid registry event schema")
            if (
                type(event["schema_version"]) is not int
                or event["schema_version"] != _VERSION
                or type(event["seq"]) is not int
                or event["seq"] != sequence
                or event["previous_sha256"] != previous
            ):
                raise RegistryError("registry hash chain or event version is inconsistent")
            experiment_id = _digest(event["experiment_id"], "experiment_id")
            operation, attempt_id, payload = (
                event["operation"],
                event["attempt_id"],
                event["payload"],
            )
            if operation == "REGISTER":
                declaration = _declaration(payload)
                if (
                    attempt_id is not None
                    or experiment_id in experiments
                    or hashlib.sha256(canonical_bytes(declaration)).hexdigest() != experiment_id
                ):
                    raise RegistryError("invalid or duplicate preregistration")
                experiments[experiment_id] = {"declaration": declaration, "attempts": {}}
            elif operation in ("START", "FINISH"):
                _identifier(attempt_id, "attempt_id")
                if experiment_id not in experiments:
                    raise RegistryError("attempt appears before preregistration")
                attempts = cast(
                    dict[str, dict[str, object]], experiments[experiment_id]["attempts"]
                )
                attempt_id = cast(str, attempt_id)
                if operation == "START":
                    if not isinstance(payload, dict) or set(payload) != {"metadata"}:
                        raise RegistryError("invalid attempt start payload")
                    metadata = _metadata(payload["metadata"])
                    if attempt_id in attempts:
                        raise RegistryError("invalid or duplicate attempt start")
                    attempts[attempt_id] = {
                        "attempt_id": attempt_id,
                        "status": "STARTED",
                        "artifacts": {},
                        "error": None,
                        "metadata": metadata,
                    }
                else:
                    finish = _finish_payload(payload)
                    if attempt_id not in attempts or attempts[attempt_id]["status"] != "STARTED":
                        raise RegistryError("finish has no uniquely open attempt")
                    attempts[attempt_id] = {
                        "attempt_id": attempt_id,
                        "metadata": attempts[attempt_id]["metadata"],
                        **finish,
                    }
            else:
                raise RegistryError("unknown registry event operation")
            previous = event_hash
        if observed != count or previous != head:
            raise RegistryError("registry event count or head mismatch")
        return experiments

    @staticmethod
    def _append(
        connection: sqlite3.Connection,
        *,
        operation: str,
        experiment_id: str,
        attempt_id: str | None,
        payload: object,
    ) -> None:
        count, head = connection.execute(
            "SELECT event_count, head_sha256 FROM registry_meta WHERE singleton = 1"
        ).fetchone()
        event = {
            "schema_version": _VERSION,
            "seq": count + 1,
            "previous_sha256": head,
            "operation": operation,
            "experiment_id": experiment_id,
            "attempt_id": attempt_id,
            "payload": payload,
        }
        encoded = canonical_bytes(event)
        event_hash = hashlib.sha256(encoded).hexdigest()
        connection.execute(
            "INSERT INTO registry_events VALUES (?, ?, ?)",
            (count + 1, encoded.decode("utf-8"), event_hash),
        )
        connection.execute(
            "UPDATE registry_meta SET event_count = ?, head_sha256 = ? WHERE singleton = 1",
            (count + 1, event_hash),
        )
        Registry._read_state(connection)

    def register(self, declaration: dict[str, object]) -> str:
        """Commit an immutable declaration and return its content-derived identity."""
        declaration = _declaration(declaration)
        experiment_id = hashlib.sha256(canonical_bytes(declaration)).hexdigest()
        with self._transaction() as connection:
            state = self._read_state(connection)
            if experiment_id not in state:
                self._append(
                    connection,
                    operation="REGISTER",
                    experiment_id=experiment_id,
                    attempt_id=None,
                    payload=declaration,
                )
        return experiment_id

    def start_attempt(
        self,
        experiment_id: str,
        attempt_id: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Durably record an attempt before the runner opens the declared data."""
        _digest(experiment_id, "experiment_id")
        _identifier(attempt_id, "attempt_id")
        metadata = _metadata({} if metadata is None else metadata)
        with self._transaction() as connection:
            state = self._read_state(connection)
            if experiment_id not in state:
                raise RegistryError("experiment must be preregistered before an attempt")
            attempts = cast(dict[str, object], state[experiment_id]["attempts"])
            if attempt_id in attempts:
                raise RegistryError("attempt_id already exists; attempts cannot be overwritten")
            self._append(
                connection,
                operation="START",
                experiment_id=experiment_id,
                attempt_id=attempt_id,
                payload={"metadata": metadata},
            )

    def finish_attempt(
        self,
        experiment_id: str,
        attempt_id: str,
        *,
        status: str,
        artifacts: dict[str, str] | None = None,
        error: str | None = None,
    ) -> None:
        """Append a terminal result; failed and interrupted attempts stay observable."""
        _digest(experiment_id, "experiment_id")
        _identifier(attempt_id, "attempt_id")
        payload = _finish_payload(
            {
                "status": status,
                "artifacts": {} if artifacts is None else artifacts,
                "error": error,
            }
        )
        with self._transaction() as connection:
            state = self._read_state(connection)
            if experiment_id not in state:
                raise RegistryError("experiment must be preregistered before finishing")
            attempts = cast(dict[str, dict[str, object]], state[experiment_id]["attempts"])
            if attempt_id not in attempts or attempts[attempt_id]["status"] != "STARTED":
                raise RegistryError("finish requires an existing STARTED attempt")
            self._append(
                connection,
                operation="FINISH",
                experiment_id=experiment_id,
                attempt_id=attempt_id,
                payload=payload,
            )

    def audit(self, experiment_id: str) -> dict[str, object]:
        """Return a validated JSON-native snapshot; counts are attempts, not DSR trials."""
        _digest(experiment_id, "experiment_id")
        with self._transaction() as connection:
            state = self._read_state(connection)
            if experiment_id not in state:
                raise RegistryError("unknown preregistered experiment")
            experiment = state[experiment_id]
            attempts = list(cast(dict[str, dict[str, object]], experiment["attempts"]).values())
            count, head = connection.execute(
                "SELECT event_count, head_sha256 FROM registry_meta WHERE singleton = 1"
            ).fetchone()
            return {
                "schema_version": _VERSION,
                "evidence_class": "ENGINEERING_ONLY",
                "experiment_id": experiment_id,
                "declaration": experiment["declaration"],
                "attempts": attempts,
                "counts": {
                    "started": len(attempts),
                    "completed": sum(item["status"] == "COMPLETED" for item in attempts),
                    "failed": sum(item["status"] == "FAILED" for item in attempts),
                    "incomplete": sum(item["status"] == "STARTED" for item in attempts),
                },
                "registry_event_count": count,
                "registry_head_sha256": head,
            }
