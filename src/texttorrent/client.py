"""Minimal TextTorrent API client for the endpoints the outreach system uses.

Wire contract per docs/PROVIDER.md (official docs, pending capture
verification — everything vendor-specific is quarantined in this module).
Auth is the ``X-API-SID`` / ``X-API-PUBLIC-KEY`` header pair loaded from the
Secrets Manager secret ``outreach/texttorrent``. All HTTP goes through
``salesforce.http.transport`` — the same injectable hook the Salesforce client
uses — so tests run against an in-memory fake.

Error semantics: 401/403/429 and 5xx raise ``TextTorrentApiError``; 404 does
NOT, because the vendor uses it for business conditions ("already started a
chat", "contact is blacklisted") that callers must branch on.
"""

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from salesforce import http
from salesforce.http import HttpRequest

BASE_URL = "https://api.texttorrent.com/api/v1"


class TextTorrentApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"TextTorrent API error {status}: {detail[:200]}")
        self.status = status


@dataclass(frozen=True)
class TextTorrentCredentials:
    api_sid: str
    public_key: str


def load_credentials(secret_id: str, secrets_client: Any) -> TextTorrentCredentials:
    data = json.loads(secrets_client.get_secret_value(SecretId=secret_id)["SecretString"])
    return TextTorrentCredentials(api_sid=data["api_sid"], public_key=data["public_key"])


def ten_digits(phone: str) -> str:
    """US number in any format -> the 10-digit form several endpoints expect."""
    return re.sub(r"\D", "", phone)[-10:]


def _multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"tt-{uuid.uuid4().hex}"
    parts = "".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in fields.items()
    )
    return (parts + f"--{boundary}--\r\n").encode(), f"multipart/form-data; boundary={boundary}"


def _scan_numbers(payload: Any) -> list[str]:
    """Collect every ``number`` field in an unknown-shaped response."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "number" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_scan_numbers(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_scan_numbers(item))
    return found


class TextTorrentClient:
    def __init__(self, creds: TextTorrentCredentials, base_url: str = BASE_URL) -> None:
        self._creds = creds
        self._base = base_url

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "X-API-SID": self._creds.api_sid,
            "X-API-PUBLIC-KEY": self._creds.public_key,
            "Accept": "application/json",
        }
        body: bytes | None = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(json_body).encode()
        elif form is not None:
            body, headers["Content-Type"] = _multipart(form)
        response = http.transport(HttpRequest(method, self._base + path, headers, body))
        raw = response.body.decode(errors="replace")
        if response.status in (401, 403, 429) or response.status >= 500:
            raise TextTorrentApiError(response.status, raw)
        try:
            envelope: dict[str, Any] = json.loads(raw)
        except ValueError as error:
            # Unknown routes return an HTML error page, not the JSON envelope.
            raise TextTorrentApiError(response.status, raw) from error
        return envelope

    def create_chat(self, receiver: str, sender: str) -> int | None:
        """Start a conversation; returns its chat id, or None if one already exists."""
        envelope = self._request(
            "POST",
            "/inbox/chat/create",
            json_body={"receiver_number": ten_digits(receiver), "sender_id": sender},
        )
        data = envelope.get("data")
        if envelope.get("success") and isinstance(data, dict):
            return int(data["id"])
        message = str(envelope.get("message", ""))
        if "already started" in message.lower():
            return None
        # e.g. "This contact is blacklisted." — callers must not silently continue.
        raise TextTorrentApiError(int(envelope.get("code") or 0), message)

    def find_chat_id(self, number: str) -> int | None:
        envelope = self._request("GET", f"/inbox?search={quote(ten_digits(number))}")
        chats = (envelope.get("data") or {}).get("data") or []
        for chat in chats:
            if isinstance(chat, dict) and chat.get("chat_id") is not None:
                return int(chat["chat_id"])
        return None

    def send_message(
        self, chat_id: int, from_number: str, to_number: str, message: str
    ) -> dict[str, Any]:
        envelope = self._request(
            "POST",
            "/inbox/chat",
            form={
                "message": message,
                "chat_id": str(chat_id),
                "from_number": from_number,
                "to_number": to_number,
            },
        )
        if not envelope.get("success"):
            raise TextTorrentApiError(int(envelope.get("code") or 0), str(envelope.get("message")))
        return dict(envelope["data"])

    def chat_messages(self, chat_id: int, limit: int = 20) -> list[dict[str, Any]]:
        envelope = self._request("GET", f"/inbox/{chat_id}?limit={limit}")
        messages = ((envelope.get("data") or {}).get("messages") or {}).get("data") or []
        return [dict(item) for item in messages]

    def active_numbers(self) -> list[str]:
        envelope = self._request("GET", "/inbox/numbers/active?limit=50")
        return _scan_numbers(envelope.get("data"))

    def blocked_numbers(self, page_size: int = 200) -> list[str]:
        """Every number on the vendor blocked list, raw, across all pages."""
        numbers: list[str] = []
        page = 1
        while True:
            envelope = self._request("GET", f"/contact/blocked-list/?limit={page_size}&page={page}")
            entries = ((envelope.get("data") or {}).get("blocked_list") or {}).get("data") or []
            numbers.extend(str(entry["number"]) for entry in entries if isinstance(entry, dict))
            if len(entries) < page_size:  # short (or empty) page: nothing further
                return numbers
            page += 1

    def block_numbers(self, numbers: list[str]) -> None:
        """Push opt-outs to the vendor blocked list (10-digit wire format)."""
        if not numbers:
            return
        envelope = self._request(
            "POST",
            "/contact/blocked-list/",
            json_body={"numbers": [ten_digits(number) for number in numbers]},
        )
        if not envelope.get("success"):
            # A silent push failure would leave an opt-out unenforced vendor-side.
            raise TextTorrentApiError(int(envelope.get("code") or 0), str(envelope.get("message")))
