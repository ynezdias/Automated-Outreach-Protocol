"""End-to-end integration test for the cleansing pipeline (steps 1-7).

Runs a deterministic 1,000-row fixture through every step Lambda handler in
sequence, exactly as Step Functions chains them, and asserts row counts at each
stage plus the quarantine files.

Fixture composition (1,000 rows total):

    780  clean, both identifiers valid
     30  clean, phone only
     30  clean, email only
      5  valid phone + email whose domain has no MX  -> email nulled, row kept
      5  clean, last contacted long before COOLDOWN_DAYS -> kept
     20  phone pre-registered in the suppression list -> excluded at step 5
     10  contacted 10 days ago (inside COOLDOWN_DAYS) -> excluded at step 5
     50  duplicates of the first 50 clean rows, enriched 1 day earlier
     20  email-only rows whose domain has no MX      -> quarantined at step 3
     30  schema-valid but unnormalizable             -> quarantined at step 2
     20  schema-invalid                              -> quarantined at step 1

Uses moto when available (CI); falls back to the in-memory fake S3 client on
platforms where moto cannot be installed (see ADR-009).
"""

import csv
import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl
import pytest

from cleansing import pipeline_io
from cleansing.identity import EXTERNAL_ID_NAMESPACE, normalized_key
from cleansing.steps import (
    assign_external_id,
    dedupe,
    normalize,
    schema_validate,
    suppress,
    validate,
    write_staging,
)
from suppression import SuppressionStore
from tests.fake_s3 import FakeS3Client

try:
    from moto import mock_aws

    HAVE_MOTO = True
except ImportError:  # pragma: no cover - exercised only on win-arm64 dev machines
    HAVE_MOTO = False

BUCKET = "outreach-data"
INPUT_KEY = "raw/enrichment/enrichment-2026-07-29.csv"
AS_OF = datetime(2026, 7, 30, 0, 0, 0, tzinfo=UTC)
ENRICHED_BASE = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
FIELDS = ["company_name", "contact_name", "phone", "email", "enriched_at", "last_contacted_at"]

GOOD_DOMAIN = "example.com"
BAD_MX_DOMAIN = "no-mx.example"


def _phone(i: int) -> str:
    return f"+1650253{i:04d}"


def _row(i: int, **overrides: str) -> dict[str, str]:
    row = {
        "company_name": f"Acme {i} LLC",
        "contact_name": f"Contact {i}",
        "phone": _phone(i),
        "email": f"user{i}@{GOOD_DOMAIN}",
        "enriched_at": (ENRICHED_BASE + timedelta(minutes=i)).isoformat(),
        "last_contacted_at": "",
    }
    row.update(overrides)
    return row


def build_fixture() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    rows += [_row(i) for i in range(780)]  # clean, both identifiers
    rows += [_row(i, email="") for i in range(780, 810)]  # phone only
    rows += [_row(i, phone="") for i in range(810, 840)]  # email only
    # valid phone, email domain without MX: email nulled at step 3, row survives
    rows += [_row(i, email=f"user{i}@{BAD_MX_DOMAIN}") for i in range(840, 845)]
    # contacted long ago — outside the cooldown window
    rows += [_row(i, last_contacted_at="2026-01-01T00:00:00+00:00") for i in range(845, 850)]
    rows += [_row(i) for i in range(850, 870)]  # phone pre-suppressed
    rows += [
        _row(i, last_contacted_at="2026-07-20T00:00:00+00:00") for i in range(870, 880)
    ]  # inside 90-day cooldown

    # 50 duplicates of rows 0..49, enriched one day earlier and in a messier
    # phone format: 25 placed before their newer counterpart, 25 after.
    def dup(i: int) -> dict[str, str]:
        return _row(
            i,
            phone=f"(650) 253-{i:04d}",
            enriched_at=(ENRICHED_BASE + timedelta(minutes=i) - timedelta(days=1)).isoformat(),
        )

    rows = [dup(i) for i in range(25)] + rows + [dup(i) for i in range(25, 50)]
    # quarantined at step 3: email only, domain without MX
    rows += [_row(i, phone="", email=f"userq{i}@{BAD_MX_DOMAIN}") for i in range(940, 960)]
    # quarantined at step 2: present but unnormalizable identifiers / company
    rows += [_row(i, phone="not-a-phone", email="not-an-email") for i in range(960, 988)]
    rows += [_row(i, company_name="​​") for i in range(988, 990)]
    # quarantined at step 1: schema failures
    rows += [_row(i, phone="", email="") for i in range(990, 1000)]  # no identifier
    rows += [_row(i, company_name="") for i in range(1000, 1005)]
    rows += [_row(i, enriched_at="yesterday") for i in range(1005, 1008)]
    rows += [_row(i, enriched_at="2026-06-01T00:00:00") for i in range(1008, 1010)]  # naive
    assert len(rows) == 1000
    return rows


