from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from .common import normalized_mysql_name


@dataclass(frozen=True)
class TableSyncConfig:
    explicit_column_mapping: Mapping[str, str]
    allowed_business_target_columns: frozenset[str] | None = None
    default_technical_sync_columns: frozenset[str] | None = None
    verification_distinct_column: str | None = None


@dataclass(frozen=True)
class ColumnMappingRule:
    source_column: str
    target_column: str
    is_business_date: bool = False

# Explicit legacy/camelCase mappings for existing CTM table contracts.
CTM_COLUMN_RULES: tuple[ColumnMappingRule, ...] = (
    ColumnMappingRule("Accountable Broker", "accountableBroker"),
    ColumnMappingRule("Escalation Received Date", "escalationReceivedDate", is_business_date=True),
    ColumnMappingRule("Status", "status"),
    ColumnMappingRule("Complaint Case ID", "complaintCaseId"),
    ColumnMappingRule("Carrier", "carrier"),
    ColumnMappingRule("Enrollment Date", "enrollmentDate", is_business_date=True),
    ColumnMappingRule("Confirmation Number", "confirmationNumber"),
    ColumnMappingRule("Phone Number", "phoneNumber"),
    ColumnMappingRule("Plan Type", "planType"),
    ColumnMappingRule("State", "state"),
    ColumnMappingRule("Effective Date", "effectiveDate", is_business_date=True),
    ColumnMappingRule("Member Name", "memberName"),
    ColumnMappingRule("CTM/Grievance", "CTMGrievance"),
    ColumnMappingRule("Grievance Description", "grievanceDescription"),
    ColumnMappingRule("Complaint Category", "complaintCategory"),
    ColumnMappingRule("Response Due Date", "responseDueDate", is_business_date=True),
    ColumnMappingRule("Fault Outcome", "faultOutcome"),
    ColumnMappingRule("Escalator", "escalator"),
    ColumnMappingRule("Accountable BU", "accountableBU"),
    ColumnMappingRule("Response Sent to BU", "responseSentToBU"),
)


CTM_EXPLICIT_COLUMN_MAPPING: dict[str, str] = {
    rule.source_column: rule.target_column for rule in CTM_COLUMN_RULES
}


TECHNICAL_SYNC_COLUMNS: frozenset[str] = frozenset(
    {
        "__row_id",
        "__row_number",
        "__parent_id",
        "__sibling_id",
        "__created_at",
        "__modified_at",
        "last_synced_at",
        "is_deleted",
        "deleted_at",
    }
)


CTM_TECHNICAL_SYNC_COLUMNS: frozenset[str] = frozenset(
    {
        "__created_at",
        "__modified_at",
    }
)


CTM_ALLOWED_TARGET_COLUMNS: frozenset[str] = frozenset(
    {rule.target_column for rule in CTM_COLUMN_RULES} | set(CTM_TECHNICAL_SYNC_COLUMNS)
)


KNOWN_TABLE_SYNC_CONFIGS: dict[str, TableSyncConfig] = {
    normalized_mysql_name("CTM"): TableSyncConfig(
        explicit_column_mapping=CTM_EXPLICIT_COLUMN_MAPPING,
        allowed_business_target_columns=frozenset({rule.target_column for rule in CTM_COLUMN_RULES}),
        default_technical_sync_columns=CTM_TECHNICAL_SYNC_COLUMNS,
        verification_distinct_column="complaintCaseId",
    ),
}


KNOWN_EXPLICIT_COLUMN_MAPPINGS: dict[str, Mapping[str, str]] = {
    table_name: config.explicit_column_mapping
    for table_name, config in KNOWN_TABLE_SYNC_CONFIGS.items()
}


KNOWN_COLUMN_MAPPING_RULES: dict[str, tuple[ColumnMappingRule, ...]] = {
    normalized_mysql_name("CTM"): CTM_COLUMN_RULES,
}


def normalized_column_lookup_key(column_name: str) -> str:
    return " ".join(str(column_name).strip().split()).casefold()


def _extract_business_date_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (pd.Timestamp, datetime)):
        return value
    if isinstance(value, date):
        return value.isoformat()

    candidate = value
    if isinstance(candidate, str):
        stripped_candidate = candidate.strip()
        if stripped_candidate.startswith("{") and stripped_candidate.endswith("}"):
            try:
                candidate = json.loads(stripped_candidate)
            except json.JSONDecodeError:
                return value
        else:
            return value

    if not isinstance(candidate, dict):
        return value

    object_type = str(candidate.get("objectType", "")).strip().upper()
    if object_type != "DATE":
        return value

    return candidate.get("value")


