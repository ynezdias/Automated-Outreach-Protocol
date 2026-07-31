"""Send exactly ONE real SMS through the TextTorrent API. Diagnostic only.

No Salesforce, no retries, no polling. Prints every wire exchange in full
(credentials redacted) and dumps raw request/response pairs to
research/output/ (gitignored — they contain real phone numbers).

    uv run python research/send_one.py --to +1XXXXXXXXXX --body "text"
        [--from-number +1YYYYYYYYYY] [--secret-id outreach/texttorrent]

Credentials: env TT_API_SID / TT_API_PUBLIC_KEY, else Secrets Manager.
The wire contract is the live-verified one from docs/PROVIDER.md: create (or
find) the chat, then a multipart send; the provider message id is the integer
``data.id`` in the send response.
"""

import argparse
import json
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_URL = "https://api.texttorrent.com/api/v1"
OUT_DIR = Path(__file__).resolve().parent / "output"
REDACTED = ("X-API-SID", "X-API-PUBLIC-KEY")


def credentials(secret_id: str) -> tuple[str, str]:
    sid, key = os.environ.get("TT_API_SID"), os.environ.get("TT_API_PUBLIC_KEY")
    if sid and key:
        return sid, key
    import boto3

    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-2"))
    data = json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"])
    return data["api_sid"], data["public_key"]


def call(
    name: str, method: str, path: str, headers: dict[str, str], body: bytes | None = None
) -> tuple[int, Any]:
    """One HTTP exchange: print it in full (redacted) and dump it to disk."""
    url = BASE_URL + path
    try:
        with urlopen(Request(url, data=body, headers=headers, method=method), timeout=60) as r:
            status, resp_headers, raw = r.status, dict(r.headers), r.read()
    except HTTPError as error:
        status, resp_headers, raw = error.code, dict(error.headers), error.read()
    try:
        parsed: Any = json.loads(raw)
    except ValueError:
        parsed = raw.decode(errors="replace")
    shown = {k: ("[redacted]" if k in REDACTED else v) for k, v in headers.items()}
    print(f"\n>>> {method} {url}")
    for key, value in shown.items():
        print(f"    {key}: {value}")
    if body is not None:
        print("    body:")
        print("\n".join("      " + line for line in body.decode(errors="replace").splitlines()))
    print(f"<<< HTTP {status}")
    for key, value in resp_headers.items():
        print(f"    {key}: {value}")
    print("    body:")
    rendered = json.dumps(parsed, indent=2, ensure_ascii=False)
    print("\n".join("      " + line for line in rendered.splitlines()))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "captured_at": datetime.now(UTC).isoformat(),
        "request": {
            "method": method,
            "url": url,
            "headers": shown,
            "body": body.decode(errors="replace") if body else None,
        },
        "response": {"status": status, "headers": resp_headers, "body": parsed},
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (OUT_DIR / f"{stamp}_{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return status, parsed


def find_chat_id(payload: Any) -> int | None:
    """Recursive scan for an integer chat id under a chat_id/id key."""
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


def pick_sender(auth: dict[str, str]) -> str:
    """First active number OWNED by this api user.

    /inbox/numbers/active lists sub-accounts' numbers too; sending from one of
    those 422s ("does not belong to you"), so filter on auth/me's user id.
    """
    _, me = call("auth_me", "POST", "/user/auth/me", auth)
    my_id = me.get("data", {}).get("id") if isinstance(me, dict) else None
    page = 1
    while page <= 15:
        _, numbers = call(
            f"active_numbers_p{page}", "GET", f"/inbox/numbers/active?page={page}", auth
        )
        data = numbers.get("data", {}) if isinstance(numbers, dict) else {}
        for row in data.get("data") or []:
            if row.get("user_id") == my_id and row.get("number"):
                return str(row["number"])
        if not data.get("next_page_url"):
            break
        page += 1
    sys.exit("No active number owned by this api user; pass --from-number.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send exactly one SMS via TextTorrent.")
    parser.add_argument("--to", required=True, help="Recipient, E.164 (+1...). No default, ever.")
    parser.add_argument("--body", required=True, help="Message text, sent verbatim.")
    parser.add_argument("--from-number", help="Sender; default: first number owned by this user")
    parser.add_argument("--secret-id", default="outreach/texttorrent")
    args = parser.parse_args()
    sid, key = credentials(args.secret_id)
    auth = {"Accept": "application/json", "X-API-SID": sid, "X-API-PUBLIC-KEY": key}

    sender = args.from_number or pick_sender(auth)
    print(f"\nsender: {sender}")

    ten_digits = re.sub(r"\D", "", args.to)[-10:]
    _, created = call(
        "chat_create",
        "POST",
        "/inbox/chat/create",
        {**auth, "Content-Type": "application/json"},
        json.dumps({"receiver_number": ten_digits, "sender_id": sender}).encode(),
    )
    chat_id = find_chat_id(created)
    if chat_id is None:  # documented 404 when the chat already exists — find it
        _, found = call("chat_search", "GET", f"/inbox?search={ten_digits}", auth)
        chat_id = find_chat_id(found)
    if chat_id is None:
        sys.exit("\nNo chat id from create or search (blacklisted?). NOT SENT — see above.")

    boundary = f"send-one-{uuid.uuid4().hex}"
    fields = {
        "message": args.body,
        "chat_id": str(chat_id),
        "from_number": sender,
        "to_number": args.to,
    }
    form = "".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in fields.items()
    )
    status, sent = call(
        "send",
        "POST",
        "/inbox/chat",
        {**auth, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        (form + f"--{boundary}--\r\n").encode(),
    )
    message_id = sent.get("data", {}).get("id") if isinstance(sent, dict) else None
    if status in (200, 201) and message_id is not None:
        print(f"\nSENT: chat_id={chat_id}, provider message id={message_id}")
        print("(message id source: integer `data.id` in the POST /inbox/chat response)")
    else:
        sys.exit(f"\nSend NOT confirmed (HTTP {status}) — see the exchange above.")


if __name__ == "__main__":
    main()
