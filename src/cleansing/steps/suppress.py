"""Step 5 — suppression and contact cooldown.

Event:  ``{"bucket": str, "key": <step 4 output>, "run_id": str,
        "as_of"?: ISO datetime}`` (``as_of`` defaults to now; pass it to make
        reruns reproducible).
Env:    ``COOLDOWN_DAYS`` (default 90).
Output: ``{"bucket", "key": ".../05_suppressed.ndjson", "run_id", "rows_in",
        "rows_out", "suppressed", "cooldown_excluded", "excluded_key"}``

Drops every contact the suppression list matches (cross-channel, via
``src/suppression/``) and everyone contacted within COOLDOWN_DAYS. Excluded
rows are written to ``.../05_excluded.ndjson`` with an ``excluded_reason``
column for auditability.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from cleansing import pipeline_io
from suppression import SuppressionStore


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    s3 = pipeline_io.get_s3()
    bucket, run_id = event["bucket"], event["run_id"]
    rows = pipeline_io.read_ndjson(s3, bucket, event["key"])
    store = SuppressionStore(bucket=bucket, s3_client=s3)
    as_of = datetime.fromisoformat(event["as_of"]) if "as_of" in event else datetime.now(UTC)
    window = timedelta(days=int(os.environ.get("COOLDOWN_DAYS", "90")))
    kept: list[pipeline_io.Row] = []
    excluded: list[pipeline_io.Row] = []
    for row in rows:
        if store.is_suppressed(phone=row["phone"], email=row["email"]).suppressed:
            excluded.append({**row, "excluded_reason": "suppressed"})
            continue
        last_contacted = row["last_contacted_at"]
        if last_contacted is not None and as_of - datetime.fromisoformat(last_contacted) < window:
            excluded.append({**row, "excluded_reason": "cooldown"})
            continue
        kept.append(row)
    out_key = pipeline_io.step_key(run_id, "05_suppressed.ndjson")
    excluded_key = pipeline_io.step_key(run_id, "05_excluded.ndjson")
    pipeline_io.write_ndjson(s3, bucket, out_key, kept)
    pipeline_io.write_ndjson(s3, bucket, excluded_key, excluded)
    suppressed_count = sum(1 for r in excluded if r["excluded_reason"] == "suppressed")
    return {
        "bucket": bucket,
        "key": out_key,
        "run_id": run_id,
        "rows_in": len(rows),
        "rows_out": len(kept),
        "suppressed": suppressed_count,
        "cooldown_excluded": len(excluded) - suppressed_count,
        "excluded_key": excluded_key,
    }
