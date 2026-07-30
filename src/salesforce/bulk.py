"""Bulk API 2.0 client: upsert ingest jobs with retry, backoff, and limit tracking.

Every response's ``Sforce-Limit-Info`` header is captured on
``BulkApiClient.api_usage`` so callers can surface daily API consumption to
CloudWatch. 429/503 responses are retried with exponential backoff (1, 2, 4,
8 seconds; five attempts). Failed jobs and failed records are never swallowed:
jobs that don't reach JobComplete raise, and per-record failures are returned
verbatim for the caller to persist.
"""

import csv
import io
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from salesforce import http
from salesforce.http import HttpRequest, HttpResponse

API_VERSION = "v63.0"
MAX_ATTEMPTS = 5
RETRYABLE_STATUSES = (429, 503)
TERMINAL_JOB_STATES = ("JobComplete", "Failed", "Aborted")


class SalesforceApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status


@dataclass(frozen=True)
class JobResult:
    job_id: str
    state: str
    processed: int
    failed: int
    failed_results_csv: str | None


class BulkApiClient:
    def __init__(
        self,
        instance_url: str,
        access_token: str,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._instance_url = instance_url
        self._access_token = access_token
        self._sleep = sleep
        #: (used, limit) from the most recent Sforce-Limit-Info header.
        self.api_usage: tuple[int, int] | None = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
    ) -> HttpResponse:
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": content_type,
            "Accept": "application/json, text/csv",
        }
        attempt = 0
        while True:
            response = http.transport(
                HttpRequest(method, f"{self._instance_url}{path}", headers, body)
            )
            self._record_usage(response)
            if response.status in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS - 1:
                self._sleep(float(2**attempt))
                attempt += 1
                continue
            if response.status >= 400:
                raise SalesforceApiError(response.status, response.body.decode(errors="replace"))
            return response

    def _record_usage(self, response: HttpResponse) -> None:
        header = response.header("Sforce-Limit-Info")
        if header is not None:
            match = re.search(r"api-usage=(\d+)/(\d+)", header)
            if match is not None:
                self.api_usage = (int(match.group(1)), int(match.group(2)))

    def create_upsert_job(self, object_name: str, external_id_field: str) -> str:
        response = self._request(
            "POST",
            f"/services/data/{API_VERSION}/jobs/ingest",
            body=json.dumps(
                {
                    "object": object_name,
                    "operation": "upsert",
                    "externalIdFieldName": external_id_field,
                    "contentType": "CSV",
                    "lineEnding": "LF",
                }
            ).encode(),
        )
        return str(response.json()["id"])

    def upload_batch(self, job_id: str, csv_data: bytes) -> None:
        self._request(
            "PUT",
            f"/services/data/{API_VERSION}/jobs/ingest/{job_id}/batches",
            body=csv_data,
            content_type="text/csv",
        )

    def close_job(self, job_id: str) -> None:
        self._request(
            "PATCH",
            f"/services/data/{API_VERSION}/jobs/ingest/{job_id}",
            body=b'{"state": "UploadComplete"}',
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/services/data/{API_VERSION}/jobs/ingest/{job_id}")
        return dict(response.json())

    def wait_for_job(
        self, job_id: str, *, poll_seconds: float = 5.0, max_polls: int = 240
    ) -> dict[str, Any]:
        for _ in range(max_polls):
            info = self.get_job(job_id)
            if info["state"] in TERMINAL_JOB_STATES:
                return info
            self._sleep(poll_seconds)
        raise SalesforceApiError(408, f"job {job_id} timed out after {max_polls} polls")

    def failed_results(self, job_id: str) -> str:
        response = self._request(
            "GET", f"/services/data/{API_VERSION}/jobs/ingest/{job_id}/failedResults"
        )
        return response.body.decode()

    def query_count(self, soql: str) -> int:
        response = self._request("GET", f"/services/data/{API_VERSION}/query?q={quote(soql)}")
        return int(response.json()["totalSize"])


def _to_csv(records: list[dict[str, str]]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(records[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return out.getvalue().encode()


def upsert_records(
    client: BulkApiClient,
    records: list[dict[str, str]],
    *,
    external_id_field: str,
    object_name: str = "Lead",
    batch_size: int = 10_000,
) -> list[JobResult]:
    """Upsert in batches of ``batch_size``; one Bulk API job per batch.

    Raises if a job ends in any state other than JobComplete. Per-record
    failures are returned (never dropped) for the caller to persist.
    """
    results: list[JobResult] = []
    for start in range(0, len(records), batch_size):
        chunk = records[start : start + batch_size]
        job_id = client.create_upsert_job(object_name, external_id_field)
        client.upload_batch(job_id, _to_csv(chunk))
        client.close_job(job_id)
        info = client.wait_for_job(job_id)
        if info["state"] != "JobComplete":
            raise SalesforceApiError(
                500, f"job {job_id} ended {info['state']}: {info.get('errorMessage')}"
            )
        failed = int(info.get("numberRecordsFailed", 0))
        results.append(
            JobResult(
                job_id=job_id,
                state=str(info["state"]),
                processed=int(info.get("numberRecordsProcessed", 0)),
                failed=failed,
                failed_results_csv=client.failed_results(job_id) if failed else None,
            )
        )
    return results
