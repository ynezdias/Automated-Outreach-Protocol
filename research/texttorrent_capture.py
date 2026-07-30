"""TextTorrent live-contract capture harness.

Exercises the documented API surface against the real account and dumps every
raw request/response pair to disk, so docs/PROVIDER.md can be rewritten from
captured evidence instead of documentation claims (this repo has already been
burned once by a guessed contract — see ADR-016/017).

Usage::

    export TT_API_SID=SID... TT_API_PUBLIC_KEY=PK...   # or --secret-id
    uv run python research/texttorrent_capture.py --to-number +1XXXXXXXXXX \
        [--from-number +1YYYYYYYYYY] [--poll-minutes 10] [--out DIR]

Steps (each writes ``NN_name.json`` to the output directory):

    01 auth_me           GET  /user/auth/me            (auth + envelope shape)
    02 unauthenticated   GET  /user/auth/me, no auth   (401 error shape)
    03 active_numbers    GET  /inbox/numbers/active    (sender inventory)
    04 inbox_list        GET  /inbox?limit=10          (poll contract, chat list)
    05 chat_create       POST /inbox/chat/create       (or find existing chat)
    06 send              POST /inbox/chat              (multipart, canary body)
    07 poll_NN           GET  /inbox/{chat_id}         (every 10s: reply +
                                                        outbound status changes)
    08 bad_request       POST /inbox/chat, empty form  (422 validation shape)
    09 not_found         GET  /inbox/999999999         (404 shape)

The canary message body deliberately contains characters an AI "cleaner" is
likely to rewrite (curly quotes, an em dash, an accent, an emoji): compare the
text on the receiving phone byte-for-byte against CANARY_TEXT for Conflict A
evidence. The docs state sends are "automatically cleaned using AI".

Raw captures default to a directory OUTSIDE the repo (~/texttorrent-captures):
they contain real phone numbers and message content, and this working copy has
an editor auto-commit extension that has already pushed PII once. Sanitized
fixtures are copied into tests/fixtures/texttorrent/ as a separate, deliberate
step. Auth header values are redacted in the dumps; nothing else is altered.
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://api.texttorrent.com/api/v1"
SECONDS_BETWEEN_CALLS = 1.1  # documented limit: 60 requests/minute
CANARY_TEXT = (
    "FundMate contract test — please reply “ok” when you see this. "
    "Café ✓ " + datetime.now(UTC).strftime("%H%M%S")
)


class Capture:
    def __init__(self, out_dir: Path, sid: str, public_key: str) -> None:
        self.out_dir = out_dir
        self.sid = sid
        self.public_key = public_key
        self.seq = 0

    def auth_headers(self) -> dict[str, str]:
        return {"X-API-SID": self.sid, "X-API-PUBLIC-KEY": self.public_key}

    def call(
        self,
        name: str,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        """Perform one request and dump the full exchange to NN_name.json."""
        time.sleep(SECONDS_BETWEEN_CALLS)
        url = BASE_URL + path
        sent_headers = {"Accept": "application/json", **(headers or {})}
        if authenticated:
            sent_headers.update(self.auth_headers())
        started = datetime.now(UTC).isoformat()
        request = Request(url, data=body, headers=sent_headers, method=method)
        try:
            with urlopen(request, timeout=60) as response:
                status, resp_headers, raw = response.status, dict(response.headers), response.read()
        except HTTPError as error:
            status, resp_headers, raw = error.code, dict(error.headers), error.read()
        except URLError as error:
            status, resp_headers, raw = -1, {}, str(error.reason).encode()
        try:
            parsed: Any = json.loads(raw)
        except ValueError:
            parsed = raw.decode(errors="replace")
        record = {
            "step": name,
            "captured_at": started,
            "request": {
                "method": method,
                "url": url,
                "headers": self._redact(sent_headers),
                "body": body.decode(errors="replace") if body else None,
            },
            "response": {"status": status, "headers": resp_headers, "body": parsed},
        }
        self.seq += 1
        out = self.out_dir / f"{self.seq:02d}_{name}.json"
        out.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"  {out.name}: HTTP {status}")
        return record

    @staticmethod
    def _redact(headers: dict[str, str]) -> dict[str, str]:
        redacted = dict(headers)
        if "X-API-SID" in redacted:
            redacted["X-API-SID"] = "SID_REDACTED"
        if "X-API-PUBLIC-KEY" in redacted:
            redacted["X-API-PUBLIC-KEY"] = "PK_REDACTED"
        return redacted


def multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"tt-capture-{uuid.uuid4().hex}"
    parts = [
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n')
        for name, value in fields.items()
    ]
    body = ("".join(parts) + f"--{boundary}--\r\n").encode()
    return body, f"multipart/form-data; boundary={boundary}"


def find_numbers(payload: Any) -> list[str]:
    """Best-effort scan for phone numbers in an unknown response shape."""
    found: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            found.extend(find_numbers(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(find_numbers(item))
    elif isinstance(payload, str) and re.fullmatch(r"\+?1?\d{10}", payload.replace("-", "")):
        found.append(payload)
    return found


def find_chat_id(payload: Any) -> int | None:
    """Best-effort scan for a chat/conversation id (chat_id or id key)."""
    if isinstance(payload, dict):
        for key in ("chat_id", "id"):
            if isinstance(payload.get(key), int):
                return int(payload[key])
        for value in payload.values():
            got = find_chat_id(value)
            if got is not None:
                return got
    if isinstance(payload, list):
        for item in payload:
            got = find_chat_id(item)
            if got is not None:
                return got
    return None


def load_credentials(args: argparse.Namespace) -> tuple[str, str]:
    sid = os.environ.get("TT_API_SID")
    public_key = os.environ.get("TT_API_PUBLIC_KEY")
    if args.secret_id:
        import boto3

        raw = boto3.client("secretsmanager").get_secret_value(SecretId=args.secret_id)
        data = json.loads(raw["SecretString"])
        sid, public_key = data["api_sid"], data["public_key"]
    if not sid or not public_key:
        sys.exit("No credentials: set TT_API_SID + TT_API_PUBLIC_KEY or pass --secret-id.")
    return sid, public_key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to-number", required=True, help="Your own phone, E.164 (+1...)")
    parser.add_argument("--from-number", help="Sender; defaults to first active number found")
    parser.add_argument("--poll-minutes", type=int, default=10)
    parser.add_argument("--secret-id", help="Secrets Manager id holding {api_sid, public_key}")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home() / "texttorrent-captures",
        help="Raw capture directory (keep OUTSIDE the repo)",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sid, public_key = load_credentials(args)
    cap = Capture(args.out, sid, public_key)
    print(f"Capturing to {args.out}")

    # Live-verified 2026-07-30: this route is POST; GET returns a router error.
    cap.call("auth_me", "POST", "/user/auth/me")
    cap.call("unauthenticated", "POST", "/user/auth/me", authenticated=False)
    numbers = cap.call("active_numbers", "GET", "/inbox/numbers/active")
    cap.call("inbox_list", "GET", "/inbox?limit=10")

    from_number = args.from_number or next(iter(find_numbers(numbers["response"]["body"])), None)
    if not from_number:
        sys.exit("Could not identify an active sender number; pass --from-number.")
    print(f"  sender: {from_number}")

    receiver_10_digits = re.sub(r"\D", "", args.to_number)[-10:]
    created = cap.call(
        "chat_create",
        "POST",
        "/inbox/chat/create",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"receiver_number": receiver_10_digits, "sender_id": from_number}).encode(),
    )
    chat_id = find_chat_id(created["response"]["body"])
    if chat_id is None:
        # Documented 404 when a chat already exists — find it in the inbox.
        search = cap.call("chat_search", "GET", f"/inbox?search={receiver_10_digits}")
        chat_id = find_chat_id(search["response"]["body"])
    if chat_id is None:
        sys.exit("No chat_id from create or search; inspect the captures and rerun.")
    print(f"  chat_id: {chat_id}")

    body, content_type = multipart(
        {
            "message": CANARY_TEXT,
            "chat_id": str(chat_id),
            "from_number": from_number,
            "to_number": args.to_number,
        }
    )
    cap.call("send", "POST", "/inbox/chat", headers={"Content-Type": content_type}, body=body)
    print(f"  sent canary: {CANARY_TEXT!r}")
    print("  REPLY FROM YOUR PHONE NOW — polling for the inbound message...")

    deadline = time.monotonic() + args.poll_minutes * 60
    last_snapshot = ""
    while time.monotonic() < deadline:
        snap = cap.call("poll", "GET", f"/inbox/{chat_id}?limit=20")
        rendered = json.dumps(snap["response"]["body"], sort_keys=True)
        if rendered == last_snapshot:
            cap.seq -= 1  # unchanged snapshot: overwrite the same file next time
        last_snapshot = rendered
        if '"direction": "inbound"' in json.dumps(snap["response"]["body"], indent=1):
            print("  inbound reply captured.")
            break
        time.sleep(10)
    else:
        print("  WARNING: no inbound reply arrived before the deadline.")

    empty_body, empty_type = multipart({})
    cap.call(
        "bad_request", "POST", "/inbox/chat", headers={"Content-Type": empty_type}, body=empty_body
    )
    cap.call("not_found", "GET", "/inbox/999999999")
    print("Done. Sanitize before committing anything as a fixture.")


if __name__ == "__main__":
    main()
