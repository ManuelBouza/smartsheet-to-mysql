#!/usr/bin/env python3
"""Fetch a Smartsheet sheet and load it into pandas."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import smartsheet
from dotenv import load_dotenv


DEFAULT_API_BASE = "https://api.smartsheet.com/2.0"
DEFAULT_PAGE_SIZE = 500


load_dotenv()


@dataclass
class SheetExtract:
    metadata: dict[str, Any]
    rows_df: pd.DataFrame
    cells_df: pd.DataFrame


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


def _extract_object_value(cell: Any) -> Any:
    object_value = _safe_get(cell, "object_value")
    if object_value is None:
        return None

    if hasattr(object_value, "to_dict"):
        return object_value.to_dict()

    return object_value


def _column_names(columns: list[Any]) -> dict[int, str]:
    counts: dict[str, int] = {}
    names: dict[int, str] = {}

    for column in columns:
        title = str(_safe_get(column, "title", f"column_{column.id}")).strip() or f"column_{column.id}"
        counts[title] = counts.get(title, 0) + 1
        if counts[title] == 1:
            names[column.id] = title
        else:
            names[column.id] = f"{title}__{column.id}"

    return names


def fetch_sheet(
    sheet_id: int,
    *,
    access_token: str | None = None,
    api_base: str = DEFAULT_API_BASE,
    page_size: int = DEFAULT_PAGE_SIZE,
    level: int = 2,
    include: list[str] | None = None,
) -> Any:
    client = build_client(access_token=access_token, api_base=api_base)
    include = include or ["columnType", "objectValue", "writerInfo", "summary"]

    first_page = client.Sheets.get_sheet(
        sheet_id,
        include=include,
        page_size=page_size,
        page=1,
        level=level,
    )

    all_rows = list(first_page.rows)
    total_rows = _safe_get(first_page, "total_row_count", len(all_rows))
    current_page = 1

    while len(all_rows) < total_rows:
        current_page += 1
        next_page = client.Sheets.get_sheet(
            sheet_id,
            include=include,
            page_size=page_size,
            page=current_page,
            level=level,
        )
        if not next_page.rows:
            break
        all_rows.extend(next_page.rows)

    first_page.rows = all_rows
    return first_page


def sheet_to_dataframes(sheet: Any) -> SheetExtract:
    columns = list(sheet.columns)
    column_names = _column_names(columns)
    column_meta = {
        column.id: {
            "column_id": column.id,
            "title": column_names[column.id],
            "original_title": _safe_get(column, "title"),
            "type": _safe_get(column, "type"),
            "index": _safe_get(column, "index"),
            "primary": _safe_get(column, "primary", False),
        }
        for column in columns
    }

    row_records: list[dict[str, Any]] = []
    cell_records: list[dict[str, Any]] = []

    for row in sheet.rows:
        row_record: dict[str, Any] = {
            "__row_id": _safe_get(row, "id"),
            "__row_number": _safe_get(row, "row_number"),
            "__parent_id": _safe_get(row, "parent_id"),
            "__sibling_id": _safe_get(row, "sibling_id"),
            "__expanded": _safe_get(row, "expanded"),
            "__created_at": _safe_get(row, "created_at"),
            "__modified_at": _safe_get(row, "modified_at"),
        }

        for cell in row.cells:
            column_id = _safe_get(cell, "column_id")
            column_name = column_names.get(column_id, f"column_{column_id}")
            value = _safe_get(cell, "value")
            display_value = _safe_get(cell, "display_value")
            object_value = _extract_object_value(cell)

            row_record[column_name] = value
            if display_value is not None and display_value != value:
                row_record[f"{column_name}__display"] = display_value
            if object_value is not None:
                row_record[f"{column_name}__object"] = json.dumps(object_value, ensure_ascii=True)

            cell_records.append(
                {
                    "sheet_id": _safe_get(sheet, "id"),
                    "sheet_name": _safe_get(sheet, "name"),
                    "row_id": _safe_get(row, "id"),
                    "row_number": _safe_get(row, "row_number"),
                    "column_id": column_id,
                    "column_title": column_name,
                    "column_type": column_meta.get(column_id, {}).get("type"),
                    "value": value,
                    "display_value": display_value,
                    "object_value": object_value,
                    "formula": _safe_get(cell, "formula"),
                    "hyperlink": _safe_get(cell, "hyperlink"),
                }
            )

        row_records.append(row_record)

    metadata = {
        "sheet_id": _safe_get(sheet, "id"),
        "sheet_name": _safe_get(sheet, "name"),
        "version": _safe_get(sheet, "version"),
        "from_id": _safe_get(sheet, "from_id"),
        "owner": _safe_get(sheet, "owner"),
        "access_level": _safe_get(sheet, "access_level"),
        "permalink": _safe_get(sheet, "permalink"),
        "total_row_count": _safe_get(sheet, "total_row_count", len(row_records)),
        "column_count": len(columns),
        "columns": list(column_meta.values()),
    }

    if _safe_get(sheet, "summary"):
        metadata["summary"] = [
            {
                "field_id": _safe_get(field, "id"),
                "title": _safe_get(field, "title"),
                "type": _safe_get(field, "type"),
                "value": _safe_get(field, "value"),
                "display_value": _safe_get(field, "display_value"),
            }
            for field in _safe_get(sheet.summary, "fields", []) or []
        ]

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
