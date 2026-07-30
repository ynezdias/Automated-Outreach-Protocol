"""Step 2 — normalization via the shared ``cleansing.normalize`` module.

Event:  ``{"bucket": str, "key": <step 1 output>, "run_id": str}``
Output: ``{"bucket", "key": ".../02_normalized.ndjson", "run_id", "rows_in",
        "rows_out", "rows_quarantined", "quarantine_key"}``

Phones become E.164, emails are canonicalized, company names are folded and
whitespace-collapsed. Rows left without any valid identifier (or an empty
company after folding) go to ``raw/quarantine/<run_id>/02_normalize.csv``.
"""

from typing import Any

from cleansing import pipeline_io
from cleansing.normalize import normalize_company, normalize_email, normalize_phone


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    s3 = pipeline_io.get_s3()
    bucket, run_id = event["bucket"], event["run_id"]
    rows = pipeline_io.read_ndjson(s3, bucket, event["key"])
    kept: list[pipeline_io.Row] = []
    quarantined: list[dict[str, str]] = []
    for row in rows:
        company = normalize_company(row["company_name"])
        if company is None:
            quarantined.append({**row, "reason": "company_empty_after_normalization"})
            continue
        phone = normalize_phone(row["phone"])
        email = normalize_email(row["email"])
        if phone is None and email is None:
            quarantined.append({**row, "reason": "no_valid_identifier"})
            continue
        kept.append({**row, "company_name": company, "phone": phone, "email": email})
    out_key = pipeline_io.step_key(run_id, "02_normalized.ndjson")
    pipeline_io.write_ndjson(s3, bucket, out_key, kept)
    return {
        "bucket": bucket,
        "key": out_key,
        "run_id": run_id,
        "rows_in": len(rows),
        "rows_out": len(kept),
        "rows_quarantined": len(quarantined),
        "quarantine_key": pipeline_io.write_quarantine(
            s3, bucket, run_id, "02_normalize", quarantined
        ),
    }
