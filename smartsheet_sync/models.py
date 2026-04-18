from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Args:
    sheet_id: int
    api_base: str
    page_size: int
    mysql_url: str | None
    mysql_host: str | None
    mysql_port: int
    mysql_database: str | None
    mysql_user: str | None
    mysql_password: str | None
    mysql_table: str | None
    chunksize: int
    log_level: str
    dry_run: bool
    mark_missing_as_deleted: bool
    sync_config: str | None
    technical_columns: tuple[str, ...] | None
    include_technical_columns: tuple[str, ...]
    exclude_technical_columns: tuple[str, ...]


@dataclass
class SyncResult:
    table_name: str
    synced_at: datetime
    rows_in_payload: int
    rows_marked_deleted: int = 0
    verification_distinct_column: str | None = None
    verification_distinct_values: tuple[object, ...] = ()


@dataclass
class SyncVerification:
    total_rows: int
    distinct_row_ids: int
    synced_rows: int
    stale_rows: int


class ParsedNamespace(argparse.Namespace):
    sheet_id: str | None
    api_base: str
    page_size: int
    mysql_url: str | None
    mysql_host: str | None
    mysql_port: int
    mysql_database: str | None
    mysql_user: str | None
    mysql_password: str | None
    mysql_table: str | None
    chunksize: int
    log_level: str
    dry_run: bool
    mark_missing_as_deleted: bool
    sync_config: str | None
    technical_columns: str | None
    include_technical_columns: str | None
    exclude_technical_columns: str | None