def _extract_source_series(df: pd.DataFrame, *, source_column: str, is_business_date: bool) -> pd.Series:
    source_series = df[source_column]
    if not is_business_date:
        return source_series
    return source_series.map(_extract_business_date_value)


def apply_explicit_column_mapping(df: pd.DataFrame, *, table_name: str) -> pd.DataFrame:
    mapping = KNOWN_EXPLICIT_COLUMN_MAPPINGS.get(normalized_mysql_name(table_name))
    mapping_rules = KNOWN_COLUMN_MAPPING_RULES.get(normalized_mysql_name(table_name), ())
    if not mapping:
        return df

    mapped_df = df.copy()
    rules_by_target = {rule.target_column: rule for rule in mapping_rules}

    source_columns_by_normalized: dict[str, str] = {}
    for column_name in mapped_df.columns:
        if str(column_name).endswith("__object"):
            continue
        source_columns_by_normalized[normalized_column_lookup_key(column_name)] = column_name

    for source_column, target_column in mapping.items():
        rule = rules_by_target.get(target_column)
        source_column_name = source_columns_by_normalized.get(normalized_column_lookup_key(source_column))
        if source_column_name is None:
            source_column_name = source_columns_by_normalized.get(
                normalized_column_lookup_key(target_column)
            )
        if source_column_name is None:
            continue

        source_series = _extract_source_series(
            mapped_df,
            source_column=source_column_name,
            is_business_date=bool(rule and rule.is_business_date),
        )
        if target_column in mapped_df.columns:
            mapped_df[target_column] = mapped_df[target_column].combine_first(source_series)
            continue

        mapped_df[target_column] = source_series

    return mapped_df


def project_to_allowed_target_columns(df: pd.DataFrame, *, table_name: str) -> pd.DataFrame:
    return project_to_allowed_target_columns_with_technical_columns(df, table_name=table_name)


def normalize_technical_sync_columns(columns: set[str] | frozenset[str] | list[str] | tuple[str, ...]) -> frozenset[str]:
    normalized_columns = frozenset(column_name.strip() for column_name in columns if column_name and column_name.strip())
    unknown_columns = sorted(normalized_columns - TECHNICAL_SYNC_COLUMNS)
    if unknown_columns:
        allowed = ", ".join(sorted(TECHNICAL_SYNC_COLUMNS))
        unknown = ", ".join(unknown_columns)
        raise ValueError(
            f"Unknown technical column(s): {unknown}. Allowed technical columns: {allowed}."
        )
    return normalized_columns


def default_technical_sync_columns_for_table(table_name: str) -> frozenset[str]:
    table_config = KNOWN_TABLE_SYNC_CONFIGS.get(normalized_mysql_name(table_name))
    if table_config and table_config.default_technical_sync_columns is not None:
        return table_config.default_technical_sync_columns
    return TECHNICAL_SYNC_COLUMNS


def project_to_allowed_target_columns_with_technical_columns(
    df: pd.DataFrame,
    *,
    table_name: str,
    technical_sync_columns: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None = None,
) -> pd.DataFrame:
    resolved_technical_sync_columns = (
        default_technical_sync_columns_for_table(table_name)
        if technical_sync_columns is None
        else normalize_technical_sync_columns(technical_sync_columns)
    )

    projected_columns = [
        column_name
        for column_name in df.columns
        if column_name not in TECHNICAL_SYNC_COLUMNS or column_name in resolved_technical_sync_columns
    ]

    table_config = KNOWN_TABLE_SYNC_CONFIGS.get(normalized_mysql_name(table_name))
    if table_config is None or table_config.allowed_business_target_columns is None:
        return df.loc[:, projected_columns]

    allowed_target_columns = set(table_config.allowed_business_target_columns) | set(resolved_technical_sync_columns)
    projected_columns = [
        column_name for column_name in projected_columns if column_name in allowed_target_columns
    ]
    return df.loc[:, projected_columns]


def get_table_sync_config(table_name: str) -> TableSyncConfig | None:
    return KNOWN_TABLE_SYNC_CONFIGS.get(normalized_mysql_name(table_name))
