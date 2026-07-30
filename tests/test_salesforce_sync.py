"""Tests for the Bulk API 2.0 sync Lambda.

Acceptance: 100 records upserted twice -> exactly 100 records exist
(idempotency on Outreach_External_Id__c), and a deliberately malformed record
lands in staging/sync-failures/ with the Salesforce error message.

These run against the in-memory FakeSalesforce everywhere; the same flow runs
against a real scratch org when SALESFORCE_SYNC_TEST_SECRET is set (CI with a
Connected App configured — see ADR-012).
"""

import csv
import io
import json
import os
from typing import Any

import polars as pl
import pytest

from salesforce.auth import get_access_token, load_credentials
from salesforce.bulk import BulkApiClient, SalesforceApiError, upsert_records
from salesforce.sync import BATCH_SIZE, handler, lead_records

BUCKET = "outreach-data"


def _cleansed_parquet(rows: list[dict[str, Any]]) -> bytes:
    frame = pl.DataFrame(
        {
            "external_id": [r["external_id"] for r in rows],
            "company_name": [r.get("company_name") for r in rows],
            "contact_name": [r.get("contact_name") for r in rows],
            "phone": [r.get("phone") for r in rows],
            "email": [r.get("email") for r in rows],
        }
    )
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    return buffer.getvalue()


def _seed(env: Any, rows: list[dict[str, Any]], run_id: str = "run-1") -> dict[str, Any]:
    key = f"staging/cleansed/{run_id}.parquet"
    env.s3.put_object(Bucket=BUCKET, Key=key, Body=_cleansed_parquet(rows))
    return {"bucket": BUCKET, "key": key, "run_id": run_id}


def _rows(n: int) -> list[dict[str, Any]]:
    return [
        {
            "external_id": f"ext-{i:05d}",
            "company_name": f"Acme {i} LLC",
            "contact_name": f"Contact {i}",
            "phone": f"+1650253{i:04d}",
            "email": f"user{i}@example.com",
        }
        for i in range(n)
    ]


def _client(env: Any) -> BulkApiClient:
    token, instance_url = get_access_token(
        load_credentials("outreach/salesforce/jwt", env.boto_clients["secretsmanager"])
    )
    return BulkApiClient(instance_url, token, sleep=lambda s: None)


def _secrets(env: Any) -> Any:
    return env.boto_clients["secretsmanager"]


# --- acceptance -----------------------------------------------------------------


def test_upserting_100_records_twice_leaves_100_records(sf_env: Any) -> None:
    rows = _rows(100)
    out1 = handler(_seed(sf_env, rows, "run-1"))
    out2 = handler(_seed(sf_env, rows, "run-2"))
    assert out1["records_in"] == out2["records_in"] == 100
    assert out1["records_failed"] == out2["records_failed"] == 0
    assert len(sf_env.sf.leads) == 100  # idempotent: upsert, not insert
    assert sf_env.sf.leads["ext-00042"]["Company"] == "Acme 42 LLC"


def test_malformed_record_lands_in_sync_failures(sf_env: Any) -> None:
    rows = _rows(5)
    rows[3]["company_name"] = None  # Company is required on Lead -> upsert fails
    out = handler(_seed(sf_env, rows, "run-bad"))
    assert out["records_in"] == 5
    assert out["records_failed"] == 1
    assert len(sf_env.sf.leads) == 4
    [failure_key] = out["failure_keys"]
    assert failure_key.startswith("staging/sync-failures/run-bad/")
    body = sf_env.s3.get_object(Bucket=BUCKET, Key=failure_key)["Body"].read().decode()
    failed = list(csv.DictReader(io.StringIO(body)))
    assert len(failed) == 1
    assert failed[0]["Outreach_External_Id__c"] == "ext-00003"
    assert "REQUIRED_FIELD_MISSING" in failed[0]["sf__Error"]  # never silently dropped


# --- batching -------------------------------------------------------------------


def test_batched_at_10000_records(sf_env: Any) -> None:
    assert BATCH_SIZE == 10_000
    records = lead_records(_rows(10_500))
    client = _client(sf_env)
    results = upsert_records(
        client, records, external_id_field="Outreach_External_Id__c", batch_size=BATCH_SIZE
    )
    assert len(results) == 2
    sizes = [
        len(job["csv"].strip().splitlines()) - 1 for job in sf_env.sf.jobs.values()
    ]  # minus header
    assert sorted(sizes) == [500, 10_000]
    assert len(sf_env.sf.leads) == 10_500


def test_no_records_makes_no_api_calls(sf_env: Any) -> None:
    out = handler(_seed(sf_env, [], "run-empty"))
    assert out["records_in"] == 0
    assert sf_env.sf.api_calls == 0


# --- retry / backoff ------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 503])
def test_exponential_backoff_then_success(sf_env: Any, status: int) -> None:
    sleeps: list[float] = []
    client = _client(sf_env)
    client._sleep = sleeps.append
    sf_env.sf.reject_statuses = [status, status]
    assert client.query_count("SELECT COUNT() FROM Lead") == 0
    assert sleeps == [1.0, 2.0]


