#!/usr/bin/env python3
"""Copy a Smartsheet sheet into a MySQL table."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal, TypeVar, cast

import pandas as pd
import smartsheet
from dotenv import load_dotenv
from smartsheet.models import Cell, Column, Error, Sheet
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL


DEFAULT_API_BASE = "https://api.smartsheet.com/2.0"
DEFAULT_PAGE_SIZE = 500
DEFAULT_COMPAT_LEVEL = 2
DEFAULT_MYSQL_PORT = 3306
DEFAULT_CHUNK_SIZE = 1000

ModelType = TypeVar("ModelType")
IfExistsMode = Literal["fail", "replace", "append"]


load_dotenv()


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
    if_exists: IfExistsMode
    chunksize: int


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
    if_exists: IfExistsMode
    chunksize: int


def build_smartsheet_client(
    *,
    access_token: str | None = None,
    api_base: str = DEFAULT_API_BASE,
) -> smartsheet.Smartsheet:
    token = access_token or os.getenv("SMARTSHEET_ACCESS_TOKEN")
    if not token:
        raise ValueError("Missing Smartsheet token. Set SMARTSHEET_ACCESS_TOKEN.")

    client = smartsheet.Smartsheet(access_token=token, api_base=api_base)
    client.errors_as_exceptions(True)
    return client


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


def _as_list(value: Iterable[ModelType] | None) -> list[ModelType]:
    if value is None:
        return []
    return list(value)


def _model_id(obj: Any) -> int:
    value = getattr(obj, "id", getattr(obj, "id_", None))
    if not isinstance(value, int):
        raise ValueError(f"Expected integer id for {type(obj).__name__}, got {value!r}")
    return value


def _require_sheet(response: Sheet | Error) -> Sheet:
    if isinstance(response, Error):
        raise RuntimeError(response.message or "Smartsheet API returned an error response.")
    return response


def _extract_object_value(cell: Cell) -> Any:
    object_value = getattr(cell, "object_value", None)
    if object_value is None:
        return None
    if hasattr(object_value, "to_dict"):
        return object_value.to_dict()
    return object_value


def _sanitize_mysql_identifier(name: str, max_length: int = 64) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z_]+", "_", name).strip("_").lower()
    sanitized = re.sub(r"_+", "_", sanitized)
    if not sanitized:
        sanitized = "smartsheet_sheet"
    if sanitized[0].isdigit():
        sanitized = f"sheet_{sanitized}"
    return sanitized[:max_length].rstrip("_") or "smartsheet_sheet"


def _default_table_name(sheet_name: str) -> str:
    return _sanitize_mysql_identifier(sheet_name)


def _normalize_mysql_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, default=str)
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is not None:
            return value.tz_convert("UTC").tz_localize(None).to_pydatetime()
        return value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def _prepare_dataframe_for_mysql(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(lambda column: column.map(_normalize_mysql_value))


def _column_names(columns: list[Column]) -> dict[int, str]:
    counts: dict[str, int] = {}
    names: dict[int, str] = {}

    for column in columns:
        column_id = _model_id(column)
        title = str(column.title or f"column_{column_id}").strip() or f"column_{column_id}"
        counts[title] = counts.get(title, 0) + 1
        names[column_id] = title if counts[title] == 1 else f"{title}__{column_id}"

    return names


def fetch_sheet(
    sheet_id: int,
    *,
    api_base: str = DEFAULT_API_BASE,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Sheet:
    client = build_smartsheet_client(api_base=api_base)
    include = ["columnType", "objectValue", "writerInfo"]

    first_page = _require_sheet(
        client.Sheets.get_sheet(
            sheet_id,
            include=include,
            page_size=page_size,
            page=1,
            level=DEFAULT_COMPAT_LEVEL,
        )
    )

    all_rows = _as_list(first_page.rows)
    total_rows = first_page.total_row_count or len(all_rows)

    if total_rows > 0 and not all_rows:
        unpaginated_sheet = _require_sheet(
            client.Sheets.get_sheet(
                sheet_id,
                include=include,
                level=DEFAULT_COMPAT_LEVEL,
            )
        )
        unpaginated_rows = _as_list(unpaginated_sheet.rows)
        if unpaginated_rows:
            return unpaginated_sheet

    current_page = 1
    while len(all_rows) < total_rows:
        current_page += 1
        next_page = _require_sheet(
            client.Sheets.get_sheet(
                sheet_id,
                include=include,
                page_size=page_size,
                page=current_page,
                level=DEFAULT_COMPAT_LEVEL,
            )
        )
        page_rows = _as_list(next_page.rows)
        if not page_rows:
            break
        all_rows.extend(page_rows)

    first_page.rows = all_rows
    return first_page


def sheet_to_dataframe(sheet: Sheet) -> pd.DataFrame:
    columns = _as_list(sheet.columns)
    column_names = _column_names(columns)
    records: list[dict[str, Any]] = []

    for row in _as_list(sheet.rows):
        record: dict[str, Any] = {
            "__row_id": _model_id(row),
            "__row_number": row.row_number,
            "__parent_id": row.parent_id,
            "__sibling_id": row.sibling_id,
            "__created_at": row.created_at,
            "__modified_at": row.modified_at,
        }

        for cell in _as_list(row.cells):
            column_id = cell.column_id
            if column_id is None:
                continue

            column_name = column_names.get(column_id, f"column_{column_id}")
            value = cell.value
            display_value = cell.display_value
            object_value = _extract_object_value(cell)

            record[column_name] = value
            if display_value is not None and display_value != value:
                record[f"{column_name}__display"] = display_value
            if object_value is not None:
                record[f"{column_name}__object"] = object_value

        records.append(record)

    return pd.DataFrame(records)


def write_dataframe_to_mysql(
    df: pd.DataFrame,
    *,
    table_name: str,
    engine: Engine,
    if_exists: IfExistsMode = "replace",
    chunksize: int = DEFAULT_CHUNK_SIZE,
) -> str:
    resolved_table_name = _sanitize_mysql_identifier(table_name)
    prepared_df = _prepare_dataframe_for_mysql(df)
    prepared_df.to_sql(
        resolved_table_name,
        engine,
        if_exists=if_exists,
        index=False,
        chunksize=chunksize,
        method="multi",
    )
    return resolved_table_name


def _resolve_sheet_id(raw_sheet_id: str | None, parser: argparse.ArgumentParser) -> int:
    sheet_id_value = raw_sheet_id or os.getenv("SMARTSHEET_SHEET_ID")
    if not sheet_id_value:
        parser.error("Missing sheet_id. Pass it as an argument or set SMARTSHEET_SHEET_ID.")
        raise AssertionError("unreachable")

    try:
        return int(sheet_id_value)
    except ValueError:
        parser.error(f"Invalid sheet_id '{sheet_id_value}'. SMARTSHEET_SHEET_ID must be an integer.")
        raise AssertionError("unreachable")


def parse_args() -> Args:
    parser = argparse.ArgumentParser(description="Copy a Smartsheet sheet into MySQL.")
    parser.add_argument("sheet_id", nargs="?", help="Smartsheet sheet ID. Defaults to SMARTSHEET_SHEET_ID.")
    parser.add_argument(
        "--api-base",
        default=os.getenv("SMARTSHEET_API_BASE", DEFAULT_API_BASE),
        help="Smartsheet API base URL.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="Rows requested per Smartsheet API page.",
    )
    parser.add_argument(
        "--mysql-url",
        default=os.getenv("MYSQL_URL"),
        help="Full SQLAlchemy MySQL URL. Overrides MYSQL_HOST/PORT/DATABASE/USER/PASSWORD.",
    )
    parser.add_argument(
        "--mysql-host",
        default=os.getenv("MYSQL_HOST"),
        help="MySQL host when MYSQL_URL is not provided.",
    )
    parser.add_argument(
        "--mysql-port",
        type=int,
        default=int(os.getenv("MYSQL_PORT", str(DEFAULT_MYSQL_PORT))),
        help="MySQL port when MYSQL_URL is not provided.",
    )
    parser.add_argument(
        "--mysql-database",
        default=os.getenv("MYSQL_DATABASE"),
        help="MySQL database when MYSQL_URL is not provided.",
    )
    parser.add_argument(
        "--mysql-user",
        default=os.getenv("MYSQL_USER"),
        help="MySQL user when MYSQL_URL is not provided.",
    )
    parser.add_argument(
        "--mysql-password",
        default=os.getenv("MYSQL_PASSWORD"),
        help="MySQL password when MYSQL_URL is not provided.",
    )
    parser.add_argument(
        "--mysql-table",
        default=os.getenv("MYSQL_TABLE"),
        help="Target MySQL table. Defaults to a sanitized version of the sheet name.",
    )
    parser.add_argument(
        "--if-exists",
        choices=["fail", "replace", "append"],
        default=os.getenv("MYSQL_IF_EXISTS", "replace"),
        help="How to behave if the target table already exists.",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Batch size used when inserting rows into MySQL.",
    )
    namespace = cast(ParsedNamespace, parser.parse_args(namespace=ParsedNamespace()))
    return Args(
        sheet_id=_resolve_sheet_id(namespace.sheet_id, parser),
        api_base=namespace.api_base,
        page_size=namespace.page_size,
        mysql_url=namespace.mysql_url,
        mysql_host=namespace.mysql_host,
        mysql_port=namespace.mysql_port,
        mysql_database=namespace.mysql_database,
        mysql_user=namespace.mysql_user,
        mysql_password=namespace.mysql_password,
        mysql_table=namespace.mysql_table,
        if_exists=namespace.if_exists,
        chunksize=namespace.chunksize,
    )


def main() -> None:
    args = parse_args()
    sheet = fetch_sheet(
        args.sheet_id,
        api_base=args.api_base,
        page_size=args.page_size,
    )
    dataframe = sheet_to_dataframe(sheet)

    engine = build_mysql_engine(
        mysql_url=args.mysql_url,
        host=args.mysql_host,
        port=args.mysql_port,
        database=args.mysql_database,
        user=args.mysql_user,
        password=args.mysql_password,
    )

    target_table = args.mysql_table or _default_table_name(sheet.name or str(args.sheet_id))
    try:
        mysql_table = write_dataframe_to_mysql(
            dataframe,
            table_name=target_table,
            engine=engine,
            if_exists=args.if_exists,
            chunksize=args.chunksize,
        )
    finally:
        engine.dispose()

    print(f"Sheet: {sheet.name} ({_model_id(sheet)})")
    print(f"Rows copied: {len(dataframe)}")
    print(f"MySQL table: {mysql_table}")
    print(f"MySQL write mode: {args.if_exists}")


if __name__ == "__main__":
    main()
