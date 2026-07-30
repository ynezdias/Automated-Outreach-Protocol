"""Tests for the OAuth 2.0 JWT Bearer Flow — no passwords, no refresh tokens."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
import rsa

from salesforce.auth import (
    SalesforceAuthError,
    build_assertion,
    get_access_token,
    load_credentials,
)
from tests.fake_salesforce import FakeSalesforce, _b64url_decode


def test_assertion_is_a_valid_rs256_jwt(
    sf_creds: Any, rsa_keypair: tuple[rsa.PublicKey, rsa.PrivateKey]
) -> None:
    public_key, _ = rsa_keypair
    assertion = build_assertion(sf_creds, now=datetime(2026, 7, 30, tzinfo=UTC))
    signing_input, _, signature = assertion.rpartition(".")
    assert rsa.verify(signing_input.encode(), _b64url_decode(signature), public_key) == "SHA-256"
    header = json.loads(_b64url_decode(signing_input.split(".")[0]))
    claims = json.loads(_b64url_decode(signing_input.split(".")[1]))
    assert header == {"alg": "RS256"}
    assert claims["iss"] == sf_creds.client_id
    assert claims["sub"] == sf_creds.username
    assert claims["aud"] == sf_creds.login_url
    assert claims["exp"] == int(datetime(2026, 7, 30, tzinfo=UTC).timestamp()) + 300


def test_get_access_token_via_fake_token_endpoint(sf_creds: Any, sf_env: Any) -> None:
    token, instance_url = get_access_token(sf_creds)
    assert token == "FAKE_TOKEN"
    assert instance_url == sf_env.sf.instance_url
    # the fake verified the signature and claims; grant was jwt-bearer only
    assert len(sf_env.sf.token_claims) == 1


def test_default_now_produces_future_expiry(sf_creds: Any) -> None:
    assertion = build_assertion(sf_creds)
    claims = json.loads(_b64url_decode(assertion.split(".")[1]))
    assert claims["exp"] > datetime.now(UTC).timestamp()


def test_token_endpoint_error_raises(sf_creds: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    private = rsa.PrivateKey.load_pkcs1(sf_creds.private_key_pem.encode())
    fake = FakeSalesforce(
        public_key=rsa.PublicKey(private.n, private.e),
        client_id=sf_creds.client_id,
        username=sf_creds.username,
        reject_statuses=[400],
    )
    monkeypatch.setattr("salesforce.http.transport", fake)
    with pytest.raises(SalesforceAuthError, match="400"):
        get_access_token(sf_creds)


def test_load_credentials_from_secrets_manager(sf_creds: Any, sf_env: Any) -> None:
    creds = load_credentials("outreach/salesforce/jwt", sf_env.boto_clients["secretsmanager"])
    assert creds == sf_creds


def test_credentials_contain_no_password_material(sf_creds: Any) -> None:
    fields = set(sf_creds.__dataclass_fields__)
    assert fields == {"client_id", "username", "login_url", "private_key_pem"}
