"""Step 3 — deliverability validation.

Event:  ``{"bucket": str, "key": <step 2 output>, "run_id": str}``
Output: ``{"bucket", "key": ".../03_validated.ndjson", "run_id", "rows_in",
        "rows_out", "rows_quarantined", "quarantine_key"}``

Emails whose domain has no MX record are nulled; if that leaves the row with no
identifier it is quarantined (``raw/quarantine/<run_id>/03_validate.csv``).

Twilio Lookup (line type) costs $0.01 per query, so it is gated behind the
``TWILIO_LOOKUP_ENABLED`` env flag and DISABLED by default. When enabled it
only annotates ``line_type`` — it never drops rows; what to do with landlines
is a human decision downstream. ``mx_check`` and ``line_type_lookup`` are
module-level hooks so tests (and reruns) can inject alternatives.
"""

import base64
import json
import os
import urllib.request
from typing import Any

import dns.resolver

from cleansing import pipeline_io


def _default_mx_check(domain: str) -> bool:  # pragma: no cover - network I/O
    try:
        return len(dns.resolver.resolve(domain, "MX")) > 0
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.LifetimeTimeout):
        return False


def _twilio_line_type(phone: str) -> str | None:  # pragma: no cover - paid network I/O
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    request = urllib.request.Request(
        f"https://lookups.twilio.com/v2/PhoneNumbers/{phone}?Fields=line_type_intelligence",
        headers={"Authorization": f"Basic {auth}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    line_type = payload.get("line_type_intelligence", {}).get("type")
    return str(line_type) if line_type is not None else None


mx_check = _default_mx_check
line_type_lookup = _twilio_line_type


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    s3 = pipeline_io.get_s3()
    bucket, run_id = event["bucket"], event["run_id"]
    rows = pipeline_io.read_ndjson(s3, bucket, event["key"])
    lookup_enabled = os.environ.get("TWILIO_LOOKUP_ENABLED", "false").lower() == "true"
    mx_cache: dict[str, bool] = {}
    kept: list[pipeline_io.Row] = []
    quarantined: list[dict[str, str]] = []
    for row in rows:
        email = row["email"]
        if email is not None:
            domain = email.rpartition("@")[2]
            if domain not in mx_cache:
                mx_cache[domain] = mx_check(domain)
            if not mx_cache[domain]:
                if row["phone"] is None:
                    quarantined.append({**row, "reason": "email_domain_has_no_mx"})
                    continue
                row = {**row, "email": None}
        line_type = (
            line_type_lookup(row["phone"])
            if lookup_enabled and row["phone"] is not None
            else None
        )
        kept.append({**row, "line_type": line_type})
    out_key = pipeline_io.step_key(run_id, "03_validated.ndjson")
    pipeline_io.write_ndjson(s3, bucket, out_key, kept)
    return {
        "bucket": bucket,
        "key": out_key,
        "run_id": run_id,
        "rows_in": len(rows),
        "rows_out": len(kept),
        "rows_quarantined": len(quarantined),
        "quarantine_key": pipeline_io.write_quarantine(
            s3, bucket, run_id, "03_validate", quarantined
        ),
    }
