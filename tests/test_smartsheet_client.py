from __future__ import annotations

from types import SimpleNamespace

from requests import exceptions as requests_exceptions

from smartsheet_sync.smartsheet_client import fetch_sheet


class FakeSheetsApi:
    def __init__(self, responses: list[object]) -> None:
        self._responses = responses
        self.calls: list[dict[str, int | None]] = []

    def get_sheet(self, sheet_id: int, **kwargs):
        self.calls.append({"sheet_id": sheet_id, "page": kwargs.get("page")})
        if not self._responses:
            raise AssertionError("No fake response left for get_sheet")

        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _fake_sheet(*, rows: list[int], total_row_count: int, name: str = "Demo") -> SimpleNamespace:
    return SimpleNamespace(rows=rows, total_row_count=total_row_count, name=name)


def test_fetch_sheet_aborts_when_pagination_count_mismatch(monkeypatch) -> None:
    fake_sheets = FakeSheetsApi(
        responses=[
            _fake_sheet(rows=[1, 2], total_row_count=3),
            _fake_sheet(rows=[], total_row_count=3),
        ]
    )
    fake_client = SimpleNamespace(Sheets=fake_sheets)

    monkeypatch.setattr("smartsheet_sync.smartsheet_client.build_smartsheet_client", lambda **_: fake_client)

    try:
        fetch_sheet(99)
    except RuntimeError as exc:
        assert "paged fetch mismatch" in str(exc).lower()
        assert "expected 3 rows, got 2" in str(exc).lower()
    else:
        raise AssertionError("Expected fetch_sheet to abort on pagination mismatch")


def test_fetch_sheet_retries_transient_errors(monkeypatch) -> None:
    fake_sheets = FakeSheetsApi(
        responses=[
            requests_exceptions.Timeout("temporary timeout"),
            _fake_sheet(rows=[1], total_row_count=1),
        ]
    )
    fake_client = SimpleNamespace(Sheets=fake_sheets)

    monkeypatch.setattr("smartsheet_sync.smartsheet_client.build_smartsheet_client", lambda **_: fake_client)
    monkeypatch.setattr("smartsheet_sync.smartsheet_client.time.sleep", lambda _: None)

    sheet = fetch_sheet(77)

    assert sheet.total_row_count == 1
    assert list(sheet.rows) == [1]
    assert len(fake_sheets.calls) == 2
