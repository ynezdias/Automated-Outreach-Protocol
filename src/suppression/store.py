"""Suppression list backed by an append-only S3 event log.

Source of truth: one immutable JSON object per suppression event under
``suppression/events/``. S3 has no append, so "append-only" means every write
creates a new object with a unique key — concurrent writers can never clobber
each other. The Parquet snapshot and the in-memory query cache are both derived
from the log and can always be rebuilt from it.

Freshness: reads go through a cache refreshed from S3 whenever it is older than
``REFRESH_TTL_SECONDS`` (< 60, the compliance bound). A writer sees its own
writes immediately.

Fail-closed: an identifier that is provided but cannot be normalized cannot be
checked against the list, so it is reported as suppressed (never fail open on a
compliance control).
"""

import io
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import boto3
import polars as pl

from cleansing.normalize import normalize_email, normalize_phone

#: Cache staleness bound. Must stay below the 60-second compliance requirement.
REFRESH_TTL_SECONDS: float = 30.0

EVENTS_PREFIX = "suppression/events/"
SNAPSHOT_KEY = "suppression/optout/current.parquet"

_SNAPSHOT_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "identifier": pl.String,
    "kind": pl.String,
    "reason": pl.String,
    "source": pl.String,
    "occurred_at": pl.Datetime(time_unit="us", time_zone="UTC"),
}


@dataclass(frozen=True)
class SuppressionMatch:
    """A suppression hit, with enough context to reconstruct why a send was blocked."""

    identifier: str
    kind: str
    reason: str
    source: str
    occurred_at: datetime


@dataclass(frozen=True)
class SuppressionResult:
    """Outcome of a suppression check.

    ``suppressed`` applies to the contact across every channel: an SMS opt-out
    suppresses email too. ``indeterminate_identifiers`` lists provided
    identifiers that could not be normalized — these fail closed and make the
    result suppressed.
    """

    suppressed: bool
    matches: tuple[SuppressionMatch, ...]
    indeterminate_identifiers: tuple[str, ...]


class SuppressionStore:
    def __init__(
        self,
        bucket: str,
        *,
        s3_client: Any | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bucket = bucket
        self._s3 = s3_client if s3_client is not None else boto3.client("s3")
        self._monotonic = monotonic
        self._cache: dict[str, SuppressionMatch] = {}
        self._cache_loaded_at: float | None = None

    def add_suppression(
        self, identifier: str, reason: str, source: str, occurred_at: datetime
    ) -> None:
        """Append a suppression event. Raises ValueError on inputs that need a human."""
        if occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        kind = "email" if "@" in identifier else "phone"
        normalized = normalize_email(identifier) if kind == "email" else normalize_phone(identifier)
        if normalized is None:
            raise ValueError(f"cannot normalize {kind} identifier {identifier!r}")
        event = {
            "schema_version": 1,
            "event_id": uuid.uuid4().hex,
            "identifier": normalized,
            "identifier_raw": identifier,
            "kind": kind,
            "reason": reason,
            "source": source,
            "occurred_at": occurred_at.isoformat(),
        }
        self._s3.put_object(
            Bucket=self._bucket,
            Key=f"{EVENTS_PREFIX}{event['event_id']}.json",
            Body=json.dumps(event).encode(),
            ContentType="application/json",
        )
        match = _to_match(event)
        existing = self._cache.get(normalized)
        if existing is None or match.occurred_at < existing.occurred_at:
            self._cache[normalized] = match

    def is_suppressed(self, phone: str | None, email: str | None) -> SuppressionResult:
        """Check a contact across channels. Any hit suppresses every channel."""
        if phone is None and email is None:
            raise ValueError("at least one of phone or email is required")
        self._refresh_if_stale()
        matches: list[SuppressionMatch] = []
        indeterminate: list[str] = []
        for raw, normalized in (
            (phone, normalize_phone(phone)),
            (email, normalize_email(email)),
        ):
            if raw is None:
                continue
            if normalized is None:
                indeterminate.append(raw)
            elif normalized in self._cache:
                matches.append(self._cache[normalized])
        return SuppressionResult(
            suppressed=bool(matches) or bool(indeterminate),
            matches=tuple(matches),
            indeterminate_identifiers=tuple(indeterminate),
        )

    def export_snapshot(self) -> str:
        """Derive the current opt-out set from the event log and write it as Parquet."""
        current = _current_set(self._load_events())
        rows = sorted(current.values(), key=lambda m: m.identifier)
        frame = pl.DataFrame(
            {
                "identifier": [m.identifier for m in rows],
                "kind": [m.kind for m in rows],
                "reason": [m.reason for m in rows],
                "source": [m.source for m in rows],
                "occurred_at": [m.occurred_at for m in rows],
            },
            schema=_SNAPSHOT_SCHEMA,
        )
        buffer = io.BytesIO()
        frame.write_parquet(buffer)
        self._s3.put_object(Bucket=self._bucket, Key=SNAPSHOT_KEY, Body=buffer.getvalue())
        return SNAPSHOT_KEY

    def _refresh_if_stale(self) -> None:
        now = self._monotonic()
        if self._cache_loaded_at is not None and now - self._cache_loaded_at < REFRESH_TTL_SECONDS:
            return
        self._cache = _current_set(self._load_events())
        self._cache_loaded_at = now

    def _load_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": EVENTS_PREFIX}
            if token is not None:
                kwargs["ContinuationToken"] = token
            response = self._s3.list_objects_v2(**kwargs)
            for entry in response.get("Contents", []):
                body = self._s3.get_object(Bucket=self._bucket, Key=entry["Key"])["Body"]
                events.append(json.loads(body.read()))
            if not response.get("IsTruncated"):
                return events
            token = response["NextContinuationToken"]


def _to_match(event: dict[str, Any]) -> SuppressionMatch:
    return SuppressionMatch(
        identifier=event["identifier"],
        kind=event["kind"],
        reason=event["reason"],
        source=event["source"],
        occurred_at=datetime.fromisoformat(event["occurred_at"]),
    )


def _current_set(events: list[dict[str, Any]]) -> dict[str, SuppressionMatch]:
    """Reduce the event log to one entry per identifier, keeping the earliest opt-out."""
    current: dict[str, SuppressionMatch] = {}
    for event in events:
        match = _to_match(event)
        existing = current.get(match.identifier)
        if existing is None or match.occurred_at < existing.occurred_at:
            current[match.identifier] = match
    return current
