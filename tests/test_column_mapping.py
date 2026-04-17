from __future__ import annotations

import pandas as pd

from smartsheet_sync.column_mapping import apply_explicit_column_mapping


def test_apply_explicit_column_mapping_populates_ctm_legacy_columns() -> None:
    df = pd.DataFrame(
        [
            {
                "Complaint Case ID": "CC-100",
                "Status": "Open",
                "Carrier": "Carrier A",
            }
        ]
    )

    mapped_df = apply_explicit_column_mapping(df, table_name="CTM")

    assert mapped_df.loc[0, "Complaint Case ID"] == "CC-100"
    assert mapped_df.loc[0, "complaintCaseId"] == "CC-100"
    assert mapped_df.loc[0, "status"] == "Open"
    assert mapped_df.loc[0, "carrier"] == "Carrier A"


def test_apply_explicit_column_mapping_is_noop_for_unknown_table() -> None:
    df = pd.DataFrame([{"Complaint Case ID": "CC-100"}])

    mapped_df = apply_explicit_column_mapping(df, table_name="other_table")

    assert "complaintCaseId" not in mapped_df.columns
    assert list(mapped_df.columns) == ["Complaint Case ID"]


def test_apply_explicit_column_mapping_keeps_existing_non_null_target_values() -> None:
    df = pd.DataFrame(
        [
            {"Complaint Case ID": "CC-100", "complaintCaseId": None},
            {"Complaint Case ID": "CC-200", "complaintCaseId": "CC-legacy"},
        ]
    )

    mapped_df = apply_explicit_column_mapping(df, table_name="CTM")

    assert mapped_df.loc[0, "complaintCaseId"] == "CC-100"
    assert mapped_df.loc[1, "complaintCaseId"] == "CC-legacy"
