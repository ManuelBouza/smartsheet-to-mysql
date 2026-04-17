from __future__ import annotations

import logging
import os
import time
from typing import Any

import smartsheet
from dotenv import load_dotenv
from requests import exceptions as requests_exceptions
from smartsheet import exceptions as smartsheet_exceptions
from smartsheet.models import Sheet

from .common import DEFAULT_API_BASE, DEFAULT_COMPAT_LEVEL, DEFAULT_PAGE_SIZE, as_list, require_sheet

load_dotenv()

LOGGER = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_FETCH_MAX_ATTEMPTS = 3
DEFAULT_FETCH_RETRY_BACKOFF_SECONDS = 1.0

TRANSIENT_FETCH_EXCEPTIONS = (
    requests_exceptions.ConnectionError,
    requests_exceptions.Timeout,
    smartsheet_exceptions.InternalServerError,
    smartsheet_exceptions.RateLimitExceededError,
    smartsheet_exceptions.ServerTimeoutExceededError,
    smartsheet_exceptions.SystemMaintenanceError,
    smartsheet_exceptions.UnexpectedErrorShouldRetryError,
)


def _read_float_env(var_name: str, default: float) -> float:
    raw_value = os.getenv(var_name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        LOGGER.warning("Invalid %s value %r. Falling back to %s", var_name, raw_value, default)
        return default


def _read_int_env(var_name: str, default: int) -> int:
    raw_value = os.getenv(var_name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        LOGGER.warning("Invalid %s value %r. Falling back to %s", var_name, raw_value, default)
        return default
    return parsed if parsed > 0 else default


def _set_default_request_timeout(client: smartsheet.Smartsheet, timeout_seconds: float) -> None:
    if timeout_seconds <= 0:
        return

    session = getattr(client, "_session", None)
    if session is None or not hasattr(session, "request"):
        return

    original_request = session.request

    def request_with_timeout(method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", timeout_seconds)
        return original_request(method, url, **kwargs)

    session.request = request_with_timeout


def _get_sheet_page_with_retries(
    client: smartsheet.Smartsheet,
    *,
    sheet_id: int,
    include: list[str],
    page_size: int | None,
    page: int | None,
    max_attempts: int,
    retry_backoff_seconds: float,
) -> Sheet:
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            return require_sheet(
                client.Sheets.get_sheet(
                    sheet_id,
                    include=include,
                    page_size=page_size,
                    page=page,
                    level=DEFAULT_COMPAT_LEVEL,
                )
            )
        except TRANSIENT_FETCH_EXCEPTIONS as exc:
            if attempt >= attempts:
                raise RuntimeError(
                    f"Smartsheet fetch failed after {attempts} attempt(s) for sheet {sheet_id}, page {page or 'all'}."
                ) from exc
            sleep_seconds = max(0.0, retry_backoff_seconds) * attempt
            LOGGER.warning(
                "Transient Smartsheet error while fetching sheet %s page %s (attempt %s/%s). Retrying in %.1fs.",
                sheet_id,
                page or "all",
                attempt,
                attempts,
                sleep_seconds,
            )
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)


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
    timeout_seconds = _read_float_env("SMARTSHEET_REQUEST_TIMEOUT_SECONDS", DEFAULT_REQUEST_TIMEOUT_SECONDS)
    _set_default_request_timeout(client, timeout_seconds)
    return client


def fetch_sheet(
    sheet_id: int,
    *,
    api_base: str = DEFAULT_API_BASE,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Sheet:
    LOGGER.info("Fetching Smartsheet sheet %s", sheet_id)
    client = build_smartsheet_client(api_base=api_base)
    max_attempts = _read_int_env("SMARTSHEET_FETCH_MAX_ATTEMPTS", DEFAULT_FETCH_MAX_ATTEMPTS)
    retry_backoff_seconds = _read_float_env(
        "SMARTSHEET_FETCH_RETRY_BACKOFF_SECONDS", DEFAULT_FETCH_RETRY_BACKOFF_SECONDS
    )
    include = ["columnType", "objectValue", "writerInfo"]

    first_page = _get_sheet_page_with_retries(
        client,
        sheet_id=sheet_id,
        include=include,
        page_size=page_size,
        page=1,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )

    all_rows = as_list(first_page.rows)
    total_rows = first_page.total_row_count or len(all_rows)
    LOGGER.info("Fetched first page for sheet %s with %s/%s rows", sheet_id, len(all_rows), total_rows)

    if total_rows > 0 and not all_rows:
        LOGGER.warning("Paged fetch returned no rows for non-empty sheet %s; retrying without pagination", sheet_id)
        unpaginated_sheet = _get_sheet_page_with_retries(
            client,
            sheet_id=sheet_id,
            include=include,
            page_size=None,
            page=None,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        unpaginated_rows = as_list(unpaginated_sheet.rows)
        if unpaginated_rows:
            return unpaginated_sheet

    current_page = 1
    while len(all_rows) < total_rows:
        current_page += 1
        LOGGER.debug("Fetching page %s for sheet %s", current_page, sheet_id)
        next_page = _get_sheet_page_with_retries(
            client,
            sheet_id=sheet_id,
            include=include,
            page_size=page_size,
            page=current_page,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        page_rows = as_list(next_page.rows)
        if not page_rows:
            break
        all_rows.extend(page_rows)

    if len(all_rows) != total_rows:
        raise RuntimeError(
            f"Smartsheet paged fetch mismatch for sheet {sheet_id}: expected {total_rows} rows, got {len(all_rows)}. "
            "Aborting to avoid partial sync."
        )

    first_page.rows = all_rows
    LOGGER.info("Completed fetch for sheet %s with %s rows", sheet_id, len(all_rows))
    return first_page
