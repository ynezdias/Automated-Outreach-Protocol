"""In-memory fake of the TextTorrent API surface the integration uses.

Speaks the real wire contract (docs/PROVIDER.md): X-API-SID/X-API-PUBLIC-KEY
header auth, the ``{code, success, message, data, errors}`` envelope, the
chat-create -> multipart-send flow, read-back via chat details, and the
paginated blocked list. ``rewrite_hook`` simulates the vendor's AI rewriter
mutating stored message text — the exact failure the canary exists to catch.
404 is used for business conditions (chat already exists, contact
blacklisted), mirroring the vendor.
"""

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

from salesforce.http import HttpRequest, HttpResponse

API_SID = "SIDtest0000000000000000000000000000000000000000"
PUBLIC_KEY = "PKtest00000000000000000000000000000000000000000"  # pragma: allowlist secret
BASE_URL = "https://api.texttorrent.com/api/v1"
ACTIVE_NUMBER = "+16505550111"


def _parse_multipart(body: bytes, content_type: str) -> dict[str, str]:
    boundary = content_type.split("boundary=", 1)[1]
    fields: dict[str, str] = {}
    for part in body.decode().split(f"--{boundary}"):
        if "Content-Disposition" in part:
            header, _, value = part.partition("\r\n\r\n")
            name_match = re.search(r'name="([^"]+)"', header)
            assert name_match is not None
            fields[name_match.group(1)] = value.removesuffix("\r\n")
    return fields


