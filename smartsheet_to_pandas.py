#!/usr/bin/env python3
"""Fetch a Smartsheet sheet and load it into pandas."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime, timezone
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any, TypeVar, TypedDict

import pandas as pd
import smartsheet
from dotenv import load_dotenv
from smartsheet.models import Cell, Column, Error, Sheet, SummaryField
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine


DEFAULT_API_BASE = "https://api.smartsheet.com/2.0"
DEFAULT_PAGE_SIZE = 500
DEFAULT_MYSQL_PORT = 3306
DEFAULT_CHUNK_SIZE = 1000


load_dotenv()


@dataclass
class SheetExtract:
    metadata: dict[str, Any]
    rows_df: pd.DataFrame
    cells_df: pd.DataFrame


class ColumnMetadata(TypedDict):
    column_id: int
    title: str
    original_title: str | None
    type: str | None
    index: int | None
    primary: bool


JsonPrimitive = str | int | float | bool | None
JsonValue = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
ModelType = TypeVar("ModelType")


def build_client(access_token: str | None = None, api_base: str = DEFAULT_API_BASE) -> smartsheet.Smartsheet:
    token = access_token or os.getenv("SMARTSHEET_ACCESS_TOKEN")
    if not token:
        raise ValueError(
            "Missing Smartsheet token. Set SMARTSHEET_ACCESS_TOKEN or pass access_token explicitly."
        )

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


def _safe_get(obj: Any, attribute: str, default: Any = None) -> Any:
    return getattr(obj, attribute, default)


def _as_list(value: Iterable[ModelType] | None) -> list[ModelType]:
    if value is None:
        return []
    return list(value)


def _model_id(obj: Any) -> int:
    value = _safe_get(obj, "id", _safe_get(obj, "id_"))
    if not isinstance(value, int):
        raise ValueError(f"Expected integer id for {type(obj).__name__}, got {value!r}")
    return value


def _model_type(obj: Any) -> str | None:
    value = _safe_get(obj, "type", _safe_get(obj, "type_"))
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _require_sheet(response: Sheet | Error) -> Sheet:
    if isinstance(response, Error):
        raise RuntimeError(response.message or "Smartsheet API returned an error response.")

    return response


def _extract_object_value(cell: Cell) -> JsonValue:
    object_value = _safe_get(cell, "object_value")
    if object_value is None:
        return None

    if hasattr(object_value, "to_dict"):
        serialized = object_value.to_dict()
        if isinstance(serialized, dict):
            return serialized
        if isinstance(serialized, list):
            return serialized
        return str(serialized)

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


def _metadata_table_name(base_table_name: str) -> str:
    suffix = "__meta"
    truncated = _sanitize_mysql_identifier(base_table_name, max_length=64 - len(suffix))
    return f"{truncated}{suffix}"


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


def _metadata_dataframe(extract: SheetExtract) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sheet_id": extract.metadata["sheet_id"],
                "sheet_name": extract.metadata["sheet_name"],
                "version": extract.metadata["version"],
                "access_level": extract.metadata["access_level"],
                "total_row_count": extract.metadata["total_row_count"],
                "column_count": extract.metadata["column_count"],
                "permalink": extract.metadata["permalink"],
                "synced_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "metadata_json": json.dumps(extract.metadata, ensure_ascii=True, default=str),
            }
        ]
    )


def _column_names(columns: list[Column]) -> dict[int, str]:
    counts: dict[str, int] = {}
    names: dict[int, str] = {}

    for column in columns:
        column_id = _model_id(column)
        title = str(_safe_get(column, "title", f"column_{column_id}")).strip() or f"column_{column_id}"
        counts[title] = counts.get(title, 0) + 1
        if counts[title] == 1:
            names[column_id] = title
        else:
            names[column_id] = f"{title}__{column_id}"

    return names


def _summary_to_metadata(fields: list[SummaryField]) -> list[dict[str, JsonValue]]:
    return [
        {
            "field_id": _model_id(field),
            "title": field.title,
            "type": _model_type(field),
            "display_value": field.display_value,
            "object_value": _safe_get(field, "object_value"),
        }
        for field in fields
    ]


def fetch_sheet(
    sheet_id: int,
    *,
    access_token: str | None = None,
    api_base: str = DEFAULT_API_BASE,
    page_size: int = DEFAULT_PAGE_SIZE,
    level: int = 2,
    include: list[str] | None = None,
) -> Sheet:
    client = build_client(access_token=access_token, api_base=api_base)
    include = include or ["columnType", "objectValue", "writerInfo", "summary"]

    first_page = _require_sheet(
        client.Sheets.get_sheet(
            sheet_id,
            include=include,
            page_size=page_size,
            page=1,
            level=level,
        )
    )

    all_rows = _as_list(first_page.rows)
    total_rows = first_page.total_row_count or len(all_rows)

    # Some sheets return total_row_count correctly but an empty first page when paginated.
    # Retry once without pagination so extraction still works.
    if total_rows > 0 and not all_rows:
        unpaginated_sheet = _require_sheet(
            client.Sheets.get_sheet(
                sheet_id,
                include=include,
                level=level,
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
                level=level,
            )
        )
        page_rows = _as_list(next_page.rows)
        if not page_rows:
            break
        all_rows.extend(page_rows)

    first_page.rows = all_rows
    return first_page


def sheet_to_dataframes(sheet: Sheet) -> SheetExtract:
    columns: list[Column] = list(_as_list(sheet.columns))
    column_names = _column_names(columns)
    column_meta: dict[int, ColumnMetadata] = {
        _model_id(column): {
            "column_id": _model_id(column),
            "title": column_names[_model_id(column)],
            "original_title": column.title,
            "type": _model_type(column),
            "index": _safe_get(column, "index"),
            "primary": bool(_safe_get(column, "primary", False)),
        }
        for column in columns
    }

    row_records: list[dict[str, Any]] = []
    cell_records: list[dict[str, Any]] = []

    for row in _as_list(sheet.rows):
        row_record: dict[str, Any] = {
            "__row_id": _model_id(row),
            "__row_number": row.row_number,
            "__parent_id": row.parent_id,
            "__sibling_id": row.sibling_id,
            "__expanded": row.expanded,
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

            row_record[column_name] = value
            if display_value is not None and display_value != value:
                row_record[f"{column_name}__display"] = display_value
            if object_value is not None:
                row_record[f"{column_name}__object"] = json.dumps(object_value, ensure_ascii=True)

            cell_records.append(
                {
                    "sheet_id": _model_id(sheet),
                    "sheet_name": sheet.name,
                    "row_id": _model_id(row),
                    "row_number": row.row_number,
                    "column_id": column_id,
                    "column_title": column_name,
                    "column_type": column_meta.get(column_id, {}).get("type"),
                    "value": value,
                    "display_value": display_value,
                    "object_value": object_value,
                    "formula": cell.formula,
                    "hyperlink": cell.hyperlink,
                }
            )

        row_records.append(row_record)

    metadata = {
        "sheet_id": _model_id(sheet),
        "sheet_name": sheet.name,
        "version": sheet.version,
        "from_id": sheet.from_id,
        "owner": sheet.owner,
        "access_level": sheet.access_level,
        "permalink": sheet.permalink,
        "total_row_count": sheet.total_row_count or len(row_records),
        "column_count": len(columns),
        "columns": list(column_meta.values()),
    }

    if sheet.summary and sheet.summary.fields:
        metadata["summary"] = _summary_to_metadata(list(_as_list(sheet.summary.fields)))

    rows_df = pd.DataFrame(row_records)
    cells_df = pd.DataFrame(cell_records)

    return SheetExtract(metadata=metadata, rows_df=rows_df, cells_df=cells_df)


def write_extract_to_mysql(
    extract: SheetExtract,
    *,
    table_name: str,
    engine: Engine,
    if_exists: str = "replace",
    chunksize: int = DEFAULT_CHUNK_SIZE,
) -> tuple[str, str]:
    resolved_table_name = _sanitize_mysql_identifier(table_name)
    resolved_metadata_table = _metadata_table_name(resolved_table_name)

    rows_df = _prepare_dataframe_for_mysql(extract.rows_df)
    metadata_df = _prepare_dataframe_for_mysql(_metadata_dataframe(extract))

    rows_df.to_sql(
        resolved_table_name,
        engine,
        if_exists=if_exists,
        index=False,
        chunksize=chunksize,
        method="multi",
    )
    metadata_df.to_sql(
        resolved_metadata_table,
        engine,
        if_exists="replace",
        index=False,
        chunksize=1,
        method="multi",
    )

    return resolved_table_name, resolved_metadata_table


def print_extract_summary(extract: SheetExtract, show_columns: bool = False) -> None:
    print(f"Sheet: {extract.metadata['sheet_name']} ({extract.metadata['sheet_id']})")
    print(f"Rows loaded: {len(extract.rows_df)}")
    print(f"API total_row_count: {extract.metadata['total_row_count']}")
    print(f"Columns loaded: {extract.metadata['column_count']}")

    column_titles = [str(column["title"]) for column in extract.metadata["columns"]]
    preview = ", ".join(column_titles[:5])
    if preview:
        suffix = " ..." if len(column_titles) > 5 else ""
        print(f"Column preview: {preview}{suffix}")

    if show_columns:
        print("Columns:")
        for index, column in enumerate(extract.metadata["columns"], start=1):
            print(f"  {index:02d}. {column['title']} [{column['type'] or 'UNKNOWN'}]")

    if len(extract.rows_df) == 0:
        print("Warning: the API returned zero rows for this sheet.")
        print("Check whether the sheet is actually empty or whether the token has access to the visible data.")


def print_mysql_summary(table_name: str, metadata_table_name: str, if_exists: str) -> None:
    print(f"MySQL rows table: {table_name}")
    print(f"MySQL metadata table: {metadata_table_name}")
    print(f"MySQL write mode: {if_exists}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a Smartsheet sheet and write it to MySQL.")
    parser.add_argument("sheet_id", type=int, help="Smartsheet sheet ID")
    parser.add_argument(
        "--api-base",
        default=os.getenv("SMARTSHEET_API_BASE", DEFAULT_API_BASE),
        help="Smartsheet API base URL. Use the regional endpoint if your account is in EU/AU.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="Rows requested per page when paginating the sheet.",
    )
    parser.add_argument(
        "--level",
        type=int,
        default=2,
        choices=[0, 1, 2],
        help="Compatibility level. Use 2 to preserve complex multi-picklist values.",
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
        help="MySQL database name when MYSQL_URL is not provided.",
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
    parser.add_argument(
        "--show-columns",
        action="store_true",
        help="Print the full column list after loading the sheet.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sheet = fetch_sheet(
        args.sheet_id,
        api_base=args.api_base,
        page_size=args.page_size,
        level=args.level,
    )
    extract = sheet_to_dataframes(sheet)
    print_extract_summary(extract, show_columns=args.show_columns)
    engine = build_mysql_engine(
        mysql_url=args.mysql_url,
        host=args.mysql_host,
        port=args.mysql_port,
        database=args.mysql_database,
        user=args.mysql_user,
        password=args.mysql_password,
    )

    target_table = args.mysql_table or _default_table_name(str(extract.metadata["sheet_name"]))
    try:
        rows_table, metadata_table = write_extract_to_mysql(
            extract,
            table_name=target_table,
            engine=engine,
            if_exists=args.if_exists,
            chunksize=args.chunksize,
        )
    finally:
        engine.dispose()

    print_mysql_summary(rows_table, metadata_table, args.if_exists)


if __name__ == "__main__":
    main()
