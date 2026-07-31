"""Manual, human-in-the-loop TextTorrent conversation harness. NOT automation.

Every outbound message requires an explicit 's' + Enter at the gate — there is
no flag, environment variable, or code path that sends without it, and EOF or
Ctrl-C at any prompt aborts WITHOUT sending. No Salesforce, no scheduling.

Loop: send opener (gated) -> poll for a reply -> print it raw -> run
src/guardrails/ -> run the TAXONOMY.md pattern-bucket heuristic -> propose the
matching template with segment count + encoding -> gate -> repeat.

    uv run python research/conversation_harness.py --to +1XXXXXXXXXX
        [--body "opener text"] [--from-number +1YYYYYYYYYY]
        [--poll-seconds 10] [--field first_name=Ana]

Credentials: env TT_API_SID / TT_API_PUBLIC_KEY, else `aws secretsmanager`
CLI, else boto3. Transcript + raw wire dumps go to research/output/
(gitignored — real phone numbers and message content, never committed).

Caveats, deliberately accepted for a manual research tool:
- Templates here are HARNESS-LOCAL DRAFTS. The approved Reply_Template__c set
  lives in Salesforce, which this tool must not touch. Nothing here is an
  approved template; that is printed at every proposal.
- The intent step is the TAXONOMY.md sizing heuristic (pattern buckets), not
  the trained classifier — it does not exist yet.
- Unedited template sends are recorded as auto-replies for the guardrail
  thread state, so the loop breaker fires after 2 of them, matching how the
  production flow would count. Edited sends count as human messages.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import send_one

from classifier.buckets import bucket_intent
from guardrails import (
    PROCEED,
    SUPPRESS_AND_STOP,
    Message,
    evaluate,
)

BASE_URL = "https://api.texttorrent.com/api/v1"
OUT_DIR = Path(__file__).resolve().parent / "output"
REDACTED = ("X-API-SID", "X-API-PUBLIC-KEY")
FIRST_TOUCH = "Hi — quick question about financing for your business."

# Harness-local DRAFT templates (per-intent; TAXONOMY.md §5 eligible set only).
# The approved, versioned Reply_Template__c records live in Salesforce.
TEMPLATES = {
    "Interested": (
        "Great{first_name_greeting} — to point you to the right option: how much "
        "capital are you looking for, and roughly what is your monthly revenue?"
    ),
    "Amount_Given": (
        "Got it, thanks. To match you with the right program: about how long in "
        "business, and what is the average monthly revenue?"
    ),
    "Request_More_Info": (
        "Happy to send that over{first_name_greeting}. What would be most useful "
        "first: terms, rates, or the application steps?"
    ),
    "Not_Interested": (
        "Understood — thanks for letting us know, and we won't follow up further. "
        "If timing ever changes, this number reaches us."
    ),
    "Wrong_Person": (
        "Apologies for the mix-up — we'll remove this number from our list. "
        "Thanks for letting us know."
    ),
}

GSM7_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENSION = "^{}\\[~]|€\f"


def sms_encoding(text: str) -> tuple[str, int, int]:
    """Returns (encoding, unit count, segment count) for an SMS body."""
    basic, extension = set(GSM7_BASIC), set(GSM7_EXTENSION)
    if all(c in basic or c in extension for c in text):
        septets = sum(2 if c in extension else 1 for c in text)
        return "GSM-7", septets, 1 if septets <= 160 else math.ceil(septets / 153)
    units = sum(2 if ord(c) > 0xFFFF else 1 for c in text)
    return "UCS-2", units, 1 if units <= 70 else math.ceil(units / 67)


def credentials(secret_id: str) -> tuple[str, str]:
    """Env vars, else AWS CLI (works on this machine), else boto3."""
    sid, key = os.environ.get("TT_API_SID"), os.environ.get("TT_API_PUBLIC_KEY")
    if sid and key:
        return sid, key
    try:
        raw = subprocess.run(
            [
                "aws",
                "secretsmanager",
                "get-secret-value",
                "--secret-id",
                secret_id,
                "--region",
                os.environ.get("AWS_REGION", "us-east-2"),
                "--query",
                "SecretString",
                "--output",
                "text",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        data = json.loads(raw)
        return data["api_sid"], data["public_key"]
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError):
        return send_one.credentials(secret_id)


class Transcript:
    def __init__(self) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.path = OUT_DIR / f"conversation_{stamp}.jsonl"

    def log(self, event: str, **data: Any) -> None:
        record = {"ts": datetime.now(UTC).isoformat(), "event": event, **data}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def wire(
    auth: dict[str, str],
    method: str,
    path: str,
    body: bytes | None = None,
    content_type: str | None = None,
    verbose: bool = False,
) -> tuple[int, Any]:
    headers = dict(auth)
    if content_type:
        headers["Content-Type"] = content_type
    url = BASE_URL + path
    try:
        with urlopen(Request(url, data=body, headers=headers, method=method), timeout=60) as r:
            status, raw = r.status, r.read()
    except HTTPError as error:
        status, raw = error.code, error.read()
    try:
        parsed: Any = json.loads(raw)
    except ValueError:
        parsed = raw.decode(errors="replace")
    if verbose:
        shown = {k: ("[redacted]" if k in REDACTED else v) for k, v in headers.items()}
        print(f"\n>>> {method} {url}")
        for key, value in shown.items():
            print(f"    {key}: {value}")
        if body is not None:
            print("    body:")
            print("\n".join("      " + ln for ln in body.decode(errors="replace").splitlines()))
        print(f"<<< HTTP {status}")
        print(
            "\n".join(
                "      " + ln
                for ln in json.dumps(parsed, indent=2, ensure_ascii=False).splitlines()
            )
        )
    return status, parsed


def multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"harness-{uuid.uuid4().hex}"
    form = "".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in fields.items()
    )
    return (form + f"--{boundary}--\r\n").encode(), f"multipart/form-data; boundary={boundary}"


def abort_without_sending(transcript: Transcript) -> None:
    print("\nInput closed — aborting WITHOUT sending. Transcript:", transcript.path)
    transcript.log("aborted", reason="input closed at gate")
    sys.exit(1)


def show_proposal(text: str, label: str) -> None:
    encoding, units, segments = sms_encoding(text)
    print(f"\n--- {label} ---")
    print(f"  {text!r}")
    print(f"  encoding={encoding}  units={units}  segments={segments}")


def gate(
    text: str | None, transcript: Transcript, allow_send: bool = True
) -> tuple[str, bool] | None:
    """The keypress gate. Returns (final_text, was_edited) only on explicit 's'.

    Returns None on skip. EOF/Ctrl-C aborts the program without sending.
    Nothing else sends — there is deliberately no default action.
    """
    edited = False
    options = "[s]end / [e]dit / [k]skip / [q]uit" if allow_send else "[k]skip / [q]uit"
    while True:
        try:
            choice = input(f"{options} > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            abort_without_sending(transcript)
        if choice == "s" and allow_send and text is not None:
            return text, edited
        if choice == "e" and allow_send:
            try:
                text = input("new text > ").rstrip("\n")
            except (EOFError, KeyboardInterrupt):
                abort_without_sending(transcript)
            edited = True
            show_proposal(text, "edited proposal")
            continue
        if choice == "k":
            transcript.log("skipped", proposed=text)
            return None
        if choice == "q":
            print("Quitting. Transcript:", transcript.path)
            transcript.log("quit")
            sys.exit(0)
        print(f"nothing sends without an explicit 's' — {options}")


def send_gated(
    auth: dict[str, str],
    transcript: Transcript,
    chat_id: int,
    sender: str,
    to: str,
    proposal: str | None,
    label: str,
    pending_verify: dict[int, str],
) -> tuple[str, bool] | None:
    """Propose -> gate -> send. The ONLY call site of the send endpoint."""
    if proposal is not None:
        show_proposal(proposal, label)
    decision = gate(proposal, transcript)
    if decision is None:
        return None
    text, edited = decision
    body, content_type = multipart(
        {"message": text, "chat_id": str(chat_id), "from_number": sender, "to_number": to}
    )
    status, parsed = wire(auth, "POST", "/inbox/chat", body, content_type, verbose=True)
    data = parsed.get("data", {}) if isinstance(parsed, dict) else {}
    message_id = data.get("id")
    transcript.log(
        "sent",
        text=text,
        edited=edited,
        http_status=status,
        message_id=message_id,
        response=parsed if isinstance(parsed, dict) else str(parsed),
    )
    if status not in (200, 201) or message_id is None:
        print(f"!! send NOT confirmed (HTTP {status}) — see above")
        return None
    stored = data.get("message")
    if stored is not None and stored != text:
        print(
            "!! REWRITE WARNING: the API stored different text than we sent "
            "(vendor AI rewriter? Conflict A) — see transcript"
        )
        transcript.log("rewrite_warning", message_id=message_id, stored=stored, sent=text)
    pending_verify[message_id] = text  # re-checked against the next poll snapshot
    print(f"sent: provider message id {message_id}")
    return text, edited


def merge_fields(template: str, fields: dict[str, str]) -> str:
    greeting = f", {fields['first_name']}" if fields.get("first_name") else ""
    merged = template.format_map({**fields, "first_name_greeting": greeting})
    return re.sub(r"\s{2,}", " ", merged).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", required=True, help="Recipient, E.164. No default, ever.")
    parser.add_argument("--body", default=FIRST_TOUCH, help="Opener text (gated like all sends)")
    parser.add_argument("--from-number", help="Sender; default: first number owned by this user")
    parser.add_argument("--secret-id", default="outreach/texttorrent")
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Template merge field, repeatable (e.g. first_name=Ana)",
    )
    args = parser.parse_args()
    fields = dict(item.split("=", 1) for item in args.field)

    transcript = Transcript()
    print("Transcript:", transcript.path)
    sid, key = credentials(args.secret_id)
    auth = {"Accept": "application/json", "X-API-SID": sid, "X-API-PUBLIC-KEY": key}
    sender = args.from_number or send_one.pick_sender(auth)
    print(f"sender: {sender}   recipient: {args.to}")

    ten_digits = re.sub(r"\D", "", args.to)[-10:]
    _, created = wire(
        auth,
        "POST",
        "/inbox/chat/create",
        json.dumps({"receiver_number": ten_digits, "sender_id": sender}).encode(),
        "application/json",
        verbose=True,
    )
    chat_id = send_one.find_chat_id(created)
    if chat_id is None:
        _, found = wire(auth, "GET", f"/inbox?search={ten_digits}", verbose=True)
        chat_id = send_one.find_chat_id(found)
    if chat_id is None:
        sys.exit("No chat id from create or search (blacklisted?). Nothing sent.")
    transcript.log("chat", chat_id=chat_id, sender=sender, to=args.to)

    # Baseline: everything already in the chat is history, not a new reply.
    seen: set[int] = set()
    pending_verify: dict[int, str] = {}
    _, snapshot = wire(auth, "GET", f"/inbox/{chat_id}?limit=20")
    if isinstance(snapshot, dict):
        for message in snapshot.get("data", {}).get("messages", {}).get("data") or []:
            seen.add(message["id"])

    thread: list[Message] = []
    outcome = send_gated(
        auth, transcript, chat_id, sender, args.to, args.body, "opener proposal", pending_verify
    )
    if outcome is not None:
        thread.append(Message(outcome[0], "outbound", is_auto_reply=False))

    print(f"\npolling every {args.poll_seconds}s — Ctrl-C to quit")
    while True:
        try:
            time.sleep(args.poll_seconds)
            status, payload = wire(auth, "GET", f"/inbox/{chat_id}?limit=20")
        except KeyboardInterrupt:
            print("\nDone. Transcript:", transcript.path)
            transcript.log("quit")
            return
        if not isinstance(payload, dict) or status != 200:
            print(f"\n!! poll error HTTP {status}; retrying")
            transcript.log("poll_error", http_status=status)
            continue
        messages = payload.get("data", {}).get("messages", {}).get("data") or []
        for message in sorted(messages, key=lambda m: m["id"]):
            stored_id, stored_text = message["id"], message.get("message") or ""
            # Conflict A check on our own sends: pop() only runs when the key exists.
            if stored_id in pending_verify and stored_text != pending_verify.pop(stored_id):
                print(
                    f"\n!! REWRITE WARNING: stored text of {stored_id} differs from "
                    "what we sent (vendor AI rewriter?) — see transcript"
                )
                transcript.log("rewrite_warning", message_id=stored_id, stored=stored_text)
            if stored_id in seen or message.get("direction") != "inbound":
                seen.add(stored_id)
                continue
            seen.add(stored_id)

            print("\n=== inbound reply (raw) ===")
            print(json.dumps(message, indent=2, ensure_ascii=False))
            transcript.log("inbound", message=message)
            thread_before = list(thread)
            thread.append(Message(stored_text, "inbound"))

            verdict = evaluate(stored_text, thread_before, "sms")
            print(
                f"guardrail: action={verdict.action}  rule={verdict.rule}  "
                f"trigger={verdict.trigger!r}"
            )
            intent, evidence = bucket_intent(stored_text, verdict)
            print(
                f"intent (TAXONOMY.md heuristic buckets, not the trained model): "
                f"{intent}  [{evidence}]"
            )
            transcript.log(
                "verdict",
                action=verdict.action,
                rule=verdict.rule,
                trigger=verdict.trigger,
                intent=intent,
                evidence=evidence,
            )

            if verdict.action == SUPPRESS_AND_STOP:
                print(
                    "OPT-OUT: no reply may be sent, ever (carrier confirms STOP). "
                    "Suppress this number in Salesforce + TextTorrent manually."
                )
                gate(None, transcript, allow_send=False)
                continue
            proposal = None
            if verdict.action == PROCEED and intent in TEMPLATES:
                proposal = merge_fields(TEMPLATES[intent], fields)
                label = f"DRAFT template for {intent} (harness-local, NOT an approved version)"
            elif verdict.action == PROCEED:
                label = f"{intent} is not auto-reply eligible (TAXONOMY.md §5) — [e] to compose"
            else:
                label = (
                    f"human_review ({verdict.rule}) — production would queue this; "
                    "[e] to compose a manual reply"
                )
            if proposal is None:
                print(f"\n--- {label} ---")
            outcome = send_gated(
                auth,
                transcript,
                chat_id,
                sender,
                args.to,
                proposal,
                label if proposal else "manual reply",
                pending_verify,
            )
            if outcome is not None:
                text, edited = outcome
                thread.append(Message(text, "outbound", is_auto_reply=not edited))


if __name__ == "__main__":
    main()
