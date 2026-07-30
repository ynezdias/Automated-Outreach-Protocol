"""In-memory fake of the Salesforce endpoints the sync layer uses.

Implements the OAuth JWT token endpoint (with real RS256 signature
verification), Bulk API 2.0 ingest (create job, upload batch, close, poll,
failedResults), and the query endpoint — enough to run the sync end to end
with true upsert semantics keyed on Outreach_External_Id__c.

A row without a Company value fails with REQUIRED_FIELD_MISSING, mirroring the
real Lead required-field behavior.
"""

import csv
import io
import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote

import rsa

from salesforce.http import HttpRequest, HttpResponse

API = "/services/data/v63.0"
MISSING_COMPANY_ERROR = "REQUIRED_FIELD_MISSING:Required fields are missing: [Company]:--"


def _b64url_decode(data: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class FakeSalesforce:
    def __init__(
        self,
        *,
        public_key: rsa.PublicKey,
        client_id: str,
        username: str,
        login_url: str = "https://login.example",
        instance_url: str = "https://org.example",
        poll_delays: int = 0,
        job_error: str | None = None,
        reject_statuses: list[int] | None = None,
    ) -> None:
        self.public_key = public_key
        self.client_id = client_id
        self.username = username
        self.login_url = login_url
        self.instance_url = instance_url
        self.poll_delays = poll_delays
        self.job_error = job_error
        self.reject_statuses = list(reject_statuses or [])
        self.leads: dict[str, dict[str, str]] = {}
        self.latest_inbound: str | None = None
        self.jobs: dict[str, dict[str, Any]] = {}
        self.token_claims: list[dict[str, Any]] = []
        self.api_calls = 0

    # --- transport --------------------------------------------------------------

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.api_calls += 1
        if self.reject_statuses:
            return self._respond(self.reject_statuses.pop(0), {"error": "throttled"})
        if request.url == f"{self.login_url}/services/oauth2/token":
            return self._token(request)
        assert request.headers.get("Authorization") == "Bearer FAKE_TOKEN", "missing bearer token"
        path = request.url.removeprefix(self.instance_url)
        if path == f"{API}/jobs/ingest" and request.method == "POST":
            return self._create_job(request)
        match = re.fullmatch(rf"{re.escape(API)}/jobs/ingest/(\w+)(/batches|/failedResults)?", path)
        if match:
            return self._job_endpoint(request, match.group(1), match.group(2))
        if path.startswith(f"{API}/query"):
            return self._query(path)
        raise AssertionError(f"unexpected request: {request.method} {path}")

    # --- endpoints --------------------------------------------------------------

    def _token(self, request: HttpRequest) -> HttpResponse:
        assert request.body is not None
        params = parse_qs(request.body.decode())
        assert params["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
        assertion = params["assertion"][0]
        signing_input, _, signature = assertion.rpartition(".")
        rsa.verify(signing_input.encode(), _b64url_decode(signature), self.public_key)
        claims = json.loads(_b64url_decode(signing_input.split(".")[1]))
        assert claims["iss"] == self.client_id
        assert claims["sub"] == self.username
        assert claims["aud"] == self.login_url
        self.token_claims.append(claims)
        return self._respond(200, {"access_token": "FAKE_TOKEN", "instance_url": self.instance_url})

    def _create_job(self, request: HttpRequest) -> HttpResponse:
        assert request.body is not None
        spec = json.loads(request.body)
        assert spec["operation"] == "upsert"
        assert spec["externalIdFieldName"] == "Outreach_External_Id__c"
        job_id = f"750{len(self.jobs):05d}"
        self.jobs[job_id] = {
            "id": job_id,
            "state": "Open",
            "object": spec["object"],
            "csv": "",
            "polls_left": self.poll_delays,
            "numberRecordsProcessed": 0,
            "numberRecordsFailed": 0,
            "failed_rows": [],
        }
        return self._respond(200, {"id": job_id, "state": "Open"})

    def _job_endpoint(self, request: HttpRequest, job_id: str, sub: str | None) -> HttpResponse:
        job = self.jobs[job_id]
        if sub == "/batches":
            assert request.method == "PUT" and job["state"] == "Open"
            assert request.body is not None
            job["csv"] = request.body.decode()
            return self._respond(201, {})
        if sub == "/failedResults":
            body = io.StringIO()
            fields = ["sf__Id", "sf__Error", *self._columns(job)]
            writer = csv.DictWriter(body, fieldnames=fields)
            writer.writeheader()
            writer.writerows(job["failed_rows"])
            return HttpResponse(200, {"Sforce-Limit-Info": self._usage()}, body.getvalue().encode())
        if request.method == "PATCH":
            assert (
                request.body is not None and json.loads(request.body)["state"] == "UploadComplete"
            )
            self._process(job)
            return self._respond(200, {"id": job_id, "state": job["state"]})
        # GET job info (poll)
        if job["state"] == "Processing" and job["polls_left"] > 0:
            job["polls_left"] -= 1
        elif job["state"] == "Processing":
            job["state"] = "JobComplete"
        return self._respond(
            200,
            {
                "id": job_id,
                "state": job["state"],
                "errorMessage": self.job_error,
                "numberRecordsProcessed": job["numberRecordsProcessed"],
                "numberRecordsFailed": job["numberRecordsFailed"],
            },
        )

    def _process(self, job: dict[str, Any]) -> None:
        if self.job_error is not None:
            job["state"] = "Failed"
            return
        ok = 0
        for row in csv.DictReader(io.StringIO(job["csv"])):
            if not row.get("Company"):
                job["failed_rows"].append({"sf__Id": "", "sf__Error": MISSING_COMPANY_ERROR, **row})
            else:
                self.leads[row["Outreach_External_Id__c"]] = dict(row)
                ok += 1
        job["numberRecordsProcessed"] = ok + len(job["failed_rows"])
        job["numberRecordsFailed"] = len(job["failed_rows"])
        job["state"] = "Processing" if job["polls_left"] else "JobComplete"

    def _query(self, path: str) -> HttpResponse:
        soql = unquote(path.split("?q=", 1)[1].replace("+", " "))
        if "FROM Outreach_Message__c" in soql:
            assert "Direction__c = 'Inbound'" in soql
            records = (
                []
                if self.latest_inbound is None
                else [
                    {
                        "attributes": {"type": "Outreach_Message__c"},
                        "CreatedDate": self.latest_inbound,
                    }
                ]
            )
            return self._respond(200, {"totalSize": len(records), "done": True, "records": records})
        assert soql.upper().startswith("SELECT COUNT()")
        return self._respond(200, {"totalSize": len(self.leads), "done": True})

    # --- helpers ----------------------------------------------------------------

    def _columns(self, job: dict[str, Any]) -> list[str]:
        reader = csv.reader(io.StringIO(job["csv"]))
        return next(reader, [])

    def _usage(self) -> str:
        return f"api-usage={self.api_calls}/15000"

    def _respond(self, status: int, payload: dict[str, Any]) -> HttpResponse:
        return HttpResponse(
            status, {"Sforce-Limit-Info": self._usage()}, json.dumps(payload).encode()
        )
