"""S3 and serialization helpers shared by the cleansing pipeline steps.

Steps exchange newline-delimited JSON under ``staging/pipeline/<run_id>/`` so
every intermediate is inspectable and any step can be rerun in isolation.
Quarantine files are CSV (matching the raw input format) with an added
``reason`` column. The final output is Parquet.
"""

import csv
import io
import json
from typing import Any

import boto3

Row = dict[str, Any]


def get_s3() -> Any:
    return boto3.client("s3")


def read_csv_rows(s3: Any, bucket: str, key: str) -> list[dict[str, str]]:
    text = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def write_csv(s3: Any, bucket: str, key: str, rows: list[dict[str, str]]) -> None:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    s3.put_object(Bucket=bucket, Key=key, Body=out.getvalue().encode(), ContentType="text/csv")


def read_ndjson(s3: Any, bucket: str, key: str) -> list[Row]:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode()
    return [json.loads(line) for line in body.splitlines() if line]


def write_ndjson(s3: Any, bucket: str, key: str, rows: list[Row]) -> None:
    body = "".join(json.dumps(row) + "\n" for row in rows)
    s3.put_object(
        Bucket=bucket, Key=key, Body=body.encode(), ContentType="application/x-ndjson"
    )


def step_key(run_id: str, name: str) -> str:
    return f"staging/pipeline/{run_id}/{name}"


def quarantine_key(run_id: str, step: str) -> str:
    return f"raw/quarantine/{run_id}/{step}.csv"


def write_quarantine(
    s3: Any, bucket: str, run_id: str, step: str, rows: list[dict[str, str]]
) -> str | None:
    """Write rejected rows (with their ``reason`` column) as CSV; None if empty."""
    if not rows:
        return None
    key = quarantine_key(run_id, step)
    write_csv(s3, bucket, key, rows)
    return key
