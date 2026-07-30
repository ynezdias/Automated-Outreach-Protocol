"""API Gateway handler: append one suppression event (Salesforce -> AWS mirror).

The Apex guardrails recognize an opt-out with no network dependency and write
Salesforce synchronously (Salesforce gates the next send);
``OutreachSuppressionSyncQueueable`` then mirrors the event here (ADR-020) so
the lake-side list stays inside the 60-second bound (ADR-009). Auth is IAM
(SigV4) enforced at the API Gateway method — the caller signs via the
``AWS_Suppression`` Named Credential.

Request:  ``POST /suppressions`` with
          ``{"identifier", "reason", "source", "occurred_at"}`` (ISO-8601,
          timezone-aware).
Response: 200 ``{"ok": true}``; 400 with the validation error on malformed
          input — the caller retries and terminally flags for human review, so
          a bad event is never silently dropped.
"""

import json
import os
from datetime import datetime
from typing import Any

from suppression import SuppressionStore


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    try:
        payload = json.loads(event.get("body") or "{}")
        occurred_at = datetime.fromisoformat(payload["occurred_at"])
        if occurred_at.tzinfo is None:
            raise ValueError("occurred_at must carry a timezone offset")
        SuppressionStore(os.environ["DATA_BUCKET"]).add_suppression(
            payload["identifier"],
            reason=payload["reason"],
            source=payload["source"],
            occurred_at=occurred_at,
        )
    except (KeyError, ValueError) as error:
        return {"statusCode": 400, "body": json.dumps({"error": str(error)})}
    return {"statusCode": 200, "body": json.dumps({"ok": True})}