def test_retries_exhausted_raises(sf_env: Any) -> None:
    sleeps: list[float] = []
    client = _client(sf_env)
    client._sleep = sleeps.append
    sf_env.sf.reject_statuses = [429] * 10
    with pytest.raises(SalesforceApiError) as excinfo:
        client.query_count("SELECT COUNT() FROM Lead")
    assert excinfo.value.status == 429
    assert sleeps == [1.0, 2.0, 4.0, 8.0]  # 5 attempts, exponential


def test_non_retryable_error_raises_immediately(sf_env: Any) -> None:
    client = _client(sf_env)
    sf_env.sf.reject_statuses = [400]
    with pytest.raises(SalesforceApiError) as excinfo:
        client.query_count("SELECT COUNT() FROM Lead")
    assert excinfo.value.status == 400


# --- job lifecycle --------------------------------------------------------------


def test_wait_polls_until_job_completes(sf_env: Any) -> None:
    sf_env.sf.poll_delays = 2
    client = _client(sf_env)
    polls: list[float] = []
    client._sleep = polls.append
    results = upsert_records(
        client, lead_records(_rows(3)), external_id_field="Outreach_External_Id__c"
    )
    assert results[0].state == "JobComplete"
    assert len(polls) >= 2


def test_wait_times_out(sf_env: Any) -> None:
    sf_env.sf.poll_delays = 50
    client = _client(sf_env)
    client._sleep = lambda s: None
    client.create_upsert_job("Lead", "Outreach_External_Id__c")
    job_id = next(iter(sf_env.sf.jobs))
    sf_env.sf.jobs[job_id]["state"] = "Processing"
    with pytest.raises(SalesforceApiError, match="timed out"):
        client.wait_for_job(job_id, max_polls=3)


def test_failed_job_raises_loudly(sf_env: Any) -> None:
    sf_env.sf.job_error = "InvalidBatch : Field name not found"
    with pytest.raises(SalesforceApiError, match="InvalidBatch"):
        handler(_seed(sf_env, _rows(2), "run-jobfail"))


# --- metrics --------------------------------------------------------------------


def test_api_usage_surfaces_to_cloudwatch(sf_env: Any) -> None:
    handler(_seed(sf_env, _rows(3), "run-metrics"))
    assert sf_env.cw.metric("RecordsProcessed") == 3
    assert sf_env.cw.metric("RecordsFailed") == 0
    usage = sf_env.cw.metric("ApiUsagePercent")
    assert usage is not None and 0 < usage < 100


def test_lead_records_maps_and_defaults() -> None:
    [record] = lead_records([{"external_id": "x", "company_name": "Acme", "contact_name": None}])
    assert record == {
        "Outreach_External_Id__c": "x",
        "Company": "Acme",
        "LastName": "Unknown",
        "Phone": "",
        "Email": "",
    }


def test_limit_header_absent_or_malformed_is_tolerated(sf_env: Any) -> None:
    from salesforce.http import HttpResponse

    client = _client(sf_env)
    usage_before = client.api_usage
    client._record_usage(HttpResponse(200, {}, b""))  # no header
    client._record_usage(HttpResponse(200, {"Sforce-Limit-Info": "nonsense"}, b""))
    assert client.api_usage == usage_before  # unchanged, no crash


def test_metrics_without_usage_data(sf_env: Any) -> None:
    from salesforce.sync import _emit_metrics

    client = BulkApiClient("https://org.example", "FAKE_TOKEN", sleep=lambda s: None)
    _emit_metrics(client, records_in=1, failed=0)
    assert sf_env.cw.metric("RecordsProcessed") == 1
    assert sf_env.cw.metric("ApiUsagePercent") is None


# --- real scratch org (env-gated) -----------------------------------------------


@pytest.mark.skipif(
    "SALESFORCE_SYNC_TEST_SECRET" not in os.environ,
    reason="set SALESFORCE_SYNC_TEST_SECRET to run against a real scratch org",
)
def test_scratch_org_upsert_idempotency() -> None:  # pragma: no cover
    from salesforce.auth import SalesforceCredentials

    raw = json.loads(os.environ["SALESFORCE_SYNC_TEST_SECRET"])
    creds = SalesforceCredentials(
        client_id=raw["client_id"],
        username=raw["username"],
        login_url=raw["login_url"],
        private_key_pem=raw["private_key"],
    )
    token, instance_url = get_access_token(creds)
    client = BulkApiClient(instance_url, token)
    records = lead_records(
        [
            {"external_id": f"citest-{i:03d}", "company_name": f"CI Test {i}", "contact_name": "CI"}
            for i in range(100)
        ]
    )
    for _ in range(2):
        results = upsert_records(client, records, external_id_field="Outreach_External_Id__c")
        assert all(r.failed == 0 for r in results)
    count = client.query_count(
        "SELECT COUNT() FROM Lead WHERE Outreach_External_Id__c LIKE 'citest-%'"
    )
    assert count == 100
