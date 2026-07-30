"""Reconciliation Lambda: lake vs Salesforce record counts.

Event:  ``{}`` (scheduled daily by EventBridge).
Env:    ``DATA_BUCKET``, ``SALESFORCE_SECRET_ID``.
Output: ``{"lake_count", "salesforce_count", "drift_percent"}``

Counts distinct external IDs across ``staging/cleansed/*.parquet`` (runs
overlap — upsert dedupes on the external ID, so distinct IDs are the ground
truth) against Salesforce leads carrying an external ID, and publishes
``ReconciliationDriftPercent`` to CloudWatch. The observability stack alarms
at drift > 1%.
"""

import io
import os
from typing import Any

import boto3
import polars as pl

from cleansing import pipeline_io
from salesforce.auth import get_access_token, load_credentials
from salesforce.bulk import BulkApiClient
from salesforce.sync import METRIC_NAMESPACE

CLEANSED_PREFIX = "staging/cleansed/"
DRIFT_QUERY = "SELECT COUNT() FROM Lead WHERE Outreach_External_Id__c != null"


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    s3 = pipeline_io.get_s3()
    bucket = os.environ["DATA_BUCKET"]
    external_ids: set[str] = set()
    for key in pipeline_io.list_keys(s3, bucket, CLEANSED_PREFIX):
        if key.endswith(".parquet"):
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            external_ids.update(pl.read_parquet(io.BytesIO(body))["external_id"].to_list())
    lake_count = len(external_ids)

    creds = load_credentials(os.environ["SALESFORCE_SECRET_ID"], boto3.client("secretsmanager"))
    access_token, instance_url = get_access_token(creds)
    salesforce_count = BulkApiClient(instance_url, access_token).query_count(DRIFT_QUERY)

    if lake_count == 0:
        drift_percent = 0.0 if salesforce_count == 0 else 100.0
    else:
        drift_percent = abs(lake_count - salesforce_count) / lake_count * 100
    boto3.client("cloudwatch").put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=[
            {"MetricName": "ReconciliationDriftPercent", "Value": drift_percent, "Unit": "Percent"},
            {"MetricName": "LakeRecordCount", "Value": lake_count, "Unit": "Count"},
            {"MetricName": "SalesforceRecordCount", "Value": salesforce_count, "Unit": "Count"},
        ],
    )
    return {
        "lake_count": lake_count,
        "salesforce_count": salesforce_count,
        "drift_percent": drift_percent,
    }
