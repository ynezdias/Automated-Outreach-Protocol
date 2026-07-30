"""Daily byte-identity canary against the vendor's AI message rewriter (Conflict A).

TextTorrent "automatically clean[s] messages using AI" on its send path. Any
mutation of approved template text in flight defeats template immutability and
the audit trail, so even with the feature disabled account-wide we verify it
daily: a vendor can re-enable a feature in a release, and we want to know the
same day.

The canary sends a message whose odd punctuation, unusual phrasing, and line
break are exactly what a rewriter would "improve", reads the stored message
back from the API, and emits ``Outreach/TextTorrent CanaryByteIdentical``
(1 = byte-identical, 0 = mutated or unreadable). The observability stack
alarms on anything other than a daily 1. Read-back reflects the server-stored
text; carrier-path mutation is covered by the one-time handset check in the
acceptance runbook.

Event:  ``{}`` (scheduled daily); optionally ``{"nonce": "..."}`` for tests.
Env:    ``TEXTTORRENT_SECRET_ID``, ``TEXTTORRENT_CANARY_TO``; optional
        ``TEXTTORRENT_FROM_NUMBER`` (defaults to the account's first active
        number).
"""

import os
import uuid
from typing import Any

import boto3

from texttorrent.client import TextTorrentApiError, TextTorrentClient, load_credentials

METRIC_NAMESPACE = "Outreach/TextTorrent"
METRIC_NAME = "CanaryByteIdentical"

#: Deliberately rewriter-tempting: mixed case, em dash, ellipsis, doubled
#: quotes/exclamations, stray symbols, brackets, braces, and a hard line break.
CANARY_BODY_TEMPLATE = (
    "cAnArY mIxEd-case check {nonce} — do NOT ''fix'' this text… "
    'punctuation stays as sent ;: really!! ~ "quoted" [brackets] {{braces}}\n'
    "second line kept verbatim"
)


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    nonce = str(event.get("nonce") or uuid.uuid4().hex[:8])
    submitted = CANARY_BODY_TEMPLATE.format(nonce=nonce)
    creds = load_credentials(os.environ["TEXTTORRENT_SECRET_ID"], boto3.client("secretsmanager"))
    client = TextTorrentClient(creds)
    to_number = os.environ["TEXTTORRENT_CANARY_TO"]
    from_number = os.environ.get("TEXTTORRENT_FROM_NUMBER") or client.active_numbers()[0]

    chat_id = client.create_chat(to_number, from_number)
    if chat_id is None:  # conversation already exists — the normal daily case
        chat_id = client.find_chat_id(to_number)
    if chat_id is None:
        raise TextTorrentApiError(0, f"no chat id resolvable for canary recipient {to_number}")

    sent = client.send_message(chat_id, from_number, to_number, submitted)
    message_id = int(sent["id"])
    delivered = next(
        (
            str(message.get("message"))
            for message in client.chat_messages(chat_id)
            if message.get("id") == message_id
        ),
        "",  # read-back miss is a canary failure, never a pass
    )
    identical = delivered == submitted
    boto3.client("cloudwatch").put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=[
            {"MetricName": METRIC_NAME, "Value": 1.0 if identical else 0.0, "Unit": "Count"}
        ],
    )
    return {
        "identical": identical,
        "submitted": submitted,
        "delivered": delivered,
        "message_id": message_id,
        "chat_id": chat_id,
    }
