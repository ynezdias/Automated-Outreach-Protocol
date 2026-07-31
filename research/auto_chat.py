"""Local auto-chat runner for TEST conversations with explicitly listed numbers.

At the owner's explicit instruction (2026-07-31 session) this runs UNATTENDED
— it overrides the conversation harness's keypress gate for numbers passed on
the command line, and ONLY those. It is a test tool, not the production reply
engine: replies come from a fixed canned map per intent, capped per thread.

Production semantics still hold where they are compliance rules:
- Guardrail-terminal verdicts never get a reply: opt-out (STOP) halts the
  thread instantly and permanently; legal/hostility halt it; bounce/auto-reply
  gets silence.
- After 2 auto-replies the log records that production's loop breaker would
  have forced human takeover (the runner keeps going, capped, because the
  recipient is a consenting test device).

Usage:
    uv run python research/auto_chat.py --to +1XXXXXXXXXX [--to +1YYYYYYYYYY]
        [--from-number +1ZZZZZZZZZZ] [--poll-seconds 15] [--max-replies 10]
        [--catch-up]

Transcript: research/output/auto_chat_<stamp>.jsonl (gitignored).
"""

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import send_one
from conversation_harness import credentials, multipart, wire

from classifier.buckets import bucket_intent
from guardrails import evaluate

OUT_DIR = Path(__file__).resolve().parent / "output"
HALT_RULES = {"opt_out", "legal_escalation", "hostility"}

OPENER = (
    "Hi, this is Maria with FundMate. If extra capital were on the table for "
    "your business, how much would actually help? Reply STOP to opt out."
)
CANNED = {
    "Interested": (
        "Great - to point you to the right option: how much capital are you "
        "looking for, and roughly what is your monthly revenue?"
    ),
    "Amount_Given": (
        "Got it, thanks. Two quick ones so I point you right: how long in "
        "business, and roughly what monthly revenue?"
    ),
    "Request_More_Info": (
        "Happy to send that over. What would be most useful first: terms, "
        "rates, or the application steps?"
    ),
    "Question": (
        "Good question. Short version: we arrange working capital for small "
        "businesses - term loans and lines of credit. What else can I answer?"
    ),
    "Call_Request": "Can do - what number and time work best for a call?",
    "Process_Update": "Thanks for the update - I will take a look and follow up shortly.",
    "Not_Interested": (
        "Understood - thanks for letting us know, and we will not follow up "
        "further. If timing ever changes, this number reaches us."
    ),
    "Wrong_Person": (
        "Apologies for the mix-up - we will remove this number from our list. "
        "Thanks for letting us know."
    ),
    "Unclear": (
        "Just to make sure I follow - are you looking into working capital "
        "for the business right now?"
    ),
}
CLOSING_INTENTS = {"Not_Interested", "Wrong_Person"}


class Thread:
    def __init__(self, number: str) -> None:
        self.number = number
        self.chat_id: int | None = None
        self.last_processed = 0
        self.auto_count = 0
        self.halted = False
        self.last_reply: str | None = None
        self.clarified = False  # the Unclear clarifier fires at most once per thread


def log(path: Path, event: str, **data: object) -> None:
    record = {"ts": datetime.now(UTC).isoformat(), "event": event, **data}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False), flush=True)


def ensure_chat(auth: dict[str, str], sender: str, number: str) -> int | None:
    ten = re.sub(r"\D", "", number)[-10:]
    _, created = wire(
        auth,
        "POST",
        "/inbox/chat/create",
        json.dumps({"receiver_number": ten, "sender_id": sender}).encode(),
        "application/json",
    )
    chat_id = send_one.find_chat_id(created)
    if chat_id is None:
        _, found = wire(auth, "GET", f"/inbox?search={ten}")
        chat_id = send_one.find_chat_id(found)
    return chat_id


def messages_for(auth: dict[str, str], chat_id: int) -> list[dict]:
    status, payload = wire(auth, "GET", f"/inbox/{chat_id}?limit=20")
    if status != 200 or not isinstance(payload, dict):
        return []
    return sorted(
        (payload.get("data", {}).get("messages", {}) or {}).get("data") or [],
        key=lambda m: m["id"],
    )


