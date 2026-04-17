from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from smartsheet_sync.common import normalize_mysql_value, sanitize_mysql_identifier


def test_sanitize_mysql_identifier_normalizes_special_chars() -> None:
    assert sanitize_mysql_identifier("CTM / Partner Downline") == "ctm_partner_downline"


def test_sanitize_mysql_identifier_prefixes_digit_start() -> None:
    assert sanitize_mysql_identifier("2026 Report") == "sheet_2026_report"


def test_normalize_mysql_value_serializes_structures() -> None:
    assert normalize_mysql_value({"a": 1}) == '{"a": 1}'
    assert normalize_mysql_value([1, 2]) == "[1, 2]"


def test_normalize_mysql_value_converts_timezone_aware_datetime() -> None:
    value = datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc)
    assert normalize_mysql_value(value) == datetime(2026, 4, 17, 10, 0)


def test_normalize_mysql_value_converts_date_to_iso() -> None:
    assert normalize_mysql_value(date(2026, 4, 17)) == "2026-04-17"


def test_normalize_mysql_value_replaces_nan_with_none() -> None:
    assert normalize_mysql_value(pd.NA) is None
