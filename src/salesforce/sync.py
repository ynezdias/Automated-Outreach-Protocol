"""Sync Lambda: push a cleansed batch to Salesforce via Bulk API 2.0 upsert.

Event:  ``{"bucket": str, "key": "staging/cleansed/<run_id>.parquet",
        "run_id": str}`` — exactly the write_staging step's output, so this
        runs as the final Step Functions state or standalone.
Env:    ``SALESFORCE_SECRET_ID`` — Secrets Manager secret with the JWT creds.
Output: ``{"bucket", "run_id", "records_in", "records_failed", "jobs",
        "failure_keys"}``

Per-record failures are written verbatim (with the Salesforce error message)
to ``staging/sync-failures/<run_id>/<job_id>.csv`` — never silently dropped.
API limit consumption and record counts go to CloudWatch (Outreach/Sync).
"""

import io
import os
from typing import Any

import boto3
import polars as pl

from cleansing import pipeline_io
from salesforce.auth import get_access_token, load_credentials
from salesforce.bulk import BulkApiClient, JobResult, upsert_records

BATCH_SIZE = 10_000
EXTERNAL_ID_FIELD = "Outreach_External_Id__c"
METRIC_NAMESPACE = "Outreach/Sync"

#: parquet column -> Lead field
FIELD_MAP = {
    "external_id": "Outreach_External_Id__c",
    "company_name": "Company",
    "contact_name": "LastName",
    "phone": "Phone",
    "email": "Email",
}


def lead_records(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    records = []
    for row in rows:
        record = {sf_field: str(row.get(column) or "") for column, sf_field in FIELD_MAP.items()}
        if not record["LastName"]:
            record["LastName"] = "Unknown"  # LastName is required on Lead
        records.append(record)
    return records


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    s3 = pipeline_io.get_s3()
    bucket, run_id = event["bucket"], event["run_id"]
    body = s3.get_object(Bucket=bucket, Key=event["key"])["Body"].read()
    records = lead_records(pl.read_parquet(io.BytesIO(body)).to_dicts())
    results: list[JobResult] = []
    failure_keys: list[str] = []
    if records:
        creds = load_credentials(os.environ["SALESFORCE_SECRET_ID"], boto3.client("secretsmanager"))
        access_token, instance_url = get_access_token(creds)
        client = BulkApiClient(instance_url, access_token)
        results = upsert_records(
            client, records, external_id_field=EXTERNAL_ID_FIELD, batch_size=BATCH_SIZE
        )
        for result in results:
            if result.failed_results_csv is not None:
                key = f"staging/sync-failures/{run_id}/{result.job_id}.csv"
                s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=result.failed_results_csv.encode(),
                    ContentType="text/csv",
                )
                failure_keys.append(key)
        _emit_metrics(client, records_in=len(records), failed=sum(r.failed for r in results))
    return {
        "bucket": bucket,
        "run_id": run_id,
        "records_in": len(records),
        "records_failed": sum(r.failed for r in results),
        "jobs": [r.job_id for r in results],
        "failure_keys": failure_keys,
    }


def _emit_metrics(client: BulkApiClient, *, records_in: int, failed: int) -> None:
    data: list[dict[str, Any]] = [
        {"MetricName": "RecordsProcessed", "Value": records_in, "Unit": "Count"},
        {"MetricName": "RecordsFailed", "Value": failed, "Unit": "Count"},
    ]
    if client.api_usage is not None:
        used, limit = client.api_usage
        data.append(
            {"MetricName": "ApiUsagePercent", "Value": used / limit * 100, "Unit": "Percent"}
        )
    boto3.client("cloudwatch").put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=data)
