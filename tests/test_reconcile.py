"""Tests for the lake-vs-Salesforce reconciliation job."""

import io
from typing import Any

import polars as pl

from salesforce.reconcile import handler

BUCKET = "outreach-data"


def _write_parquet(env: Any, key: str, external_ids: list[str]) -> None:
    frame = pl.DataFrame({"external_id": external_ids})
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    env.s3.put_object(Bucket=BUCKET, Key=key, Body=buffer.getvalue())


def _seed_salesforce(env: Any, external_ids: list[str]) -> None:
    for ext in external_ids:
        env.sf.leads[ext] = {"Outreach_External_Id__c": ext}


def test_no_drift(sf_env: Any) -> None:
    # overlapping runs: distinct external ids are what count, not raw rows
    _write_parquet(sf_env, "staging/cleansed/run-1.parquet", ["a", "b", "c"])
    _write_parquet(sf_env, "staging/cleansed/run-2.parquet", ["b", "c", "d"])
    sf_env.s3.put_object(Bucket=BUCKET, Key="staging/cleansed/notes.txt", Body=b"ignore me")
    _seed_salesforce(sf_env, ["a", "b", "c", "d"])
    out = handler({})
    assert (out["lake_count"], out["salesforce_count"]) == (4, 4)
    assert out["drift_percent"] == 0.0
    assert sf_env.cw.metric("ReconciliationDriftPercent") == 0.0


def test_drift_over_threshold_is_reported(sf_env: Any) -> None:
    _write_parquet(sf_env, "staging/cleansed/run-1.parquet", [f"id-{i}" for i in range(100)])
    _seed_salesforce(sf_env, [f"id-{i}" for i in range(97)])  # 3% drift
    out = handler({})
    assert out["drift_percent"] == 3.0
    assert sf_env.cw.metric("ReconciliationDriftPercent") == 3.0
    assert sf_env.cw.metric("LakeRecordCount") == 100
    assert sf_env.cw.metric("SalesforceRecordCount") == 97


def test_empty_lake_and_org_is_zero_drift(sf_env: Any) -> None:
    assert handler({})["drift_percent"] == 0.0


def test_empty_lake_with_salesforce_records_is_full_drift(sf_env: Any) -> None:
    _seed_salesforce(sf_env, ["orphan"])
    assert handler({})["drift_percent"] == 100.0


def test_listing_paginates(sf_env: Any, monkeypatch: Any) -> None:
    from tests.fake_s3 import FakeS3Client

    paged = FakeS3Client(page_size=1)
    monkeypatch.setattr(
        "cleansing.pipeline_io.boto3.client",
        lambda service: paged if service == "s3" else sf_env.boto_clients[service],
    )
    for n in range(3):
        frame = pl.DataFrame({"external_id": [f"p-{n}"]})
        buffer = io.BytesIO()
        frame.write_parquet(buffer)
        paged.put_object(
            Bucket=BUCKET, Key=f"staging/cleansed/run-{n}.parquet", Body=buffer.getvalue()
        )
    _seed_salesforce(sf_env, ["p-0", "p-1", "p-2"])
    out = handler({})
    assert out["lake_count"] == 3
    assert out["drift_percent"] == 0.0
