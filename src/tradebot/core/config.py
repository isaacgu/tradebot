"""Strict YAML configuration loading and canonical provenance hashing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Hashable
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from tradebot.core.errors import ConfigurationError

_NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LoggingConfig(_StrictModel):
    """Process logging settings; no secret-bearing fields are permitted."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_output: Literal[True] = Field(
        default=True, validation_alias="json", serialization_alias="json"
    )


class MetricsConfig(_StrictModel):
    """Local metrics client toggle; no network listener is started in P0."""

    enabled: bool = True


class RuntimeConfig(_StrictModel):
    """Resolved non-secret runtime configuration for the P0 wiring demo."""

    environment: Literal["backtest", "paper", "live"]
    run_id: _NonBlankText
    instrument: _NonBlankText
    execution_enabled: Literal[False] = False
    logging: LoggingConfig = LoggingConfig()
    metrics: MetricsConfig = MetricsConfig()


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader variant that refuses duplicate mapping keys."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[object, object]:
        self.flatten_mapping(node)
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, Hashable):
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                )
            if key in mapping:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _validation_details(error: ValidationError) -> str:
    issues: list[str] = []
    for issue in error.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in issue["loc"])
        issues.append(f"{location}: {issue['msg']}")
    return "; ".join(issues)


def load_runtime_config(path: Path) -> RuntimeConfig:
    """Load and validate a P0 runtime YAML file with recursive unknown-key rejection."""
    try:
        loader = _UniqueKeyLoader(path.read_text(encoding="utf-8"))
        try:
            raw = loader.get_single_data()
        finally:
            loader.dispose()
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"cannot parse configuration {path}: {error}") from None
    if not isinstance(raw, dict):
        raise ConfigurationError(f"configuration {path} must contain a YAML mapping")
    try:
        return RuntimeConfig.model_validate(raw)
    except ValidationError as error:
        raise ConfigurationError(
            f"invalid or unknown configuration fields in {path}: {_validation_details(error)}"
        ) from None


def canonical_config_json(config: RuntimeConfig) -> str:
    """Return canonical UTF-8 JSON input used for config provenance hashes."""
    return json.dumps(
        config.model_dump(mode="json", exclude_none=False, by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def config_hash(config: RuntimeConfig) -> str:
    """Return lowercase SHA-256 of the fully resolved non-secret configuration."""
    return hashlib.sha256(canonical_config_json(config).encode("utf-8")).hexdigest()
