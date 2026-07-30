"""Test session setup: ensure jsii can find a Node.js runtime.

CDK's jsii kernel spawns ``node``. On machines without a system Node.js install,
point jsii (via JSII_NODE) at the node binary bundled by nodejs-wheel-binaries.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
import rsa as rsa_lib


def _ensure_node_runtime() -> None:
    if shutil.which("node") or os.environ.get("JSII_NODE"):
        return
    import nodejs_wheel

    root = Path(nodejs_wheel.__file__).parent
    for candidate in (root / "node.exe", root / "bin" / "node"):
        if candidate.exists():
            os.environ["JSII_NODE"] = str(candidate)
            return


_ensure_node_runtime()


@pytest.fixture(scope="session")
def rsa_keypair() -> tuple[rsa_lib.PublicKey, rsa_lib.PrivateKey]:
    # 1024-bit keeps pure-python keygen fast; test-only, never a real key size.
    return rsa_lib.newkeys(1024)


@pytest.fixture
def sf_creds(rsa_keypair: tuple[rsa_lib.PublicKey, rsa_lib.PrivateKey]) -> Any:
    from salesforce.auth import SalesforceCredentials

    _, private_key = rsa_keypair
    return SalesforceCredentials(
        client_id="3MVG9fake",
        username="integration@fundmatellc.com.scratch",
        login_url="https://login.example",
        private_key_pem=private_key.save_pkcs1().decode(),
    )


class FakeSecretsManager:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
        return {"SecretString": self._secrets[SecretId]}


class FakeCloudWatch:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def put_metric_data(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def metric(self, name: str) -> Any:
        for call in self.calls:
            for datum in call["MetricData"]:
                if datum["MetricName"] == name:
                    return datum["Value"]
        return None


@pytest.fixture
def sf_env(sf_creds: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Wire fakes for transport, S3, Secrets Manager, and CloudWatch."""
    from tests.fake_s3 import FakeS3Client
    from tests.fake_salesforce import FakeSalesforce

    fake_sf = FakeSalesforce(
        public_key=_public_key_for(sf_creds),
        client_id=sf_creds.client_id,
        username=sf_creds.username,
        login_url=sf_creds.login_url,
    )
    fake_s3 = FakeS3Client()
    cloudwatch = FakeCloudWatch()
    secrets = FakeSecretsManager(
        {
            "outreach/salesforce/jwt": json.dumps(
                {
                    "client_id": sf_creds.client_id,
                    "username": sf_creds.username,
                    "login_url": sf_creds.login_url,
                    "private_key": sf_creds.private_key_pem,
                }
            )
        }
    )
    clients = {"s3": fake_s3, "secretsmanager": secrets, "cloudwatch": cloudwatch}
    monkeypatch.setattr("salesforce.http.transport", fake_sf)
    monkeypatch.setattr("cleansing.pipeline_io.boto3.client", lambda service: clients[service])
    monkeypatch.setattr("salesforce.sync.boto3.client", lambda service: clients[service])
    monkeypatch.setattr("salesforce.reconcile.boto3.client", lambda service: clients[service])
    monkeypatch.setenv("SALESFORCE_SECRET_ID", "outreach/salesforce/jwt")
    monkeypatch.setenv("DATA_BUCKET", "outreach-data")

    class Env:
        sf = fake_sf
        s3 = fake_s3
        cw = cloudwatch
        boto_clients = clients

    return Env


def _public_key_for(creds: Any) -> rsa_lib.PublicKey:
    private = rsa_lib.PrivateKey.load_pkcs1(creds.private_key_pem.encode())
    return rsa_lib.PublicKey(private.n, private.e)


@pytest.fixture
def tt_env(sf_env: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """sf_env plus a FakeTextTorrent wired as the HTTP transport."""
    from tests.fake_texttorrent import API_SID, PUBLIC_KEY, FakeTextTorrent

    fake = FakeTextTorrent()
    monkeypatch.setattr("salesforce.http.transport", fake)
    sf_env.boto_clients["secretsmanager"]._secrets["outreach/texttorrent"] = json.dumps(
        {"api_sid": API_SID, "public_key": PUBLIC_KEY}
    )
    monkeypatch.setenv("TEXTTORRENT_SECRET_ID", "outreach/texttorrent")
    monkeypatch.setenv("TEXTTORRENT_CANARY_TO", "+16505550100")
    sf_env.tt = fake
    return sf_env
