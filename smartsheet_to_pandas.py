#!/usr/bin/env python3
"""Fetch a Smartsheet sheet and load it into pandas."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, TypedDict

import pandas as pd
import smartsheet
from dotenv import load_dotenv
from smartsheet.models import Cell, Column, Error, Sheet, SummaryField


DEFAULT_API_BASE = "https://api.smartsheet.com/2.0"
DEFAULT_PAGE_SIZE = 500


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


def _safe_get(obj: Any, attribute: str, default: Any = None) -> Any:
    return getattr(obj, attribute, default)


def _as_list(value: list[ModelType] | None) -> list[ModelType]:
    return value or []


def _model_id(obj: Any) -> int:
    value = _safe_get(obj, "id", _safe_get(obj, "id_"))
    if not isinstance(value, int):
        raise ValueError(f"Expected integer id for {type(obj).__name__}, got {value!r}")
    return value


def _model_type(obj: Any) -> str | None:
    value = _safe_get(obj, "type", _safe_get(obj, "type_"))
    return value if isinstance(value, str) else None


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


def export_extract(extract: SheetExtract, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "sheet_metadata.json").write_text(
        json.dumps(extract.metadata, indent=2, ensure_ascii=True, default=str),
        encoding="utf-8",
    )
    extract.rows_df.to_csv(output_dir / "rows.csv", index=False)
    extract.cells_df.to_csv(output_dir / "cells.csv", index=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a Smartsheet sheet into pandas DataFrames.")
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
        "--output-dir",
        type=Path,
        default=None,
        help="If provided, writes rows.csv, cells.csv, and sheet_metadata.json.",
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

    if args.output_dir:
        export_extract(extract, args.output_dir)
        print(f"Export completed in {args.output_dir}")

    print(f"Sheet: {extract.metadata['sheet_name']} ({extract.metadata['sheet_id']})")
    print(f"Rows loaded: {len(extract.rows_df)}")
    print(f"Columns loaded: {extract.metadata['column_count']}")


if __name__ == "__main__":
    main()
