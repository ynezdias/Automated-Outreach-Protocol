"""OAuth 2.0 JWT Bearer Flow against the Connected App.

Every invocation mints a short-lived RS256 JWT signed with the private key
stored in Secrets Manager and exchanges it for an access token. No
username/password flow exists here and no refresh tokens are ever stored.

Secret JSON shape (Secrets Manager)::

    {"client_id": "<consumer key>", "username": "<integration user>",
     "login_url": "https://login.salesforce.com", "private_key": "<PKCS#1 PEM>"}

The key must be in PKCS#1 PEM form (the traditional RSA format, e.g. the
output of ``openssl rsa -traditional``): signing uses the pure-python ``rsa``
package because ``cryptography`` has no Windows-ARM64 wheel (ADR-012).
"""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import rsa

from salesforce import http
from salesforce.http import HttpRequest

JWT_EXPIRY_SECONDS = 300
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"


class SalesforceAuthError(Exception):
    pass


@dataclass(frozen=True)
class SalesforceCredentials:
    client_id: str
    username: str
    login_url: str
    private_key_pem: str


def load_credentials(secret_id: str, secrets_client: Any) -> SalesforceCredentials:
    data = json.loads(secrets_client.get_secret_value(SecretId=secret_id)["SecretString"])
    return SalesforceCredentials(
        client_id=data["client_id"],
        username=data["username"],
        login_url=data["login_url"],
        private_key_pem=data["private_key"],
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def build_assertion(creds: SalesforceCredentials, now: datetime | None = None) -> str:
    """Build the signed RS256 JWT assertion."""
    if now is None:
        now = datetime.now(UTC)
    header = _b64url(json.dumps({"alg": "RS256"}).encode())
    claims = _b64url(
        json.dumps(
            {
                "iss": creds.client_id,
                "sub": creds.username,
                "aud": creds.login_url,
                "exp": int(now.timestamp()) + JWT_EXPIRY_SECONDS,
            }
        ).encode()
    )
    signing_input = f"{header}.{claims}"
    private_key = rsa.PrivateKey.load_pkcs1(creds.private_key_pem.encode())
    signature = rsa.sign(signing_input.encode(), private_key, "SHA-256")
    return f"{signing_input}.{_b64url(signature)}"


def get_access_token(creds: SalesforceCredentials) -> tuple[str, str]:
    """Exchange a JWT assertion for (access_token, instance_url)."""
    body = urlencode({"grant_type": JWT_BEARER_GRANT, "assertion": build_assertion(creds)})
    response = http.transport(
        HttpRequest(
            "POST",
            f"{creds.login_url}/services/oauth2/token",
            {"Content-Type": "application/x-www-form-urlencoded"},
            body.encode(),
        )
    )
    if response.status != 200:
        raise SalesforceAuthError(f"token request failed: {response.status} {response.body!r}")
    payload = response.json()
    return payload["access_token"], payload["instance_url"]
