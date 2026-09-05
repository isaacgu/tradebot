from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

verifier = importlib.import_module("scripts.verify_demo_baseline")


@pytest.fixture
def profile() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[3] / "configs/accounts/demo_usd_1000.json"
    result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    assert result["initial_balance"] == "1000.00"
    assert result["planned_live_initial_balance"] == "1000.00"
    return result


@pytest.fixture
def state() -> dict[str, Any]:
    return {
        "read_only": True,
        "snapshot_available": True,
        "snapshot_stale": False,
        "ipc_poisoned": False,
        "account_changed": False,
        "snapshot": {
            "completed_at_seconds": 99.0,
            "account": {
                "account_kind": "demo",
                "server": "FBS-Demo",
                "currency": "USD",
                "balance": 1000.0,
                "equity": 1000.0,
                "margin": 0.0,
            },
            "positions": [],
            "orders": [],
        },
    }


def test_current_profile_and_starting_snapshot_match(
    profile: dict[str, Any], state: dict[str, Any]
) -> None:
    assert all(verifier.verify_baseline(profile, state, now=100).values())


@pytest.mark.parametrize("timestamp", [0, 101, float("nan"), float("inf"), None, "99", True])
def test_bad_snapshot_time_never_looks_fresh(
    profile: dict[str, Any], state: dict[str, Any], timestamp: object
) -> None:
    state["snapshot"]["completed_at_seconds"] = timestamp
    assert not verifier.verify_baseline(profile, state, now=100)["snapshot_age_within_15_seconds"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("balance", 20000),
        ("equity", None),
        ("margin", "NaN"),
        ("account_kind", "real"),
        ("server", "FBS-Real"),
        ("currency", "EUR"),
    ],
)
def test_mismatched_account_fails(
    profile: dict[str, Any], state: dict[str, Any], key: str, value: object
) -> None:
    state["snapshot"]["account"][key] = value
    assert not all(verifier.verify_baseline(profile, state, now=100).values())


@pytest.mark.parametrize("key", ["snapshot_stale", "ipc_poisoned", "account_changed"])
def test_observer_alarm_cannot_be_hidden_by_matching_balance(
    profile: dict[str, Any], state: dict[str, Any], key: str
) -> None:
    state[key] = True
    assert not all(verifier.verify_baseline(profile, state, now=100).values())


def test_missing_snapshot_is_not_a_flat_account(profile: dict[str, Any]) -> None:
    checks = verifier.verify_baseline(profile, {}, now=100)
    assert not any(checks.values())


@pytest.mark.parametrize("key", ["positions", "orders"])
def test_open_exposure_is_not_the_starting_baseline(
    profile: dict[str, Any], state: dict[str, Any], key: str
) -> None:
    state["snapshot"][key] = [{"ticket": 123}]
    assert not all(verifier.verify_baseline(profile, state, now=100).values())


@pytest.mark.parametrize("key,value", [("execution_enabled", True), ("initial_balance", "NaN")])
def test_invalid_profile_rejected(profile: dict[str, Any], key: str, value: object) -> None:
    profile[key] = value
    with pytest.raises(ValueError):
        verifier.verify_baseline(profile, {}, now=100)


def test_cli_creates_private_minimal_copy_once_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: dict[str, Any], state: dict[str, Any]
) -> None:
    config = tmp_path / "profile.json"
    config.write_text(json.dumps(profile), encoding="utf-8")
    output = tmp_path / "result.json"
    state["snapshot"]["account"]["login"] = "private-account"
    monkeypatch.setattr(verifier, "read_observer", lambda: state)
    monkeypatch.setattr(verifier.time, "time", lambda: 100)
    args = ["--profile", str(config), "--output", str(output)]
    assert verifier.main(args) == 0
    before = output.read_bytes()
    result = json.loads(before)
    assert result["status"] == "VERIFIED"
    assert result["gate_approval"] is False and result["execution_enabled"] is False
    assert "private-account" not in before.decode()
    with pytest.raises(SystemExit) as failure:
        verifier.main(args)
    assert failure.value.code == 2
    assert output.read_bytes() == before
