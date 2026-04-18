from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .column_mapping import (
    default_technical_sync_columns_for_table,
    normalize_technical_sync_columns,
)

DEFAULT_SYNC_CONFIG_FILE = "smartsheet_sync.config.json"


@dataclass(frozen=True)
class TechnicalColumnsRule:
    columns: frozenset[str] | None = None
    include: frozenset[str] = frozenset()
    exclude: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SyncConfig:
    source_path: str | None
    default_rule: TechnicalColumnsRule
    table_rules: dict[str, TechnicalColumnsRule]


def resolve_sync_config_path(sync_config: str | None) -> str | None:
    if sync_config:
        return sync_config

    env_path = os.getenv("SMARTSHEET_SYNC_CONFIG")
    if env_path:
        return env_path

    default_path = Path(DEFAULT_SYNC_CONFIG_FILE)
    if default_path.exists() and default_path.is_file():
        return str(default_path)

    return None


def _parse_rule(raw_rule: Any, *, context: str) -> TechnicalColumnsRule:
    if raw_rule is None:
        return TechnicalColumnsRule()
    if not isinstance(raw_rule, dict):
        raise ValueError(f"Invalid {context}: expected an object.")

    allowed_keys = {"columns", "include", "exclude"}
    unexpected_keys = sorted(set(raw_rule.keys()) - allowed_keys)
    if unexpected_keys:
        unexpected = ", ".join(unexpected_keys)
        raise ValueError(f"Invalid {context}: unsupported key(s): {unexpected}.")

    def _list_values(key: str) -> list[str]:
        value = raw_rule.get(key)
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"Invalid {context}.{key}: expected a list of strings.")
        return value

    columns_list = _list_values("columns")
    include = normalize_technical_sync_columns(_list_values("include"))
    exclude = normalize_technical_sync_columns(_list_values("exclude"))
    columns = normalize_technical_sync_columns(columns_list) if columns_list else None

    return TechnicalColumnsRule(columns=columns, include=include, exclude=exclude)


def load_sync_config(sync_config: str | None = None) -> SyncConfig:
    resolved_path = resolve_sync_config_path(sync_config)
    if resolved_path is None:
        return SyncConfig(source_path=None, default_rule=TechnicalColumnsRule(), table_rules={})

    with Path(resolved_path).expanduser().resolve().open("r", encoding="utf-8") as config_file:
        payload = json.load(config_file)

    if not isinstance(payload, dict):
        raise ValueError("Invalid sync config: root must be a JSON object.")

    technical_columns_payload = payload.get("technical_columns", {})
    if not isinstance(technical_columns_payload, dict):
        raise ValueError("Invalid sync config: 'technical_columns' must be an object.")

    default_rule = _parse_rule(technical_columns_payload.get("default"), context="technical_columns.default")

    tables_payload = technical_columns_payload.get("tables", {})
    if not isinstance(tables_payload, dict):
        raise ValueError("Invalid sync config: 'technical_columns.tables' must be an object.")

    table_rules: dict[str, TechnicalColumnsRule] = {}
    for table_name, raw_rule in tables_payload.items():
        if not isinstance(table_name, str) or not table_name.strip():
            raise ValueError("Invalid sync config: table names must be non-empty strings.")
        table_rules[table_name.casefold()] = _parse_rule(
            raw_rule,
            context=f"technical_columns.tables.{table_name}",
        )

    return SyncConfig(source_path=resolved_path, default_rule=default_rule, table_rules=table_rules)


def _apply_rule(base_columns: set[str], rule: TechnicalColumnsRule) -> set[str]:
    resolved_columns = set(base_columns)
    if rule.columns is not None:
        resolved_columns = set(rule.columns)

    resolved_columns.update(rule.include)
    resolved_columns.difference_update(rule.exclude)
    return resolved_columns


def parse_technical_columns_csv(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    parsed_columns = tuple(
        column_name.strip()
        for column_name in raw.split(",")
        if isinstance(column_name, str) and column_name.strip()
    )
    return parsed_columns


def resolve_technical_sync_columns(
    *,
    table_name: str,
    sync_config: str | None = None,
    cli_technical_columns: tuple[str, ...] | None = None,
    cli_include_technical_columns: tuple[str, ...] = (),
    cli_exclude_technical_columns: tuple[str, ...] = (),
) -> frozenset[str]:
    loaded_config = load_sync_config(sync_config)

    resolved_columns = set(default_technical_sync_columns_for_table(table_name))
    resolved_columns = _apply_rule(resolved_columns, loaded_config.default_rule)
    table_rule = loaded_config.table_rules.get(table_name.casefold())
    if table_rule is not None:
        resolved_columns = _apply_rule(resolved_columns, table_rule)

    if cli_technical_columns is not None:
        resolved_columns = set(normalize_technical_sync_columns(cli_technical_columns))

    resolved_columns.update(normalize_technical_sync_columns(cli_include_technical_columns))
    resolved_columns.difference_update(normalize_technical_sync_columns(cli_exclude_technical_columns))

    return normalize_technical_sync_columns(resolved_columns)
