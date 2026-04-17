from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .common import normalized_mysql_name

# Explicit legacy/camelCase mappings for existing CTM table contracts.
CTM_EXPLICIT_COLUMN_MAPPING: dict[str, str] = {
    "Accountable Broker": "accountableBroker",
    "Escalation Received Date": "escalationReceivedDate",
    "Status": "status",
    "Complaint Case ID": "complaintCaseId",
    "Carrier": "carrier",
    "Enrollment Date": "enrollmentDate",
    "Confirmation Number": "confirmationNumber",
    "Phone Number": "phoneNumber",
    "Plan Type": "planType",
    "State": "state",
    "Effective Date": "effectiveDate",
    "Member Name": "memberName",
    "CTM/Grievance": "CTMGrievance",
    "Grievance Description": "grievanceDescription",
    "Complaint Category": "complaintCategory",
    "Response Due Date": "responseDueDate",
    "Fault Outcome": "faultOutcome",
    "Escalator": "escalator",
    "Accountable BU": "accountableBU",
    "Response Sent to BU": "responseSentToBU",
}


KNOWN_EXPLICIT_COLUMN_MAPPINGS: dict[str, Mapping[str, str]] = {
    normalized_mysql_name("CTM"): CTM_EXPLICIT_COLUMN_MAPPING,
}


def apply_explicit_column_mapping(df: pd.DataFrame, *, table_name: str) -> pd.DataFrame:
    mapping = KNOWN_EXPLICIT_COLUMN_MAPPINGS.get(normalized_mysql_name(table_name))
    if not mapping:
        return df

    mapped_df = df.copy()
    for source_column, target_column in mapping.items():
        if source_column not in mapped_df.columns:
            continue

        source_series = mapped_df[source_column]
        if target_column in mapped_df.columns:
            mapped_df[target_column] = mapped_df[target_column].combine_first(source_series)
            continue

        mapped_df[target_column] = source_series

    return mapped_df
