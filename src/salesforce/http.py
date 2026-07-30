"""Tiny HTTP transport abstraction.

Everything Salesforce-bound goes through ``transport`` (a callable), so tests
inject a fake and the Lambda uses stdlib urllib — no requests dependency.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)

    def header(self, name: str) -> str | None:
        lowered = {key.lower(): value for key, value in self.headers.items()}
        return lowered.get(name.lower())


def _urllib_transport(request: HttpRequest) -> HttpResponse:  # pragma: no cover - network I/O
    req = urllib.request.Request(
        request.url, data=request.body, headers=request.headers, method=request.method
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return HttpResponse(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as error:
        return HttpResponse(error.code, dict(error.headers), error.read())


transport: Callable[[HttpRequest], HttpResponse] = _urllib_transport
