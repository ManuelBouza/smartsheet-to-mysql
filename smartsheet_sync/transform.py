from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from smartsheet.models import Column, Sheet

from .common import (
    DELETED_AT_COLUMN,
    IS_DELETED_COLUMN,
    LAST_SYNCED_AT_COLUMN,
    as_list,
    extract_object_value,
    model_id,
    normalize_mysql_value,
)


def prepare_dataframe_for_mysql(df: pd.DataFrame) -> pd.DataFrame:
    normalized_df = df.apply(lambda column: column.map(normalize_mysql_value)).astype(object)
    return normalized_df.where(pd.notna(normalized_df), None)


def managed_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    managed_df = prepare_dataframe_for_mysql(df.copy())
    managed_df[LAST_SYNCED_AT_COLUMN] = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    managed_df[IS_DELETED_COLUMN] = False
    managed_df[DELETED_AT_COLUMN] = None
    return managed_df


def column_names(columns: list[Column]) -> dict[int, str]:
    counts: dict[str, int] = {}
    names: dict[int, str] = {}

    for column in columns:
        column_id = model_id(column)
        title = str(column.title or f"column_{column_id}").strip() or f"column_{column_id}"
        counts[title] = counts.get(title, 0) + 1
        names[column_id] = title if counts[title] == 1 else f"{title}__{column_id}"

    return names


def sheet_to_dataframe(sheet: Sheet) -> pd.DataFrame:
    columns = as_list(sheet.columns)
    resolved_column_names = column_names(columns)
    records: list[dict[str, Any]] = []

    for row in as_list(sheet.rows):
        record: dict[str, Any] = {
            "__row_id": model_id(row),
            "__row_number": row.row_number,
            "__parent_id": row.parent_id,
            "__sibling_id": row.sibling_id,
            "__created_at": row.created_at,
            "__modified_at": row.modified_at,
        }

        for cell in as_list(row.cells):
            column_id = cell.column_id
            if column_id is None:
                continue

            column_name = resolved_column_names.get(column_id, f"column_{column_id}")
            value = cell.value
            display_value = cell.display_value
            object_value = extract_object_value(cell)

            record[column_name] = value
            if display_value is not None and display_value != value:
                record[f"{column_name}__display"] = display_value
            if object_value is not None:
                record[f"{column_name}__object"] = object_value

        records.append(record)

    return pd.DataFrame(records)
