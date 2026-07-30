"""Step 7 — write the cleansed batch as Parquet to staging/cleansed/.

Event:  ``{"bucket": str, "key": <step 6 output>, "run_id": str}``
Output: ``{"bucket", "key": "staging/cleansed/<run_id>.parquet", "run_id",
        "rows_in", "rows_out"}``
"""

import io
from typing import Any

import polars as pl

from cleansing import pipeline_io

COLUMNS = [
    "external_id",
    "normalized_key",
    "company_name",
    "contact_name",
    "phone",
    "email",
    "line_type",
    "enriched_at",
    "last_contacted_at",
]


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    s3 = pipeline_io.get_s3()
    bucket, run_id = event["bucket"], event["run_id"]
    rows = pipeline_io.read_ndjson(s3, bucket, event["key"])
    frame = pl.DataFrame(
        {column: [row.get(column) for row in rows] for column in COLUMNS},
        schema=dict.fromkeys(COLUMNS, pl.String),
    ).with_columns(pl.col("enriched_at").str.to_datetime(time_zone="UTC"))
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    out_key = f"staging/cleansed/{run_id}.parquet"
    s3.put_object(Bucket=bucket, Key=out_key, Body=buffer.getvalue())
    return {
        "bucket": bucket,
        "key": out_key,
        "run_id": run_id,
        "rows_in": len(rows),
        "rows_out": frame.height,
    }
