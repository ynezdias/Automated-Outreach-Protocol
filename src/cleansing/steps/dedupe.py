"""Step 4 — dedupe on the normalized key, keeping the most-recently-enriched row.

Event:  ``{"bucket": str, "key": <step 3 output>, "run_id": str}``
Output: ``{"bucket", "key": ".../04_deduped.ndjson", "run_id", "rows_in",
        "rows_out", "duplicates_removed"}``

Adds ``normalized_key`` to every surviving row (used again by step 6).
"""

from datetime import datetime
from typing import Any

from cleansing import pipeline_io
from cleansing.identity import normalized_key


def _enriched_at(row: pipeline_io.Row) -> datetime:
    return datetime.fromisoformat(row["enriched_at"])


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    s3 = pipeline_io.get_s3()
    bucket, run_id = event["bucket"], event["run_id"]
    rows = pipeline_io.read_ndjson(s3, bucket, event["key"])
    best: dict[str, pipeline_io.Row] = {}
    for row in rows:
        key = normalized_key(row["phone"], row["email"])
        incumbent = best.get(key)
        if incumbent is None or _enriched_at(row) > _enriched_at(incumbent):
            best[key] = {**row, "normalized_key": key}
    kept = list(best.values())
    out_key = pipeline_io.step_key(run_id, "04_deduped.ndjson")
    pipeline_io.write_ndjson(s3, bucket, out_key, kept)
    return {
        "bucket": bucket,
        "key": out_key,
        "run_id": run_id,
        "rows_in": len(rows),
        "rows_out": len(kept),
        "duplicates_removed": len(rows) - len(kept),
    }
