"""Tests for the classify Lambda adapter: auth, routing, fail-closed, logging."""

import base64
import hashlib
import json
import logging
from typing import Any

import pytest

from classifier import lambda_api
from classifier.handler import rule_set_hash

TOKEN = "unit-test-classify-token"  # pragma: allowlist secret
SECRET_ID = "outreach/classify/token"  # pragma: allowlist secret


class FakeSecrets:
    def __init__(self, token: str, fail: bool = False) -> None:
        self.token = token
        self.fail = fail
        self.calls = 0

    def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("secretsmanager down")
        assert SecretId == SECRET_ID
        return {"SecretString": self.token}


@pytest.fixture
def secrets_env(monkeypatch: pytest.MonkeyPatch) -> FakeSecrets:
    fake = FakeSecrets(TOKEN)
    monkeypatch.setattr(lambda_api, "_token_cache", None)
    monkeypatch.setattr("classifier.lambda_api.boto3.client", lambda service: fake)
    monkeypatch.setenv(lambda_api.TOKEN_SECRET_ENV, SECRET_ID)
    return fake


def url_event(
    method: str = "POST",
    path: str = "/v1/classify",
    body: str | None = None,
    token: str | None = TOKEN,
    b64: bool = False,
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    event: dict[str, Any] = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": headers,
    }
    if body is not None:
        event["body"] = base64.b64encode(body.encode()).decode() if b64 else body
        event["isBase64Encoded"] = b64
    return event


def call(event: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = lambda_api.handler(event, None)
    return response["statusCode"], json.loads(response["body"])


def test_classify_stop_over_the_wire(secrets_env: FakeSecrets) -> None:
    status, payload = call(url_event(body=json.dumps({"message_id": "1", "body": "STOP"})))
    assert status == 200
    assert payload["action"] == "suppress_and_stop"
    assert payload["rule"] == "opt_out"
    assert payload["confidence"] is None
    assert payload["model_version"] == "rules-v1"


def test_health_reports_the_same_rule_set_hash(secrets_env: FakeSecrets) -> None:
    status, payload = call(url_event(method="GET", path="/health"))
    assert status == 200
    assert payload == {"version": "rules-v1", "rule_set_hash": rule_set_hash()}


def test_missing_or_wrong_token_is_401(secrets_env: FakeSecrets) -> None:
    assert call(url_event(token=None))[0] == 401
    assert call(url_event(token="wrong"))[0] == 401
    assert call(url_event(token=None) | {"headers": {"Authorization": "Basic abc"}})[0] == 401
    no_headers = url_event(token=None)
    del no_headers["headers"]
    assert call(no_headers)[0] == 401


def test_unconfigured_secret_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lambda_api, "_token_cache", None)
    monkeypatch.delenv(lambda_api.TOKEN_SECRET_ENV, raising=False)
    assert call(url_event())[0] == 503


def test_secretsmanager_failure_is_503_not_open(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSecrets(TOKEN, fail=True)
    monkeypatch.setattr(lambda_api, "_token_cache", None)
    monkeypatch.setattr("classifier.lambda_api.boto3.client", lambda service: fake)
    monkeypatch.setenv(lambda_api.TOKEN_SECRET_ENV, SECRET_ID)
    assert call(url_event())[0] == 503


def test_token_is_fetched_once_per_environment(secrets_env: FakeSecrets) -> None:
    call(url_event(method="GET", path="/health"))
    call(url_event(method="GET", path="/health"))
    assert secrets_env.calls == 1


def test_unknown_route_is_404(secrets_env: FakeSecrets) -> None:
    assert call(url_event(method="GET", path="/"))[0] == 404
    assert call(url_event(method="DELETE", path="/v1/classify"))[0] == 404


def test_base64_bodies_are_decoded(secrets_env: FakeSecrets) -> None:
    status, payload = call(url_event(body=json.dumps({"body": "Who is this?"}), b64=True))
    assert status == 200
    assert payload["intent"] == "Question"


def test_garbage_and_non_object_bodies_fail_closed(secrets_env: FakeSecrets) -> None:
    for raw in ("not json", "[1, 2]", ""):
        status, payload = call(url_event(body=raw))
        assert status == 200
        assert payload["action"] == "human_review"
        assert payload["handoff_reason"] == "internal_error:KeyError"


def test_classify_exception_returns_human_review_not_500(
    secrets_env: FakeSecrets, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(lambda_api, "classify", boom)
    status, payload = call(url_event(body=json.dumps({"body": "Yes"})))
    assert status == 200
    assert payload["action"] == "human_review"
    assert payload["handoff_reason"] == "internal_error:wrapper"


def test_bodies_are_never_logged_only_their_hash(
    secrets_env: FakeSecrets, caplog: pytest.LogCaptureFixture
) -> None:
    body = "SECRET-BODY-MUST-NOT-APPEAR call me"
    with caplog.at_level(logging.INFO, logger="classify.lambda"):
        call(url_event(body=json.dumps({"message_id": "42", "body": body})))
    assert "SECRET-BODY" not in caplog.text
    assert hashlib.sha256(body.encode()).hexdigest() in caplog.text
