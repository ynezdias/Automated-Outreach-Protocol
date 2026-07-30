"""Minimal in-memory fake of the S3 client surface the suppression store uses.

moto is the project convention for AWS mocking, but it hard-depends on
``cryptography``, which has no Windows-ARM64 wheel; this fake stands in for it.
It implements put_object / get_object / list_objects_v2 (with pagination) and is
thread-safe so concurrent-write tests exercise real interleaving.
"""

import io
import threading
from typing import Any


class FakeS3Client:
    def __init__(self, page_size: int = 1000) -> None:
        self._page_size = page_size
        self._objects: dict[tuple[str, str], bytes] = {}
        self._lock = threading.Lock()

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_: Any) -> dict[str, Any]:
        with self._lock:
            self._objects[(Bucket, Key)] = bytes(Body)
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        with self._lock:
            data = self._objects[(Bucket, Key)]
        return {"Body": io.BytesIO(data)}

    def list_objects_v2(
        self,
        *,
        Bucket: str,
        Prefix: str = "",
        ContinuationToken: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        with self._lock:
            keys = sorted(
                key for bucket, key in self._objects if bucket == Bucket and key.startswith(Prefix)
            )
        start = keys.index(ContinuationToken) + 1 if ContinuationToken else 0
        page = keys[start : start + self._page_size]
        response: dict[str, Any] = {
            "IsTruncated": start + self._page_size < len(keys),
        }
        if page:
            response["Contents"] = [{"Key": key} for key in page]
        if response["IsTruncated"]:
            response["NextContinuationToken"] = page[-1]
        return response

    def keys(self, bucket: str, prefix: str = "") -> list[str]:
        with self._lock:
            return sorted(key for b, key in self._objects if b == bucket and key.startswith(prefix))
