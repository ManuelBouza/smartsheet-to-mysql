from __future__ import annotations

import logging
import os

import smartsheet
from dotenv import load_dotenv
from smartsheet.models import Sheet

from .common import DEFAULT_API_BASE, DEFAULT_COMPAT_LEVEL, DEFAULT_PAGE_SIZE, as_list, require_sheet

load_dotenv()

LOGGER = logging.getLogger(__name__)


def build_smartsheet_client(
    *,
    access_token: str | None = None,
    api_base: str = DEFAULT_API_BASE,
) -> smartsheet.Smartsheet:
    token = access_token or os.getenv("SMARTSHEET_ACCESS_TOKEN")
    if not token:
        raise ValueError("Missing Smartsheet token. Set SMARTSHEET_ACCESS_TOKEN.")

    client = smartsheet.Smartsheet(access_token=token, api_base=api_base)
    client.errors_as_exceptions(True)
    return client


def fetch_sheet(
    sheet_id: int,
    *,
    api_base: str = DEFAULT_API_BASE,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Sheet:
    LOGGER.info("Fetching Smartsheet sheet %s", sheet_id)
    client = build_smartsheet_client(api_base=api_base)
    include = ["columnType", "objectValue", "writerInfo"]

    first_page = require_sheet(
        client.Sheets.get_sheet(
            sheet_id,
            include=include,
            page_size=page_size,
            page=1,
            level=DEFAULT_COMPAT_LEVEL,
        )
    )

    all_rows = as_list(first_page.rows)
    total_rows = first_page.total_row_count or len(all_rows)
    LOGGER.info("Fetched first page for sheet %s with %s/%s rows", sheet_id, len(all_rows), total_rows)

    if total_rows > 0 and not all_rows:
        LOGGER.warning("Paged fetch returned no rows for non-empty sheet %s; retrying without pagination", sheet_id)
        unpaginated_sheet = require_sheet(
            client.Sheets.get_sheet(
                sheet_id,
                include=include,
                level=DEFAULT_COMPAT_LEVEL,
            )
        )
        unpaginated_rows = as_list(unpaginated_sheet.rows)
        if unpaginated_rows:
            return unpaginated_sheet

    current_page = 1
    while len(all_rows) < total_rows:
        current_page += 1
        LOGGER.debug("Fetching page %s for sheet %s", current_page, sheet_id)
        next_page = require_sheet(
            client.Sheets.get_sheet(
                sheet_id,
                include=include,
                page_size=page_size,
                page=current_page,
                level=DEFAULT_COMPAT_LEVEL,
            )
        )
        page_rows = as_list(next_page.rows)
        if not page_rows:
            break
        all_rows.extend(page_rows)

    first_page.rows = all_rows
    LOGGER.info("Completed fetch for sheet %s with %s rows", sheet_id, len(all_rows))
    return first_page
