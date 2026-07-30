"""Unit tests for individual pipeline step behaviors not covered end-to-end."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from cleansing import pipeline_io
from cleansing.identity import EXTERNAL_ID_NAMESPACE, external_id, normalized_key
from cleansing.normalize import normalize_company
from cleansing.steps import suppress, validate
from tests.fake_s3 import FakeS3Client

BUCKET = "outreach-data"


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3Client:
    client = FakeS3Client()
    monkeypatch.setattr("cleansing.pipeline_io.boto3.client", lambda service: client)
    monkeypatch.setattr("suppression.store.boto3.client", lambda service: client)
    return client


def _seed_step_input(s3: FakeS3Client, rows: list[dict[str, Any]]) -> dict[str, Any]:
    pipeline_io.write_ndjson(s3, BUCKET, "staging/pipeline/run/in.ndjson", rows)
    return {"bucket": BUCKET, "key": "staging/pipeline/run/in.ndjson", "run_id": "run"}


def _valid_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "company_name": "Acme LLC",
        "contact_name": "Pat",
        "phone": "+16502530000",
        "email": "user@example.com",
        "enriched_at": "2026-06-01T00:00:00+00:00",
        "last_contacted_at": None,
        "line_type": None,
    }
    row.update(overrides)
    return row


# --- normalize_company ----------------------------------------------------------


def test_company_whitespace_collapsed_and_folded() -> None:
    assert normalize_company("  Acme   Capital\tLLC ") == "Acme Capital LLC"


def test_company_zero_width_only_is_none() -> None:
    assert normalize_company("​​") is None


def test_company_none_is_none() -> None:
    assert normalize_company(None) is None


# --- external id ----------------------------------------------------------------


def test_external_id_is_deterministic_uuid5() -> None:
    key = normalized_key("+16502530000", "user@example.com")
    assert key == "+16502530000|user@example.com"
    assert external_id(key) == external_id(key)
    assert external_id(key) == str(uuid.uuid5(EXTERNAL_ID_NAMESPACE, key))


def test_normalized_key_handles_missing_sides() -> None:
    assert normalized_key("+16502530000", None) == "+16502530000|"
    assert normalized_key(None, "user@example.com") == "|user@example.com"


# --- validate: Twilio Lookup feature flag ---------------------------------------


def test_twilio_lookup_annotates_when_enabled(
    s3: FakeS3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TWILIO_LOOKUP_ENABLED", "true")
    monkeypatch.setattr(validate, "mx_check", lambda domain: True)
    calls: list[str] = []

    def fake_lookup(phone: str) -> str:
        calls.append(phone)
        return "mobile"

    monkeypatch.setattr(validate, "line_type_lookup", fake_lookup)
    event = _seed_step_input(s3, [_valid_row(), _valid_row(phone=None, email="a@example.com")])
    out = validate.handler(event)
    rows = pipeline_io.read_ndjson(s3, BUCKET, out["key"])
    assert [r["line_type"] for r in rows] == ["mobile", None]
    assert calls == ["+16502530000"]  # phones only — the flag gates a paid API


def test_twilio_lookup_disabled_by_default(
    s3: FakeS3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TWILIO_LOOKUP_ENABLED", raising=False)
    monkeypatch.setattr(validate, "mx_check", lambda domain: True)
    monkeypatch.setattr(
        validate, "line_type_lookup", lambda phone: pytest.fail("must not be called")
    )
    out = validate.handler(_seed_step_input(s3, [_valid_row()]))
    rows = pipeline_io.read_ndjson(s3, BUCKET, out["key"])
    assert rows[0]["line_type"] is None


# --- suppress: default as_of and cooldown boundary ------------------------------


def test_suppress_defaults_as_of_to_now(s3: FakeS3Client) -> None:
    recent = datetime.now(UTC).isoformat()
    event = _seed_step_input(
        s3,
        [
            _valid_row(last_contacted_at=recent),
            _valid_row(phone="+16502530001", email="b@example.com"),
        ],
    )
    out = suppress.handler(event)  # no as_of in the event
    assert (out["rows_out"], out["cooldown_excluded"], out["suppressed"]) == (1, 1, 0)


def test_cooldown_days_env_override(s3: FakeS3Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COOLDOWN_DAYS", "5")
    event = _seed_step_input(s3, [_valid_row(last_contacted_at="2026-07-20T00:00:00+00:00")])
    out = suppress.handler({**event, "as_of": "2026-07-30T00:00:00+00:00"})
    assert (out["rows_out"], out["cooldown_excluded"]) == (1, 0)  # 10 days > 5-day window
