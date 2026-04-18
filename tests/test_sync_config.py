from __future__ import annotations

import json

from smartsheet_sync.sync_config import load_sync_config, resolve_technical_sync_columns


def test_load_sync_config_reads_default_and_table_rules(tmp_path) -> None:
    config_path = tmp_path / "sync.json"
    config_path.write_text(
        json.dumps(
            {
                "technical_columns": {
                    "default": {
                        "include": ["__parent_id"],
                        "exclude": ["deleted_at"],
                    },
                    "tables": {
                        "CTM": {
                            "include": ["last_synced_at"],
                            "exclude": ["__modified_at"],
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_sync_config(str(config_path))

    assert config.source_path is not None
    assert "__parent_id" in config.default_rule.include
    assert "deleted_at" in config.default_rule.exclude
    assert "ctm" in config.table_rules
    assert "last_synced_at" in config.table_rules["ctm"].include
    assert "__modified_at" in config.table_rules["ctm"].exclude


def test_resolve_technical_sync_columns_cli_overrides_config(tmp_path) -> None:
    config_path = tmp_path / "sync.json"
    config_path.write_text(
        json.dumps(
            {
                "technical_columns": {
                    "tables": {
                        "CTM": {
                            "columns": ["__created_at", "last_synced_at"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    resolved_columns = resolve_technical_sync_columns(
        table_name="CTM",
        sync_config=str(config_path),
        cli_technical_columns=("__modified_at",),
        cli_include_technical_columns=("__row_id",),
        cli_exclude_technical_columns=("__modified_at",),
    )

    assert resolved_columns == frozenset({"__row_id"})
