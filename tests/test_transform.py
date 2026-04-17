from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from smartsheet_sync.transform import managed_dataframe, sheet_to_dataframe


class FakeObjectValue:
    def __init__(self, payload: dict[str, str]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, str]:
        return self.payload


def test_sheet_to_dataframe_maps_cells_and_metadata() -> None:
    sheet = SimpleNamespace(
        columns=[
            SimpleNamespace(id=1, title="Status"),
            SimpleNamespace(id=2, title="Status"),
        ],
        rows=[
            SimpleNamespace(
                id=123,
                row_number=4,
                parent_id=None,
                sibling_id=None,
                created_at=datetime(2026, 4, 1, 8, 0),
                modified_at=datetime(2026, 4, 2, 9, 0),
                cells=[
                    SimpleNamespace(column_id=1, value="Open", display_value="Open", object_value=None),
                    SimpleNamespace(column_id=2, value="A", display_value="Alpha", object_value=FakeObjectValue({"k": "v"})),
                ],
            )
        ],
    )

    df = sheet_to_dataframe(sheet)

    assert df.loc[0, "__row_id"] == 123
    assert df.loc[0, "Status"] == "Open"
    assert df.loc[0, "Status__2"] == "A"
    assert df.loc[0, "Status__2__display"] == "Alpha"
    assert df.loc[0, "Status__2__object"] == {"k": "v"}


def test_managed_dataframe_adds_sync_management_columns() -> None:
    df = managed_dataframe(sheet_to_dataframe(SimpleNamespace(columns=[], rows=[])))

    assert "last_synced_at" in df.columns
    assert "is_deleted" in df.columns
    assert "deleted_at" in df.columns
    assert df.empty