class FakeTextTorrent:
    def __init__(
        self,
        *,
        rewrite_hook: Callable[[str], str] | None = None,
        reject_statuses: list[int] | None = None,
    ) -> None:
        self.rewrite_hook = rewrite_hook
        self.reject_statuses = list(reject_statuses or [])
        self.opt_outs: list[str] = []  # vendor blocked list, raw entries
        self.pushed_numbers: list[str] = []  # 10-digit numbers received via add
        self.chats: dict[int, dict[str, Any]] = {}
        self.messages: dict[int, str] = {}  # message id -> stored (possibly rewritten) text
        self.blocked_list_requests = 0
        self._next_chat_id = 1000
        self._next_message_id = 5000

    # --- transport --------------------------------------------------------------

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if self.reject_statuses:
            return HttpResponse(self.reject_statuses.pop(0), {}, b"<html>vendor error</html>")
        assert request.headers.get("X-API-SID") == API_SID, "missing X-API-SID"
        assert request.headers.get("X-API-PUBLIC-KEY") == PUBLIC_KEY, "missing X-API-PUBLIC-KEY"
        assert request.url.startswith(BASE_URL), f"unexpected host: {request.url}"
        parsed = urlparse(request.url)
        path = parsed.path.removeprefix(urlparse(BASE_URL).path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if path == "/inbox/chat/create" and request.method == "POST":
            return self._chat_create(request)
        if path == "/inbox" and request.method == "GET":
            return self._inbox_search(query)
        if path == "/inbox/chat" and request.method == "POST":
            return self._send(request)
        if path == "/inbox/numbers/active" and request.method == "GET":
            data = {"current_page": 1, "data": [{"id": 1, "number": ACTIVE_NUMBER}]}
            return self._envelope(200, 200, True, "Active numbers fetched", data)
        chat = re.fullmatch(r"/inbox/(\d+)", path)
        if chat and request.method == "GET":
            return self._chat_details(int(chat.group(1)))
        if path == "/contact/blocked-list/" and request.method == "GET":
            return self._blocked_list(query)
        if path == "/contact/blocked-list/" and request.method == "POST":
            return self._block(request)
        raise AssertionError(f"unexpected request: {request.method} {path}")

    # --- endpoints --------------------------------------------------------------

    def _chat_create(self, request: HttpRequest) -> HttpResponse:
        assert request.body is not None
        payload = json.loads(request.body)
        receiver = str(payload["receiver_number"])
        assert re.fullmatch(r"\d{10}", receiver), "receiver must be 10 digits without +1"
        if any(receiver == re.sub(r"\D", "", raw)[-10:] for raw in self.opt_outs):
            return self._envelope(404, 404, False, "This contact is blacklisted.", None)
        for chat in self.chats.values():
            if chat["receiver"] == receiver:
                return self._envelope(
                    404, 404, False, "You have already started a chat with this contact.", None
                )
        self._next_chat_id += 1
        chat_id = self._next_chat_id
        self.chats[chat_id] = {
            "receiver": receiver,
            "sender": str(payload["sender_id"]),
            "messages": [],
        }
        data = {"id": chat_id, "user_id": 1, "contact_id": chat_id, "last_message": None}
        return self._envelope(201, 201, True, "Chat started successfully.", data)

    def _inbox_search(self, query: dict[str, str]) -> HttpResponse:
        search = query.get("search", "")
        rows = [
            {"chat_id": chat_id, "number": f"+1{chat['receiver']}"}
            for chat_id, chat in self.chats.items()
            if search in chat["receiver"]
        ]
        data = {"current_page": 1, "data": rows}
        return self._envelope(200, 200, True, "Chats retrieved successfully", data)

    def _send(self, request: HttpRequest) -> HttpResponse:
        assert request.body is not None
        content_type = request.headers.get("Content-Type", "")
        assert content_type.startswith("multipart/form-data"), "send must be multipart"
        fields = _parse_multipart(request.body, content_type)
        chat = self.chats[int(fields["chat_id"])]
        submitted = fields["message"]
        stored = submitted if self.rewrite_hook is None else self.rewrite_hook(submitted)
        self._next_message_id += 1
        message_id = self._next_message_id
        self.messages[message_id] = stored
        chat["messages"].append(
            {
                "id": message_id,
                "chat_id": int(fields["chat_id"]),
                "message": stored,
                "direction": "outbound",
                "status": 1,
                "from_number": fields["from_number"],
                "to_number": fields["to_number"],
                "media_url": None,
            }
        )
        data = {"id": message_id, "chat_id": int(fields["chat_id"]), "direction": "outbound"}
        return self._envelope(201, 201, True, "Message send successfully", data)

    def _chat_details(self, chat_id: int) -> HttpResponse:
        chat = self.chats[chat_id]
        data = {
            "chat": {"chat_id": chat_id, "number": f"+1{chat['receiver']}"},
            "messages": {"current_page": 1, "data": list(reversed(chat["messages"]))},
        }
        return self._envelope(200, 200, True, "Chat retrieved successfully", data)

    def _blocked_list(self, query: dict[str, str]) -> HttpResponse:
        self.blocked_list_requests += 1
        limit = int(query.get("limit", "10"))
        page = int(query.get("page", "1"))
        start = (page - 1) * limit
        rows = [
            {"id": start + offset, "number": raw}
            for offset, raw in enumerate(self.opt_outs[start : start + limit])
        ]
        data = {"blocked_list": {"current_page": page, "data": rows}}
        return self._envelope(200, 200, True, "Blocked list fetched successfully", data)

    def _block(self, request: HttpRequest) -> HttpResponse:
        assert request.body is not None
        numbers = [str(number) for number in json.loads(request.body)["numbers"]]
        for number in numbers:
            assert re.fullmatch(r"\d{10}", number), "blocked numbers are 10-digit, no +1"
        self.pushed_numbers.extend(numbers)
        self.opt_outs.extend(f"+1{number}" for number in numbers)
        return self._envelope(200, 200, True, "Numbers blocked successfully", None)

    def _envelope(
        self, http_status: int, code: int, success: bool, message: str, data: Any
    ) -> HttpResponse:
        body = {"code": code, "success": success, "message": message, "data": data, "errors": None}
        return HttpResponse(http_status, {}, json.dumps(body).encode())
