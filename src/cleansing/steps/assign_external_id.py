"""Step 6 — assign the deterministic UUIDv5 external ID.

Event:  ``{"bucket": str, "key": <step 5 output>, "run_id": str}``
Output: ``{"bucket", "key": ".../06_with_ids.ndjson", "run_id", "rows_in",
        "rows_out"}``

The ID is UUIDv5 over the normalized key (see ``cleansing.identity``), so the
same contact always maps to the same Salesforce external ID across runs.
"""

from typing import Any

from cleansing import pipeline_io
from cleansing.identity import external_id


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    s3 = pipeline_io.get_s3()
    bucket, run_id = event["bucket"], event["run_id"]
    rows = pipeline_io.read_ndjson(s3, bucket, event["key"])
    for row in rows:
        row["external_id"] = external_id(row["normalized_key"])
    out_key = pipeline_io.step_key(run_id, "06_with_ids.ndjson")
    pipeline_io.write_ndjson(s3, bucket, out_key, rows)
    return {
        "bucket": bucket,
        "key": out_key,
        "run_id": run_id,
        "rows_in": len(rows),
        "rows_out": len(rows),
    }
