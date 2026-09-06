"""Read-only pre-trading check of the local broker observer against the demo baseline.

No MetaTrader import, login, order, balance adjustment, or execution enablement.
This is a starting-account check, not a trading safety controller or gate approval.
Only comparison results are retained: no account identifiers or position details.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _money(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("account amount must be a finite number")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid account amount") from exc
    if not amount.is_finite():
        raise ValueError("account amount must be finite")
    return amount


def verify_baseline(
    profile: dict[str, Any], state: dict[str, Any], *, now: float
) -> dict[str, bool]:
    """Require a fresh, flat, demo starting snapshot; never treat missing as zero."""
    if (
        type(profile.get("schema_version")) is not int
        or profile["schema_version"] != 1
        or profile.get("account_kind") != "demo"
        or profile.get("execution_enabled") is not False
        or profile.get("currency") != "USD"
        or profile.get("broker_server") != "FBS-Demo"
        or _money(profile.get("initial_balance")) <= 0
    ):
        raise ValueError("profile must be an execution-disabled positive-USD FBS demo baseline")
    snapshot = state.get("snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    account = snapshot.get("account")
    account = account if isinstance(account, dict) else {}
    timestamp = snapshot.get("completed_at_seconds")
    # Match the observer's minimum freshness bound. This is an operational read
    # check, not a new portfolio risk or gate threshold. Future snapshots fail.
    fresh = (
        isinstance(timestamp, (int, float))
        and not isinstance(timestamp, bool)
        and math.isfinite(timestamp)
        and 0 <= now - timestamp <= 15
    )
    amount_matches: dict[str, bool] = {}
    for name, expected in (
        ("balance", _money(profile["initial_balance"])),
        ("equity", _money(profile["initial_balance"])),
        ("margin", Decimal(0)),
    ):
        try:
            amount_matches[name] = _money(account.get(name)) == expected
        except ValueError:
            amount_matches[name] = False
    return {
        "observer_read_only": state.get("read_only") is True,
        "snapshot_available": state.get("snapshot_available") is True,
        "snapshot_not_stale": state.get("snapshot_stale") is False,
        "snapshot_age_within_15_seconds": fresh,
        "ipc_not_poisoned": state.get("ipc_poisoned") is False,
        "account_identity_unchanged": state.get("account_changed") is False,
        "demo_account": account.get("account_kind") == profile["account_kind"],
        "broker_server_matches": account.get("server") == profile["broker_server"],
        "currency_matches": account.get("currency") == profile["currency"],
        "starting_balance_matches": amount_matches["balance"],
        "starting_equity_matches": amount_matches["equity"],
        "no_used_margin": amount_matches["margin"],
        "no_positions": snapshot.get("positions") == [],
        "no_pending_orders": snapshot.get("orders") == [],
    }


def read_observer() -> dict[str, Any]:
    """Read only the fixed loopback observer; no proxy, redirect, or credential use."""
    connection = http.client.HTTPConnection("127.0.0.1", 8766, timeout=15)
    try:
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"observer HTTP status {response.status}")
        payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("observer response exceeds bounded size")
        value: object = json.loads(payload, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise ValueError("observer response is not an object")
        return value
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.exit(2, "Refusing to overwrite an existing verification record.\n")
    try:
        encoded = args.profile.read_bytes()
        profile = json.loads(encoded, object_pairs_hook=_unique_object)
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
        checks = verify_baseline(profile, read_observer(), now=time.time())
    except (ValueError, OSError, http.client.HTTPException) as exc:
        parser.exit(2, f"Demo baseline could not be verified: {type(exc).__name__}\n")
    passed = all(checks.values())
    report = {
        "schema_version": 1,
        "status": "VERIFIED" if passed else "MISMATCH",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "profile_sha256": hashlib.sha256(encoded).hexdigest(),
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "checks": checks,
        "execution_enabled": False,
        "gate_approval": False,
        "scope": "Fresh demo starting-account comparison only; no trading action or approval.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(f"{report['status']}: {sum(checks.values())}/{len(checks)} checks; {args.output}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
