"""Tests for the Salesforce -> AWS suppression mirror endpoint."""

import json
from typing import Any

from suppression import SuppressionStore
from suppression.api import handler


def _event(**overrides: str) -> dict[str, str]:
    payload = {
        "identifier": "+16502530400",
        "reason": "opt_out",
        "source": "salesforce_inbound_guardrail",
        "occurred_at": "2026-07-30T12:00:00+00:00",
    }
    payload.update(overrides)
    return {"body": json.dumps(payload)}


def test_valid_event_is_queryable_immediately(sf_env: Any) -> None:
    response = handler(_event())
    assert response["statusCode"] == 200
    # A fresh store sees the event now — the 60-second bound holds trivially.
    result = SuppressionStore("outreach-data", s3_client=sf_env.s3).is_suppressed(
        phone="+16502530400", email=None
    )
    assert result.suppressed is True
    assert result.matches[0].source == "salesforce_inbound_guardrail"


def test_unnormalizable_identifier_is_rejected(sf_env: Any) -> None:
    response = handler(_event(identifier="not-a-number"))
    assert response["statusCode"] == 400
    assert "normalize" in json.loads(response["body"])["error"]


def test_missing_field_is_rejected(sf_env: Any) -> None:
    payload = json.loads(_event()["body"])
    del payload["reason"]
    assert handler({"body": json.dumps(payload)})["statusCode"] == 400


def test_naive_timestamp_is_rejected(sf_env: Any) -> None:
    assert handler(_event(occurred_at="2026-07-30T12:00:00"))["statusCode"] == 400


def test_garbage_body_is_rejected(sf_env: Any) -> None:
    assert handler({"body": "not json"})["statusCode"] == 400


def test_absent_body_is_rejected(sf_env: Any) -> None:
    assert handler({"body": None})["statusCode"] == 400
