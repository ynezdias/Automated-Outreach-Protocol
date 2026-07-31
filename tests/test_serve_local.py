"""HTTP-layer tests for the local classify wrapper.

Bearer auth on every request (401 without), fail-closed 200s (never a 500),
/docs left enabled, and message bodies never logged — only their SHA-256.
"""

import hashlib
import logging
from typing import Any

import pytest
import serve_local
from fastapi.testclient import TestClient

TOKEN = "test-token-0123456789"  # pragma: allowlist secret


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CLASSIFY_API_TOKEN", TOKEN)
    return TestClient(serve_local.app)


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_classify_requires_bearer_token(client: TestClient) -> None:
    assert client.post("/v1/classify", json={"body": "Yes"}).status_code == 401
    wrong = {"Authorization": "Bearer wrong-token"}
    assert client.post("/v1/classify", json={"body": "Yes"}, headers=wrong).status_code == 401


def test_health_requires_token_and_reports_the_rule_set(client: TestClient) -> None:
    assert client.get("/health").status_code == 401
    response = client.get("/health", headers=auth())
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "rules-v1"
    assert len(payload["rule_set_hash"]) == 64


def test_unconfigured_token_refuses_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLASSIFY_API_TOKEN", raising=False)
    client = TestClient(serve_local.app)
    response = client.post("/v1/classify", json={"body": "Yes"}, headers=auth())
    assert response.status_code == 503, "no configured token -> nothing authenticates"


def test_classify_end_to_end(client: TestClient) -> None:
    response = client.post(
        "/v1/classify",
        json={"message_id": "1", "from_number": "+15512357742", "body": "Who is this?"},
        headers=auth(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "human_review"
    assert payload["intent"] == "Question"
    assert payload["confidence"] is None
    assert payload["model_version"] == "rules-v1"


def test_exception_returns_human_review_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(serve_local, "classify", boom)
    response = client.post("/v1/classify", json={"body": "Yes"}, headers=auth())
    assert response.status_code == 200, "fail closed: never a 500"
    payload = response.json()
    assert payload["action"] == "human_review"
    assert payload["handoff_reason"] == "internal_error:wrapper"


def test_unparseable_json_fails_closed(client: TestClient) -> None:
    response = client.post(
        "/v1/classify",
        content=b"this is not json",
        headers={**auth(), "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "human_review"


def test_docs_are_enabled(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert "/v1/classify" in openapi.json()["paths"]


def test_bodies_are_never_logged_only_their_hash(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    body = "SECRET-BODY-MUST-NOT-APPEAR-IN-LOGS call me"
    with caplog.at_level(logging.INFO, logger="classify.local"):
        client.post("/v1/classify", json={"message_id": "42", "body": body}, headers=auth())
    assert body not in caplog.text
    assert "SECRET-BODY" not in caplog.text
    assert hashlib.sha256(body.encode()).hexdigest() in caplog.text
