from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_float_dtype, is_integer_dtype
from sqlalchemy import BIGINT, BOOLEAN, DATETIME, FLOAT, TEXT, Column as SAColumn, MetaData, Table, create_engine, inspect
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Engine, URL
from sqlalchemy.sql.sqltypes import BigInteger, Boolean, DateTime, Float, Text

from .common import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MYSQL_PORT,
    DELETED_AT_COLUMN,
    IS_DELETED_COLUMN,
    LAST_SYNCED_AT_COLUMN,
    normalized_mysql_name,
    sanitize_mysql_identifier,
)
from .models import SyncResult, SyncVerification
from .transform import managed_dataframe

LOGGER = logging.getLogger(__name__)


def build_mysql_engine(
    *,
    mysql_url: str | None = None,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> Engine:
    if mysql_url:
        return create_engine(mysql_url)

    resolved_host = host or os.getenv("MYSQL_HOST")
    resolved_port = port or int(os.getenv("MYSQL_PORT", str(DEFAULT_MYSQL_PORT)))
    resolved_database = database or os.getenv("MYSQL_DATABASE")
    resolved_user = user or os.getenv("MYSQL_USER")
    resolved_password = password or os.getenv("MYSQL_PASSWORD")

    missing = [
        name
        for name, value in (
            ("MYSQL_HOST", resolved_host),
            ("MYSQL_DATABASE", resolved_database),
            ("MYSQL_USER", resolved_user),
            ("MYSQL_PASSWORD", resolved_password),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing MySQL configuration. Set MYSQL_URL or provide: " + ", ".join(missing)
        )

    url = URL.create(
        "mysql+pymysql",
        username=resolved_user,
        password=resolved_password,
        host=resolved_host,
        port=resolved_port,
        database=resolved_database,
    )
    return create_engine(url)


def write_dataframe_to_mysql(
    df: pd.DataFrame,
    *,
    table_name: str,
    engine: Engine,
    chunksize: int = DEFAULT_CHUNK_SIZE,
    mark_missing_as_deleted: bool = False,
) -> SyncResult:
    resolved_table_name = sanitize_mysql_identifier(table_name, preserve_case=True)
    prepared_df = managed_dataframe(df)
    synced_at = (
        prepared_df[LAST_SYNCED_AT_COLUMN].iloc[0]
        if not prepared_df.empty
        else datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    )

    LOGGER.info("Syncing %s rows into MySQL table %s", len(prepared_df), resolved_table_name)

    with engine.begin() as connection:
        table = ensure_sync_table(connection.engine, resolved_table_name, prepared_df)
        table_column_names = list(table.c.keys())
        table_columns_by_normalized = {
            normalized_mysql_name(column_name): column_name for column_name in table_column_names
        }
        aligned_df = prepared_df.rename(
            columns={
                column_name: table_columns_by_normalized.get(normalized_mysql_name(column_name), column_name)
                for column_name in prepared_df.columns
            }
        )
        managed_columns = [column_name for column_name in aligned_df.columns if column_name in table.c]
        records = aligned_df[managed_columns].to_dict(orient="records")

        for start in range(0, len(records), chunksize):
            batch = records[start : start + chunksize]
            if not batch:
                continue

            insert_stmt = mysql_insert(table).values(batch)
            update_map = {
                column_name: insert_stmt.inserted[column_name]
                for column_name in managed_columns
                if column_name != "__row_id"
            }
            connection.execute(insert_stmt.on_duplicate_key_update(**update_map))

        rows_marked_deleted = 0
        if mark_missing_as_deleted:
            rows_marked_deleted = mark_missing_rows_as_deleted(
                connection.engine,
                resolved_table_name,
                row_ids=list(aligned_df["__row_id"]) if "__row_id" in aligned_df else [],
                chunksize=chunksize,
            )
            LOGGER.info("Marked %s rows as deleted in %s", rows_marked_deleted, resolved_table_name)
        else:
            LOGGER.debug("Missing-row deletion marking disabled for table %s", resolved_table_name)

    return SyncResult(
        table_name=resolved_table_name,
        synced_at=synced_at,
        rows_in_payload=len(prepared_df),
        rows_marked_deleted=rows_marked_deleted,
    )


def verify_sync(engine: Engine, sync_result: SyncResult) -> SyncVerification:
    inspector = inspect(engine)
    if not inspector.has_table(sync_result.table_name):
        raise RuntimeError(f"Sync verification failed: table '{sync_result.table_name}' does not exist.")

    quoted_table = engine.dialect.identifier_preparer.quote_identifier(sync_result.table_name)
    sync_sql = sync_result.synced_at.strftime("%Y-%m-%d %H:%M:%S")
    with engine.begin() as connection:
        row = connection.exec_driver_sql(
            f"""
            SELECT
                COUNT(*) AS total_rows,
                COUNT(DISTINCT __row_id) AS distinct_row_ids,
                SUM(CASE WHEN {LAST_SYNCED_AT_COLUMN} = %s THEN 1 ELSE 0 END) AS synced_rows,
                SUM(CASE WHEN {LAST_SYNCED_AT_COLUMN} IS NULL OR {LAST_SYNCED_AT_COLUMN} <> %s THEN 1 ELSE 0 END) AS stale_rows
            FROM {quoted_table}
            """,
            (sync_sql, sync_sql),
        ).mappings().one()

    verification = SyncVerification(
        total_rows=int(row["total_rows"] or 0),
        distinct_row_ids=int(row["distinct_row_ids"] or 0),
        synced_rows=int(row["synced_rows"] or 0),
        stale_rows=int(row["stale_rows"] or 0),
    )

    if verification.synced_rows != sync_result.rows_in_payload:
        raise RuntimeError(
            "Sync verification failed: synced row count in MySQL does not match the rows sent by the script."
        )
    if verification.distinct_row_ids < sync_result.rows_in_payload:
        raise RuntimeError(
            "Sync verification failed: distinct __row_id count is lower than the synchronized payload size."
        )

    return verification


def column_type_for_series(column_name: str, series: pd.Series) -> BigInteger | Boolean | DateTime | Float | Text:
    if column_name == "__row_id":
        return BIGINT()
    if column_name in {"__row_number", "__parent_id", "__sibling_id"}:
        return BIGINT()
    if column_name in {"__created_at", "__modified_at", LAST_SYNCED_AT_COLUMN, DELETED_AT_COLUMN}:
        return DATETIME()
    if column_name == IS_DELETED_COLUMN:
        return BOOLEAN()
    if is_datetime64_any_dtype(series):
        return DATETIME()
    if is_bool_dtype(series):
        return BOOLEAN()
    if is_integer_dtype(series):
        return BIGINT()
    if is_float_dtype(series):
        return FLOAT()
    return TEXT()


def define_table(metadata: MetaData, table_name: str, df: pd.DataFrame) -> Table:
    columns: list[SAColumn[Any]] = []
    for column_name in df.columns:
        column_type = column_type_for_series(column_name, df[column_name])
        nullable = column_name != "__row_id"
        columns.append(SAColumn(column_name, column_type, nullable=nullable))

    return Table(table_name, metadata, *columns)


def is_generated_on_insert(column: dict[str, Any]) -> bool:
    autoincrement = column.get("autoincrement")
    if autoincrement is True or str(autoincrement).lower() == "auto":
        return True
    default = column.get("default")
    return default not in (None, "")


def validate_existing_table_for_sync(inspector: Any, table_name: str, df: pd.DataFrame) -> None:
    managed_columns = {normalized_mysql_name(column_name) for column_name in df.columns}
    columns = inspector.get_columns(table_name)
    columns_by_normalized = {
        normalized_mysql_name(column["name"]): column for column in columns
    }
    pk_constraint = inspector.get_pk_constraint(table_name)
    pk_columns = pk_constraint.get("constrained_columns") or []

    blocking_pk_columns: list[str] = []
    for column_name in pk_columns:
        normalized_column_name = normalized_mysql_name(column_name)
        if normalized_column_name in managed_columns:
            continue

        column_metadata = columns_by_normalized.get(normalized_column_name, {"name": column_name})
        if is_generated_on_insert(column_metadata):
            continue
        blocking_pk_columns.append(str(column_metadata["name"]))

    if blocking_pk_columns:
        blocking_list = ", ".join(blocking_pk_columns)
        raise RuntimeError(
            f"Unsafe sync target '{table_name}': primary key column(s) {blocking_list} "
            "are required by the existing table but are not populated by the Smartsheet payload. "
            "Use a dedicated sync table or add an explicit mapping for those columns."
        )


def ensure_sync_table(engine: Engine, table_name: str, df: pd.DataFrame) -> Table:
    inspector = inspect(engine)
    metadata = MetaData()

    if not inspector.has_table(table_name):
        LOGGER.info("Creating MySQL table %s", table_name)
        table = define_table(metadata, table_name, df)
        metadata.create_all(engine, tables=[table])
        ensure_unique_row_id_index(engine, table_name, inspector=None)
        metadata.clear()
        return Table(table_name, metadata, autoload_with=engine)

    validate_existing_table_for_sync(inspector, table_name, df)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    existing_columns_by_normalized = {
        normalized_mysql_name(column_name): column_name for column_name in existing_columns
    }
    quoted_table = engine.dialect.identifier_preparer.quote_identifier(table_name)

    for column_name in df.columns:
        if normalized_mysql_name(column_name) in existing_columns_by_normalized:
            continue
        column_type = column_type_for_series(column_name, df[column_name]).compile(dialect=engine.dialect)
        nullable_sql = "NULL"
        LOGGER.info("Adding missing column %s to table %s", column_name, table_name)
        with engine.begin() as connection:
            quoted_column = engine.dialect.identifier_preparer.quote_identifier(column_name)
            connection.exec_driver_sql(
                f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_column} {column_type} {nullable_sql}"
            )

    ensure_unique_row_id_index(engine, table_name, inspector=inspect(engine))
    metadata.clear()
    return Table(table_name, metadata, autoload_with=engine)


def ensure_unique_row_id_index(engine: Engine, table_name: str, inspector: Any | None) -> None:
    local_inspector = inspector or inspect(engine)
    existing_columns = {column["name"] for column in local_inspector.get_columns(table_name)}
    existing_columns_by_normalized = {
        normalized_mysql_name(column_name): column_name for column_name in existing_columns
    }
    if normalized_mysql_name("__row_id") not in existing_columns_by_normalized:
        return

    indexes = local_inspector.get_indexes(table_name)
    for index in indexes:
        if index.get("unique") and index.get("column_names") == ["__row_id"]:
            return

    quoted_table = engine.dialect.identifier_preparer.quote_identifier(table_name)
    index_name = sanitize_mysql_identifier(f"{table_name}__row_id__uniq", preserve_case=True)
    quoted_index = engine.dialect.identifier_preparer.quote_identifier(index_name)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE UNIQUE INDEX {quoted_index} ON {quoted_table} (`__row_id`)"
        )