def send(auth: dict[str, str], thread: Thread, sender: str, text: str) -> int | None:
    body, content_type = multipart(
        {
            "message": text,
            "chat_id": str(thread.chat_id),
            "from_number": sender,
            "to_number": thread.number,
        }
    )
    status, payload = wire(auth, "POST", "/inbox/chat", body, content_type)
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    return data.get("id") if status in (200, 201) else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", action="append", required=True, help="Authorized test numbers ONLY")
    parser.add_argument("--from-number", help="Sender; default: first owned active number")
    parser.add_argument("--secret-id", default="outreach/texttorrent")
    parser.add_argument("--poll-seconds", type=int, default=15)
    parser.add_argument("--max-replies", type=int, default=10)
    parser.add_argument(
        "--catch-up",
        action="store_true",
        help="Also answer inbound messages that predate startup (else history is ignored)",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    transcript = OUT_DIR / f"auto_chat_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    sid, key = credentials(args.secret_id)
    auth = {"Accept": "application/json", "X-API-SID": sid, "X-API-PUBLIC-KEY": key}
    sender = args.from_number or send_one.pick_sender(auth)
    log(transcript, "start", sender=sender, numbers=args.to, catch_up=args.catch_up)

    threads = [Thread(number) for number in args.to]
    for thread in threads:
        thread.chat_id = ensure_chat(auth, sender, thread.number)
        if thread.chat_id is None:
            thread.halted = True
            log(transcript, "no_chat", number=thread.number, note="blacklisted or create failed")
            continue
        history = messages_for(auth, thread.chat_id)
        outbound_ids = [m["id"] for m in history if m["direction"] == "outbound"]
        all_ids = [m["id"] for m in history]
        thread.last_processed = (
            max(outbound_ids, default=0) if args.catch_up else max(all_ids, default=0)
        )
        if not outbound_ids:
            message_id = send(auth, thread, sender, OPENER)
            log(transcript, "opener_sent", number=thread.number, message_id=message_id)
        log(
            transcript,
            "thread_ready",
            number=thread.number,
            chat_id=thread.chat_id,
            baseline=thread.last_processed,
        )

    try:
        while True:
            for thread in threads:
                if thread.halted or thread.chat_id is None:
                    continue
                try:
                    process_thread(auth, transcript, thread, sender, args.max_replies)
                except Exception as error:  # transient network etc: log, retry next cycle
                    log(
                        transcript,
                        "cycle_error",
                        number=thread.number,
                        error=type(error).__name__,
                        detail=str(error)[:200],
                    )
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        log(transcript, "stopped", reason="keyboard interrupt")


def process_thread(
    auth: dict[str, str], transcript: Path, thread: Thread, sender: str, max_replies: int
) -> None:
    new_inbound = [
        m
        for m in messages_for(auth, thread.chat_id)
        if m["direction"] == "inbound" and m["id"] > thread.last_processed
    ]
    if not new_inbound:
        return
    latest = new_inbound[-1]
    thread.last_processed = latest["id"]
    text = latest.get("message") or ""
    verdict = evaluate(text, [], "sms")
    intent, evidence = bucket_intent(text, verdict)
    log(
        transcript,
        "inbound",
        number=thread.number,
        message_id=latest["id"],
        body=text,
        action=verdict.action,
        rule=verdict.rule,
        intent=intent,
        evidence=evidence,
    )
    if verdict.rule in HALT_RULES:
        thread.halted = True
        log(
            transcript,
            "thread_halted",
            number=thread.number,
            rule=verdict.rule,
            note="no reply, ever (production suppresses/queues)",
        )
        return
    if verdict.action != "proceed":
        log(transcript, "no_reply", number=thread.number, rule=verdict.rule)
        return
    if thread.auto_count >= max_replies:
        log(transcript, "cap_reached", number=thread.number)
        return
    if intent == "Unclear" and thread.clarified:
        log(
            transcript,
            "no_reply",
            number=thread.number,
            note="unclear again after one clarifier; production queues for a human",
        )
        return
    reply = CANNED.get(intent, CANNED["Unclear"])
    if reply == thread.last_reply:
        log(
            transcript,
            "no_reply",
            number=thread.number,
            note="would repeat the previous line verbatim; staying quiet instead",
        )
        return
    message_id = send(auth, thread, sender, reply)
    thread.auto_count += 1
    thread.last_reply = reply
    if intent == "Unclear":
        thread.clarified = True
    log(
        transcript,
        "reply_sent",
        number=thread.number,
        message_id=message_id,
        intent=intent,
        body=reply,
        auto_count=thread.auto_count,
    )
    if thread.auto_count == 2:
        log(
            transcript,
            "loop_breaker_note",
            number=thread.number,
            note="production would force human takeover from here",
        )
    if intent in CLOSING_INTENTS:
        thread.halted = True
        log(transcript, "thread_closed", number=thread.number, intent=intent)


if __name__ == "__main__":
    main()
