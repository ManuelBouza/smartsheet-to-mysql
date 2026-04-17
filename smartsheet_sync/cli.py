from __future__ import annotations

import argparse
import logging
import os
from typing import cast

from dotenv import load_dotenv

from .common import DEFAULT_API_BASE, DEFAULT_CHUNK_SIZE, DEFAULT_MYSQL_PORT, DEFAULT_PAGE_SIZE, default_table_name, model_id
from .models import Args, ParsedNamespace
from .mysql_sync import build_mysql_engine, verify_sync, write_dataframe_to_mysql
from .smartsheet_client import fetch_sheet
from .transform import sheet_to_dataframe

load_dotenv()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def resolve_sheet_id(raw_sheet_id: str | None, parser: argparse.ArgumentParser) -> int:
    sheet_id_value = raw_sheet_id or os.getenv("SMARTSHEET_SHEET_ID")
    if not sheet_id_value:
        parser.error("Missing sheet_id. Pass it as an argument or set SMARTSHEET_SHEET_ID.")
        raise AssertionError("unreachable")

    try:
        return int(sheet_id_value)
    except ValueError:
        parser.error(f"Invalid sheet_id '{sheet_id_value}'. SMARTSHEET_SHEET_ID must be an integer.")
        raise AssertionError("unreachable")


def parse_args() -> Args:
    parser = argparse.ArgumentParser(description="Copy a Smartsheet sheet into MySQL.")
    parser.add_argument("sheet_id", nargs="?", help="Smartsheet sheet ID. Defaults to SMARTSHEET_SHEET_ID.")
    parser.add_argument(
        "--api-base",
        default=os.getenv("SMARTSHEET_API_BASE", DEFAULT_API_BASE),
        help="Smartsheet API base URL.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="Rows requested per Smartsheet API page.",
    )
    parser.add_argument(
        "--mysql-url",
        default=os.getenv("MYSQL_URL"),
        help="Full SQLAlchemy MySQL URL. Overrides MYSQL_HOST/PORT/DATABASE/USER/PASSWORD.",
    )
    parser.add_argument(
        "--mysql-host",
        default=os.getenv("MYSQL_HOST"),
        help="MySQL host when MYSQL_URL is not provided.",
    )
    parser.add_argument(
        "--mysql-port",
        type=int,
        default=int(os.getenv("MYSQL_PORT", str(DEFAULT_MYSQL_PORT))),
        help="MySQL port when MYSQL_URL is not provided.",
    )
    parser.add_argument(
        "--mysql-database",
        default=os.getenv("MYSQL_DATABASE"),
        help="MySQL database when MYSQL_URL is not provided.",
    )
    parser.add_argument(
        "--mysql-user",
        default=os.getenv("MYSQL_USER"),
        help="MySQL user when MYSQL_URL is not provided.",
    )
    parser.add_argument(
        "--mysql-password",
        default=os.getenv("MYSQL_PASSWORD"),
        help="MySQL password when MYSQL_URL is not provided.",
    )
    parser.add_argument(
        "--mysql-table",
        default=os.getenv("MYSQL_TABLE"),
        help="Target MySQL table. Defaults to a sanitized version of the sheet name.",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Batch size used when inserting rows into MySQL.",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging level: DEBUG, INFO, WARNING, ERROR.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and transform the sheet, but do not write anything to MySQL.",
    )
    parser.add_argument(
        "--mark-missing-as-deleted",
        action="store_true",
        help="Mark rows absent from the latest Smartsheet payload with is_deleted=TRUE and deleted_at timestamp.",
    )
    namespace = cast(ParsedNamespace, parser.parse_args(namespace=ParsedNamespace()))
    return Args(
        sheet_id=resolve_sheet_id(namespace.sheet_id, parser),
        api_base=namespace.api_base,
        page_size=namespace.page_size,
        mysql_url=namespace.mysql_url,
        mysql_host=namespace.mysql_host,
        mysql_port=namespace.mysql_port,
        mysql_database=namespace.mysql_database,
        mysql_user=namespace.mysql_user,
        mysql_password=namespace.mysql_password,
        mysql_table=namespace.mysql_table,
        chunksize=namespace.chunksize,
        log_level=namespace.log_level,
        dry_run=namespace.dry_run,
        mark_missing_as_deleted=namespace.mark_missing_as_deleted,
    )


def main() -> None:
    args = parse_args()
    configure_logging(args.log_level)
    logger = logging.getLogger(__name__)

    sheet = fetch_sheet(
        args.sheet_id,
        api_base=args.api_base,
        page_size=args.page_size,
    )
    dataframe = sheet_to_dataframe(sheet)
    target_table = args.mysql_table or default_table_name(sheet.name or str(args.sheet_id))

    if args.dry_run:
        logger.info("Dry run complete. No MySQL writes executed.")
        print(f"Sheet: {sheet.name} ({model_id(sheet)})")
        print(f"Rows fetched: {len(dataframe)}")
        print(f"Target table: {target_table}")
        print("Dry run: yes")
        print(f"Columns detected: {len(dataframe.columns)}")
        return

    engine = build_mysql_engine(
        mysql_url=args.mysql_url,
        host=args.mysql_host,
        port=args.mysql_port,
        database=args.mysql_database,
        user=args.mysql_user,
        password=args.mysql_password,
    )

    try:
        sync_result = write_dataframe_to_mysql(
            dataframe,
            table_name=target_table,
            engine=engine,
            chunksize=args.chunksize,
            mark_missing_as_deleted=args.mark_missing_as_deleted,
        )
        verification = verify_sync(engine, sync_result)
    finally:
        engine.dispose()

    print(f"Sheet: {sheet.name} ({model_id(sheet)})")
    print(f"Rows copied: {len(dataframe)}")
    print(f"MySQL table: {sync_result.table_name}")
    print("MySQL sync mode: upsert")
    print(f"Rows marked deleted: {sync_result.rows_marked_deleted}")
    print(f"Verification: total_rows={verification.total_rows}, distinct_row_ids={verification.distinct_row_ids}")
    print(f"Verification: synced_rows={verification.synced_rows}, stale_rows={verification.stale_rows}")
