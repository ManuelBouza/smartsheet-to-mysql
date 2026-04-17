from __future__ import annotations

import pandas as pd

from smartsheet_sync.column_mapping import (
    CTM_ALLOWED_TARGET_COLUMNS,
    apply_explicit_column_mapping,
    project_to_allowed_target_columns,
)


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


def test_apply_explicit_column_mapping_resolves_trimmed_logical_and_camelcase_sources() -> None:
    df = pd.DataFrame(
        [
            {
                " Complaint Case ID ": "CC-100",
                " status ": "Open",
                " effectiveDate ": "2025-12-02",
            }
        ]
    )

    mapped_df = apply_explicit_column_mapping(df, table_name="CTM")

    assert mapped_df.loc[0, "complaintCaseId"] == "CC-100"
    assert mapped_df.loc[0, "status"] == "Open"
    assert mapped_df.loc[0, "effectiveDate"] == "2025-12-02"


def test_apply_explicit_column_mapping_does_not_use_object_columns_as_business_source() -> None:
    df = pd.DataFrame(
        [
            {
                "Escalation Received Date__object": {"objectType": "DATE", "value": "2025-12-02"},
                "escalationReceivedDate": None,
            }
        ]
    )

    mapped_df = apply_explicit_column_mapping(df, table_name="CTM")

    assert mapped_df.loc[0, "escalationReceivedDate"] is None


def test_project_to_allowed_target_columns_for_ctm_filters_raw_and_unknown_columns() -> None:
    df = pd.DataFrame(
        [
            {
                "Complaint Case ID": "CC-100",
                "complaintCaseId": "CC-100",
                "status": "Open",
                "__created_at": "2026-01-01 00:00:00",
                "__modified_at": "2026-01-01 00:30:00",
                "Unexpected": "value",
            }
        ]
    )

    projected_df = project_to_allowed_target_columns(df, table_name="CTM")

    assert "Complaint Case ID" not in projected_df.columns
    assert "Unexpected" not in projected_df.columns
    assert set(projected_df.columns) == {
        "complaintCaseId",
        "status",
        "__created_at",
        "__modified_at",
    }
    assert set(projected_df.columns).issubset(CTM_ALLOWED_TARGET_COLUMNS)


def test_project_to_allowed_target_columns_is_noop_for_unknown_table() -> None:
    df = pd.DataFrame([{"Complaint Case ID": "CC-100", "status": "Open"}])

    projected_df = project_to_allowed_target_columns(df, table_name="other_table")

    assert list(projected_df.columns) == ["Complaint Case ID", "status"]
