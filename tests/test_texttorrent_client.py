"""Client-level wire tests: error semantics the endpoint fakes can't reach."""

import json
from typing import Any

import pytest

from salesforce.http import HttpResponse
from texttorrent.client import (
    TextTorrentApiError,
    TextTorrentClient,
    TextTorrentCredentials,
    ten_digits,
)

CREDS = TextTorrentCredentials(api_sid="SIDx", public_key="PKx")


def _canned(monkeypatch: pytest.MonkeyPatch, status: int, payload: Any) -> None:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    monkeypatch.setattr("salesforce.http.transport", lambda request: HttpResponse(status, {}, body))


def test_ten_digits_strips_formatting() -> None:
    assert ten_digits("+1 (650) 253-0001") == "6502530001"
    assert ten_digits("6502530001") == "6502530001"


def test_auth_errors_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _canned(monkeypatch, 401, {"message": "Unauthorized"})
    with pytest.raises(TextTorrentApiError) as excinfo:
        TextTorrentClient(CREDS).active_numbers()
    assert excinfo.value.status == 401


def test_html_error_page_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unknown routes return an HTML page, not the JSON envelope.
    _canned(monkeypatch, 200, b"<html>Oops! Something went wrong</html>")
    with pytest.raises(TextTorrentApiError):
        TextTorrentClient(CREDS).active_numbers()


def test_find_chat_id_none_when_absent_or_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    _canned(
        monkeypatch,
        200,
        {"code": 200, "success": True, "data": {"current_page": 1, "data": [{"foo": 1}]}},
    )
    assert TextTorrentClient(CREDS).find_chat_id("+16502530001") is None


def test_send_failure_envelope_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _canned(
        monkeypatch,
        422,
        {"code": 422, "success": False, "message": "Unable to send message.", "data": None},
    )
    with pytest.raises(TextTorrentApiError):
        TextTorrentClient(CREDS).send_message(1, "+16505550111", "+16502530001", "hi")


def test_block_push_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # A silent push failure would leave an opt-out unenforced vendor-side.
    _canned(monkeypatch, 422, {"code": 422, "success": False, "message": "nope", "data": None})
    with pytest.raises(TextTorrentApiError):
        TextTorrentClient(CREDS).block_numbers(["+16502530001"])
