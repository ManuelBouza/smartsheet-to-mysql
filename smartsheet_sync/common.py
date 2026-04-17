from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import date, datetime, timezone
from typing import Any, TypeVar

import pandas as pd
from smartsheet.models import Cell, Error, Sheet


DEFAULT_API_BASE = "https://api.smartsheet.com/2.0"
DEFAULT_PAGE_SIZE = 500
DEFAULT_COMPAT_LEVEL = 2
DEFAULT_MYSQL_PORT = 3306
DEFAULT_CHUNK_SIZE = 1000
LAST_SYNCED_AT_COLUMN = "last_synced_at"
DELETED_AT_COLUMN = "deleted_at"
IS_DELETED_COLUMN = "is_deleted"

ModelType = TypeVar("ModelType")


def as_list(value: Iterable[ModelType] | None) -> list[ModelType]:
    if value is None:
        return []
    return list(value)


def model_id(obj: Any) -> int:
    value = getattr(obj, "id", getattr(obj, "id_", None))
    if not isinstance(value, int):
        raise ValueError(f"Expected integer id for {type(obj).__name__}, got {value!r}")
    return value


def require_sheet(response: Sheet | Error) -> Sheet:
    if isinstance(response, Error):
        raise RuntimeError(response.message or "Smartsheet API returned an error response.")
    return response


def extract_object_value(cell: Cell) -> Any:
    object_value = getattr(cell, "object_value", None)
    if object_value is None:
        return None
    if hasattr(object_value, "to_dict"):
        return object_value.to_dict()
    return object_value


def sanitize_mysql_identifier(name: str, max_length: int = 64, preserve_case: bool = False) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z_]+", "_", name).strip("_")
    if not preserve_case:
        sanitized = sanitized.lower()
    sanitized = re.sub(r"_+", "_", sanitized)
    if not sanitized:
        sanitized = "smartsheet_sheet"
    if sanitized[0].isdigit():
        sanitized = f"sheet_{sanitized}"
    return sanitized[:max_length].rstrip("_") or "smartsheet_sheet"


def default_table_name(sheet_name: str) -> str:
    return sanitize_mysql_identifier(sheet_name)


def normalized_mysql_name(name: str) -> str:
    return name.casefold()


def normalize_mysql_value(value: Any) -> Any:
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