def _to_csv(rows: list[dict[str, str]]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    if HAVE_MOTO:
        import boto3

        for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            monkeypatch.setenv(var, "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        with mock_aws():
            client = boto3.client("s3")
            client.create_bucket(Bucket=BUCKET)
            yield client
    else:
        client = FakeS3Client()
        monkeypatch.setattr("cleansing.pipeline_io.boto3.client", lambda service: client)
        monkeypatch.setattr("suppression.store.boto3.client", lambda service: client)
        yield client


@pytest.fixture
def pipeline_env(s3: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(validate, "mx_check", lambda domain: domain == GOOD_DOMAIN)
    monkeypatch.delenv("TWILIO_LOOKUP_ENABLED", raising=False)
    monkeypatch.setenv("COOLDOWN_DAYS", "90")
    s3.put_object(Bucket=BUCKET, Key=INPUT_KEY, Body=_to_csv(build_fixture()))
    suppression_writer = SuppressionStore(bucket=BUCKET, s3_client=s3)
    for i in range(850, 870):
        suppression_writer.add_suppression(
            f"650 253 {i:04d}", reason="STOP reply", source="sms", occurred_at=AS_OF
        )
    return s3


def _read_quarantine(s3: Any, key: str) -> list[dict[str, str]]:
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
    return [dict(r) for r in csv.DictReader(io.StringIO(body))]


def test_pipeline_end_to_end(pipeline_env: Any) -> None:
    s3 = pipeline_env

    out1 = schema_validate.handler({"bucket": BUCKET, "key": INPUT_KEY})
    assert (out1["rows_in"], out1["rows_out"], out1["rows_quarantined"]) == (1000, 980, 20)
    quarantined = _read_quarantine(s3, out1["quarantine_key"])
    assert len(quarantined) == 20
    assert {r["reason"] for r in quarantined} == {
        "missing_contact_identifier",
        "missing_company_name",
        "invalid_enriched_at",
    }

    out2 = normalize.handler(out1)
    assert (out2["rows_in"], out2["rows_out"], out2["rows_quarantined"]) == (980, 950, 30)
    reasons2 = {r["reason"] for r in _read_quarantine(s3, out2["quarantine_key"])}
    assert reasons2 == {"no_valid_identifier", "company_empty_after_normalization"}

    out3 = validate.handler(out2)
    assert (out3["rows_in"], out3["rows_out"], out3["rows_quarantined"]) == (950, 930, 20)
    reasons3 = {r["reason"] for r in _read_quarantine(s3, out3["quarantine_key"])}
    assert reasons3 == {"email_domain_has_no_mx"}

    out4 = dedupe.handler(out3)
    assert (out4["rows_in"], out4["rows_out"], out4["duplicates_removed"]) == (930, 880, 50)
    # most-recently-enriched wins: survivors of the 50 duplicated keys carry the
    # newer enriched_at, whichever CSV order the duplicate arrived in
    deduped = pipeline_io.read_ndjson(s3, BUCKET, out4["key"])
    by_phone = {r["phone"]: r for r in deduped if r["phone"]}
    for i in (0, 30, 49):
        assert (
            by_phone[_phone(i)]["enriched_at"] == (ENRICHED_BASE + timedelta(minutes=i)).isoformat()
        )

    out5 = suppress.handler({**out4, "as_of": AS_OF.isoformat()})
    assert (out5["rows_in"], out5["rows_out"]) == (880, 850)
    assert (out5["suppressed"], out5["cooldown_excluded"]) == (20, 10)
    excluded = pipeline_io.read_ndjson(s3, BUCKET, out5["excluded_key"])
    assert len(excluded) == 30
    suppressed_phones = {r["phone"] for r in excluded if r["excluded_reason"] == "suppressed"}
    assert suppressed_phones == {_phone(i) for i in range(850, 870)}

    out6 = assign_external_id.handler(out5)
    assert out6["rows_out"] == 850

    out7 = write_staging.handler(out6)
    assert out7["rows_out"] == 850
    assert out7["key"].startswith("staging/cleansed/")
    assert out7["key"].endswith(".parquet")

    body = s3.get_object(Bucket=BUCKET, Key=out7["key"])["Body"].read()
    frame = pl.read_parquet(io.BytesIO(body))
    assert frame.height == 850
    assert frame["external_id"].n_unique() == 850
    # external IDs are deterministic UUIDv5 over the normalized key
    sample = frame.filter(pl.col("phone") == _phone(0)).row(0, named=True)
    expected = str(
        uuid.uuid5(EXTERNAL_ID_NAMESPACE, normalized_key(_phone(0), f"user0@{GOOD_DOMAIN}"))
    )
    assert sample["external_id"] == expected
    # rows that survived the failed-MX soft path kept their phone, lost their email
    soft = frame.filter(pl.col("phone") == _phone(840))
    assert soft.height == 1 and soft["email"][0] is None
    # Twilio Lookup disabled by default: annotated as null, never called
    assert frame["line_type"].null_count() == 850
