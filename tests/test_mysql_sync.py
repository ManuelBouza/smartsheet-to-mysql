from __future__ import annotations

import pandas as pd

from smartsheet_sync.column_mapping import apply_explicit_column_mapping
from smartsheet_sync.mysql_sync import (
    build_alignment_rename_map,
    column_type_for_series,
    legacy_row_id_pairs_for_backfill,
    mark_missing_rows_as_deleted,
    prepare_sync_dataframe,
    reconcile_legacy_key_changes_by_row_id,
    resolve_sync_timestamp,
    should_backfill_row_id_on_duplicate,
    validate_existing_table_for_sync,
    verification_distinct_payload,
    verify_sync,
)
from smartsheet_sync.models import SyncResult


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


def test_build_alignment_rename_map_skips_raw_rename_when_legacy_column_exists() -> None:
    rename_map = build_alignment_rename_map(
        column_names=["Status", "status", "Complaint Case ID", "complaintCaseId"],
        table_columns_by_normalized={
            "status": "status",
            "complaint case id": "complaintCaseId",
        },
    )

    assert rename_map == {}


def test_build_alignment_rename_map_renames_raw_column_when_legacy_missing() -> None:
    rename_map = build_alignment_rename_map(
        column_names=["Status", "Complaint Case ID"],
        table_columns_by_normalized={
            "status": "status",
            "complaint case id": "complaintCaseId",
        },
    )

    assert rename_map == {
        "Status": "status",
        "Complaint Case ID": "complaintCaseId",
    }


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


def test_validate_existing_table_for_sync_accepts_explicitly_mapped_pk() -> None:
    inspector = FakeInspector(
        columns=[
            {"name": "__row_id", "autoincrement": False, "default": None},
            {"name": "complaintCaseId", "autoincrement": False, "default": None},
        ],
        pk_columns=["__row_id", "complaintCaseId"],
    )
    df = pd.DataFrame([{"__row_id": 1, "Complaint Case ID": "CC-100"}])

    mapped_df = apply_explicit_column_mapping(df, table_name="CTM")

    validate_existing_table_for_sync(inspector, "CTM", mapped_df)
    assert mapped_df.loc[0, "complaintCaseId"] == "CC-100"


def test_prepare_sync_dataframe_projects_ctm_to_allowed_destination_columns() -> None:
    df = pd.DataFrame(
        [
            {
                "__row_id": 1,
                "__created_at": "2026-04-01 08:00:00",
                "__modified_at": "2026-04-02 09:00:00",
                "Complaint Case ID": "CC-100",
                "Status": "Open",
                "Accountable Broker": "Broker",
                "unexpected_raw_column": "x",
            }
        ]
    )

    prepared_df = prepare_sync_dataframe(df, table_name="CTM")

    assert "Complaint Case ID" not in prepared_df.columns
    assert "Status" not in prepared_df.columns
    assert "Accountable Broker" not in prepared_df.columns
    assert "unexpected_raw_column" not in prepared_df.columns
    assert "complaintCaseId" in prepared_df.columns
    assert "status" in prepared_df.columns
    assert "accountableBroker" in prepared_df.columns
    assert "__created_at" in prepared_df.columns
    assert "__modified_at" in prepared_df.columns
    assert "__row_id" not in prepared_df.columns
    assert "last_synced_at" not in prepared_df.columns
    assert "is_deleted" not in prepared_df.columns
    assert "deleted_at" not in prepared_df.columns


def test_prepare_sync_dataframe_keeps_non_ctm_payload_shape() -> None:
    df = pd.DataFrame([{"__row_id": 1, "Status": "Open", "unexpected_raw_column": "x"}])

    prepared_df = prepare_sync_dataframe(df, table_name="partner_downline_complaint_tracker")

    assert "Status" in prepared_df.columns
    assert "unexpected_raw_column" in prepared_df.columns


def test_resolve_sync_timestamp_falls_back_when_last_synced_missing() -> None:
    df = pd.DataFrame([{"complaintCaseId": "CC-1"}])

    synced_at = resolve_sync_timestamp(df)

    assert synced_at is not None


def test_prepare_sync_dataframe_maps_ctm_business_dates_from_date_objects() -> None:
    df = pd.DataFrame(
        [
            {
                "__row_id": 1,
                "Escalation Received Date": {"objectType": "DATE", "value": "2025-12-02"},
                "Enrollment Date": {"objectType": "DATE", "value": "2025-12-03"},
                "Response Due Date": {"objectType": "DATE", "value": "2025-12-04"},
            }
        ]
    )

    prepared_df = prepare_sync_dataframe(df, table_name="CTM")

    assert prepared_df.loc[0, "escalationReceivedDate"] == "2025-12-02"
    assert prepared_df.loc[0, "enrollmentDate"] == "2025-12-03"
    assert prepared_df.loc[0, "responseDueDate"] == "2025-12-04"


