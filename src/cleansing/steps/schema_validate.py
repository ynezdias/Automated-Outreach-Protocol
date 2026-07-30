"""Step 1 — schema validation.

Event:  ``{"bucket": str, "key": "raw/enrichment/<file>.csv"}`` (``run_id``
        optional; derived from the file name when absent, so the S3 trigger
        needs no extra plumbing).
Output: ``{"bucket", "key": "staging/pipeline/<run_id>/01_schema_valid.ndjson",
        "run_id", "rows_in", "rows_out", "rows_quarantined", "quarantine_key"}``

Rows must have a non-empty company name, at least one contact identifier, and a
timezone-aware ISO ``enriched_at``. Rejected rows go to
``raw/quarantine/<run_id>/01_schema.csv`` with a ``reason`` column.
"""

from datetime import datetime
from typing import Any

from cleansing import pipeline_io


def _reason(row: dict[str, str]) -> str | None:
    if not (row.get("company_name") or "").strip():
        return "missing_company_name"
    if not (row.get("phone") or "").strip() and not (row.get("email") or "").strip():
        return "missing_contact_identifier"
    try:
        enriched = datetime.fromisoformat(row.get("enriched_at", ""))
    except ValueError:
        return "invalid_enriched_at"
    if enriched.tzinfo is None:
        return "invalid_enriched_at"
    return None


def _run_id(key: str) -> str:
    stem = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in stem)


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    s3 = pipeline_io.get_s3()
    bucket, key = event["bucket"], event["key"]
    run_id = event.get("run_id") or _run_id(key)
    rows = pipeline_io.read_csv_rows(s3, bucket, key)
    valid: list[pipeline_io.Row] = []
    quarantined: list[dict[str, str]] = []
    for row in rows:
        reason = _reason(row)
        if reason is not None:
            quarantined.append({**row, "reason": reason})
        else:
            valid.append({**row, "last_contacted_at": row.get("last_contacted_at") or None})
    out_key = pipeline_io.step_key(run_id, "01_schema_valid.ndjson")
    pipeline_io.write_ndjson(s3, bucket, out_key, valid)
    return {
        "bucket": bucket,
        "key": out_key,
        "run_id": run_id,
        "rows_in": len(rows),
        "rows_out": len(valid),
        "rows_quarantined": len(quarantined),
        "quarantine_key": pipeline_io.write_quarantine(
            s3, bucket, run_id, "01_schema", quarantined
        ),
    }