def mark_missing_rows_as_deleted(
    engine: Engine,
    table_name: str,
    *,
    row_ids: list[Any],
    chunksize: int = DEFAULT_CHUNK_SIZE,
) -> int:
    inspector = inspect(engine)
    columns = {normalized_mysql_name(column["name"]): column["name"] for column in inspector.get_columns(table_name)}
    if normalized_mysql_name("__row_id") not in columns:
        return 0

    quoted_table = engine.dialect.identifier_preparer.quote_identifier(table_name)
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    rows_marked_deleted = 0

    with engine.begin() as connection:
        if row_ids:
            payload_row_ids = set(row_ids)
            existing_rows = connection.exec_driver_sql(
                f"SELECT __row_id FROM {quoted_table}"
            ).scalars()
            missing_row_ids = [
                existing_row_id for existing_row_id in existing_rows if existing_row_id not in payload_row_ids
            ]
            for start in range(0, len(missing_row_ids), chunksize):
                batch = missing_row_ids[start : start + chunksize]
                if not batch:
                    continue
                placeholders = ", ".join(["%s"] * len(batch))
                params = [now, *batch]
                result = connection.exec_driver_sql(
                    f"""
                    UPDATE {quoted_table}
                    SET {IS_DELETED_COLUMN} = TRUE, {DELETED_AT_COLUMN} = %s
                    WHERE __row_id IN ({placeholders})
                      AND ({IS_DELETED_COLUMN} IS NULL OR {IS_DELETED_COLUMN} = FALSE)
                    """,
                    tuple(params),
                )
                rows_marked_deleted += int(result.rowcount or 0)
        else:
            result = connection.exec_driver_sql(
                f"""
                UPDATE {quoted_table}
                SET {IS_DELETED_COLUMN} = TRUE, {DELETED_AT_COLUMN} = %s
                WHERE ({IS_DELETED_COLUMN} IS NULL OR {IS_DELETED_COLUMN} = FALSE)
                """,
                (now,),
            )
            rows_marked_deleted += int(result.rowcount or 0)

    return rows_marked_deleted
