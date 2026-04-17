from __future__ import annotations

import pandas as pd

from smartsheet_sync.mysql_sync import column_type_for_series, validate_existing_table_for_sync


class FakeInspector:
    def __init__(self, columns: list[dict], pk_columns: list[str]) -> None:
        self._columns = columns
        self._pk_columns = pk_columns

    def get_columns(self, table_name: str) -> list[dict]:
        return self._columns

    def get_pk_constraint(self, table_name: str) -> dict[str, list[str]]:
        return {"constrained_columns": self._pk_columns}


def test_column_type_for_series_handles_deleted_flag() -> None:
    series = pd.Series([True, False])
    compiled = str(column_type_for_series("is_deleted", series))
    assert "BOOLEAN" in compiled.upper()


def test_validate_existing_table_for_sync_rejects_unmapped_required_pk() -> None:
    inspector = FakeInspector(
        columns=[
            {"name": "__row_id", "autoincrement": False, "default": None},
            {"name": "tenant_id", "autoincrement": False, "default": None},
        ],
        pk_columns=["__row_id", "tenant_id"],
    )
    df = pd.DataFrame([{"__row_id": 1, "name": "A"}])

    try:
        validate_existing_table_for_sync(inspector, "target_table", df)
    except RuntimeError as exc:
        assert "tenant_id" in str(exc)
    else:
        raise AssertionError("Expected validate_existing_table_for_sync to raise")