def test_prepare_sync_dataframe_ctm_runtime_technical_projection() -> None:
    df = pd.DataFrame(
        [
            {
                "Complaint Case ID": "CC-100",
                "Status": "Open",
                "__created_at": "2026-01-01 10:00:00",
                "__modified_at": "2026-01-01 10:30:00",
            }
        ]
    )

    prepared_df = prepare_sync_dataframe(
        df,
        table_name="CTM",
        technical_sync_columns={"__created_at", "last_synced_at"},
    )

    assert "complaintCaseId" in prepared_df.columns
    assert "status" in prepared_df.columns
    assert "__created_at" in prepared_df.columns
    assert "last_synced_at" in prepared_df.columns
    assert "__modified_at" not in prepared_df.columns
    assert "__row_id" not in prepared_df.columns


def test_mark_missing_rows_as_deleted_fails_fast_on_empty_payload(monkeypatch) -> None:
    class _Inspector:
        def get_columns(self, table_name: str) -> list[dict]:
            return [{"name": "__row_id"}]

    monkeypatch.setattr("smartsheet_sync.mysql_sync.inspect", lambda engine: _Inspector())

    try:
        mark_missing_rows_as_deleted(engine=object(), table_name="target_table", row_ids=[])
    except RuntimeError as exc:
        message = str(exc)
        assert "Unsafe soft-delete aborted" in message
        assert "empty payload" in message
    else:
        raise AssertionError("Expected mark_missing_rows_as_deleted to raise")


def test_should_backfill_row_id_on_duplicate_for_ctm_reduced_contract() -> None:
    should_backfill = should_backfill_row_id_on_duplicate(
        table_name="CTM",
        managed_columns=["complaintCaseId", "status", "__created_at", "__modified_at"],
    )

    assert should_backfill is False


def test_should_backfill_row_id_on_duplicate_false_for_regular_tables() -> None:
    should_backfill = should_backfill_row_id_on_duplicate(
        table_name="partner_downline_complaint_tracker",
        managed_columns=["__row_id", "complaintCaseId", "status"],
    )

    assert should_backfill is False


def test_should_backfill_row_id_on_duplicate_false_without_legacy_key() -> None:
    should_backfill = should_backfill_row_id_on_duplicate(
        table_name="CTM",
        managed_columns=["__row_id", "status"],
    )

    assert should_backfill is False


def test_legacy_row_id_pairs_for_backfill_skips_nulls_and_deduplicates_row_ids() -> None:
    df = pd.DataFrame(
        [
            {"__row_id": 10, "complaintCaseId": "CC-1"},
            {"__row_id": 10, "complaintCaseId": "CC-1-updated"},
            {"__row_id": None, "complaintCaseId": "CC-2"},
            {"__row_id": 20, "complaintCaseId": None},
            {"__row_id": 30, "complaintCaseId": "CC-3"},
        ]
    )

    pairs = legacy_row_id_pairs_for_backfill(df, key_column="complaintCaseId")

    assert pairs == [(10, "CC-1-updated"), (30, "CC-3")]


def test_reconcile_legacy_key_changes_by_row_id_runs_update_per_pair() -> None:
    class _Preparer:
        @staticmethod
        def quote_identifier(identifier: str) -> str:
            return f"`{identifier}`"

    class _Dialect:
        identifier_preparer = _Preparer()

    class _Engine:
        dialect = _Dialect()

    class _Connection:
        engine = _Engine()

        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple]] = []

        def exec_driver_sql(self, sql: str, params: tuple) -> None:
            self.calls.append((sql, params))

    connection = _Connection()
    reconcile_legacy_key_changes_by_row_id(
        connection,
        table_name="CTM",
        key_column="complaintCaseId",
        row_id_pairs=[(10, "CC-1"), (20, "CC-2")],
    )

    assert len(connection.calls) == 2
    assert connection.calls[0][1] == ("CC-1", 10, "CC-1")
    assert "UPDATE `CTM`" in connection.calls[0][0]
    assert "SET `complaintCaseId` = %s" in connection.calls[0][0]
    assert "`complaintCaseId` <> %s" in connection.calls[0][0]


def test_reconcile_legacy_key_changes_by_row_id_handles_real_case_pairs() -> None:
    df = pd.DataFrame(
        [
            {"__row_id": 65, "complaintCaseId": "2222"},
            {"__row_id": 66, "complaintCaseId": "4444"},
        ]
    )

    pairs = legacy_row_id_pairs_for_backfill(df, key_column="complaintCaseId")

    assert pairs == [(65, "2222"), (66, "4444")]


def test_verification_distinct_payload_for_ctm_uses_business_key() -> None:
    df = pd.DataFrame(
        [
            {"complaintCaseId": "CC-1"},
            {"complaintCaseId": "CC-1"},
            {"complaintCaseId": "CC-2"},
            {"complaintCaseId": None},
        ]
    )

    column_name, values = verification_distinct_payload(df, table_name="CTM")

    assert column_name == "complaintCaseId"
    assert values == ("CC-1", "CC-2")


