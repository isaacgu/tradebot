from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tradebot.core.config import config_hash, load_runtime_config
from tradebot.core.errors import ConfigurationError

VALID_CONFIG = """\
environment: backtest
run_id: test-run
instrument: GBP_USD
execution_enabled: false
logging:
  level: INFO
  json: true
metrics:
  enabled: true
"""


def test_load_config_is_strict_and_frozen(tmp_path: Path) -> None:
    path = tmp_path / "runtime.yaml"
    path.write_text(VALID_CONFIG, encoding="utf-8")
    config = load_runtime_config(path)

    assert config.environment == "backtest"
    with pytest.raises(ValidationError, match="frozen"):
        config.run_id = "changed"


def test_unknown_top_level_and_nested_keys_are_rejected(tmp_path: Path) -> None:
    top = tmp_path / "top.yaml"
    top.write_text(f"{VALID_CONFIG}unknown: true\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="unknown"):
        load_runtime_config(top)

    nested = tmp_path / "nested.yaml"
    nested.write_text(
        VALID_CONFIG.replace("  json: true", "  json: true\n  secret: bad"), encoding="utf-8"
    )
    with pytest.raises(ConfigurationError, match=r"logging\.secret"):
        load_runtime_config(nested)


def test_json_logging_cannot_be_disabled(tmp_path: Path) -> None:
    path = tmp_path / "plain-text.yaml"
    path.write_text(VALID_CONFIG.replace("json: true", "json: false"), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"logging\.json"):
        load_runtime_config(path)


@pytest.mark.parametrize(
    ("line", "replacement", "field"),
    [
        ("run_id: test-run", "run_id: '   '", "run_id"),
        ("instrument: GBP_USD", "instrument: ''", "instrument"),
    ],
)
def test_identity_fields_must_be_nonblank(
    tmp_path: Path, line: str, replacement: str, field: str
) -> None:
    path = tmp_path / f"blank-{field}.yaml"
    path.write_text(VALID_CONFIG.replace(line, replacement), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=field):
        load_runtime_config(path)


def test_config_hash_is_independent_of_yaml_key_order(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(VALID_CONFIG, encoding="utf-8")
    second.write_text(
        """\
metrics: {enabled: true}
logging: {json: true, level: INFO}
execution_enabled: false
instrument: GBP_USD
run_id: test-run
environment: backtest
""",
        encoding="utf-8",
    )

    assert config_hash(load_runtime_config(first)) == config_hash(load_runtime_config(second))
    assert len(config_hash(load_runtime_config(first))) == 64

    changed = tmp_path / "changed.yaml"
    changed.write_text(VALID_CONFIG.replace("test-run", "different-run"), encoding="utf-8")
    assert config_hash(load_runtime_config(first)) != config_hash(load_runtime_config(changed))


def test_duplicate_keys_and_unsafe_yaml_tags_are_rejected(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(f"{VALID_CONFIG}run_id: duplicate\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="duplicate key"):
        load_runtime_config(duplicate)

    unsafe = tmp_path / "unsafe.yaml"
    unsafe.write_text("!!python/object/apply:os.system ['echo unsafe']\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="cannot parse"):
        load_runtime_config(unsafe)


def test_missing_file_non_mapping_and_invalid_fields_fail_cleanly(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="cannot parse"):
        load_runtime_config(tmp_path / "missing.yaml")

    sequence = tmp_path / "sequence.yaml"
    sequence.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="YAML mapping"):
        load_runtime_config(sequence)

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(VALID_CONFIG.replace("backtest", "invalid"), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="environment"):
        load_runtime_config(invalid)
