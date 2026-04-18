from __future__ import annotations

from smartsheet_sync.cli import parse_args


def test_parse_args_reads_technical_columns_flags(monkeypatch) -> None:
    monkeypatch.setenv("SMARTSHEET_SHEET_ID", "123")
    monkeypatch.setattr(
        "sys.argv",
        [
            "sync_smartsheet_to_mysql.py",
            "--technical-columns",
            "__created_at,last_synced_at",
            "--include-technical-columns",
            "__row_id",
            "--exclude-technical-columns",
            "last_synced_at",
        ],
    )

    args = parse_args()

    assert args.sheet_id == 123
    assert args.technical_columns == ("__created_at", "last_synced_at")
    assert args.include_technical_columns == ("__row_id",)
    assert args.exclude_technical_columns == ("last_synced_at",)