def test_verify_sync_for_ctm_without_row_id_or_last_synced_at(monkeypatch) -> None:
    class _Inspector:
        @staticmethod
        def has_table(table_name: str) -> bool:
            return True

        @staticmethod
        def get_columns(table_name: str) -> list[dict]:
            return [
                {"name": "complaintCaseId"},
                {"name": "status"},
                {"name": "__created_at"},
                {"name": "__modified_at"},
            ]

    class _Preparer:
        @staticmethod
        def quote_identifier(identifier: str) -> str:
            return f"`{identifier}`"

    class _Dialect:
        identifier_preparer = _Preparer()

    class _Result:
        @staticmethod
        def mappings():
            class _Mappings:
                @staticmethod
                def one() -> dict[str, int]:
                    return {
                        "total_rows": 10,
                        "distinct_row_ids": 10,
                        "synced_rows": 10,
                        "stale_rows": 0,
                    }

            return _Mappings()

    class _Connection:
        @staticmethod
        def exec_driver_sql(sql: str, params: tuple):
            return _Result()

    class _Context:
        def __enter__(self):
            return _Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Engine:
        dialect = _Dialect()

        @staticmethod
        def begin():
            return _Context()

    monkeypatch.setattr("smartsheet_sync.mysql_sync.inspect", lambda engine: _Inspector())
    monkeypatch.setattr(
        "smartsheet_sync.mysql_sync.count_matching_distinct_values",
        lambda *args, **kwargs: 2,
    )

    verification = verify_sync(
        _Engine(),
        SyncResult(
            table_name="CTM",
            synced_at=pd.Timestamp("2026-04-17 10:00:00").to_pydatetime(),
            rows_in_payload=2,
            verification_distinct_column="complaintCaseId",
            verification_distinct_values=("CC-1", "CC-2"),
        ),
    )

    assert verification.total_rows == 10


def test_verify_sync_for_ctm_fails_when_business_key_count_drops(monkeypatch) -> None:
    class _Inspector:
        @staticmethod
        def has_table(table_name: str) -> bool:
            return True

        @staticmethod
        def get_columns(table_name: str) -> list[dict]:
            return [
                {"name": "complaintCaseId"},
                {"name": "status"},
                {"name": "__created_at"},
                {"name": "__modified_at"},
            ]

    class _Preparer:
        @staticmethod
        def quote_identifier(identifier: str) -> str:
            return f"`{identifier}`"

    class _Dialect:
        identifier_preparer = _Preparer()

    class _Result:
        @staticmethod
        def mappings():
            class _Mappings:
                @staticmethod
                def one() -> dict[str, int]:
                    return {
                        "total_rows": 10,
                        "distinct_row_ids": 10,
                        "synced_rows": 10,
                        "stale_rows": 0,
                    }

            return _Mappings()

    class _Connection:
        @staticmethod
        def exec_driver_sql(sql: str, params: tuple):
            return _Result()

    class _Context:
        def __enter__(self):
            return _Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Engine:
        dialect = _Dialect()

        @staticmethod
        def begin():
            return _Context()

    monkeypatch.setattr("smartsheet_sync.mysql_sync.inspect", lambda engine: _Inspector())
    monkeypatch.setattr(
        "smartsheet_sync.mysql_sync.count_matching_distinct_values",
        lambda *args, **kwargs: 1,
    )

    try:
        verify_sync(
            _Engine(),
            SyncResult(
                table_name="CTM",
                synced_at=pd.Timestamp("2026-04-17 10:00:00").to_pydatetime(),
                rows_in_payload=2,
                verification_distinct_column="complaintCaseId",
                verification_distinct_values=("CC-1", "CC-2"),
            ),
        )
    except RuntimeError as exc:
        assert "business-key" in str(exc)
    else:
        raise AssertionError("Expected verify_sync to fail when business-key count drops")


def test_verify_sync_without_last_synced_at_and_without_distinct_column(monkeypatch) -> None:
    class _Inspector:
        @staticmethod
        def has_table(table_name: str) -> bool:
            return True

        @staticmethod
        def get_columns(table_name: str) -> list[dict]:
            return [
                {"name": "status"},
                {"name": "__created_at"},
            ]

    class _Preparer:
        @staticmethod
        def quote_identifier(identifier: str) -> str:
            return f"`{identifier}`"

    class _Dialect:
        identifier_preparer = _Preparer()

    class _Result:
        @staticmethod
        def mappings():
            class _Mappings:
                @staticmethod
                def one() -> dict[str, int]:
                    return {
                        "total_rows": 3,
                        "distinct_row_ids": 3,
                        "synced_rows": 3,
                        "stale_rows": 0,
                    }

            return _Mappings()

    class _Connection:
        @staticmethod
        def exec_driver_sql(sql: str, params: tuple):
            return _Result()

    class _Context:
        def __enter__(self):
            return _Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Engine:
        dialect = _Dialect()

        @staticmethod
        def begin():
            return _Context()

    monkeypatch.setattr("smartsheet_sync.mysql_sync.inspect", lambda engine: _Inspector())

    verification = verify_sync(
        _Engine(),
        SyncResult(
            table_name="partner_downline_complaint_tracker",
            synced_at=pd.Timestamp("2026-04-17 10:00:00").to_pydatetime(),
            rows_in_payload=3,
        ),
    )

    assert verification.synced_rows == 3
