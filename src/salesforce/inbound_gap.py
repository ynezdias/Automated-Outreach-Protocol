"""Inbound-freshness detector: pages when replies stop landing in Salesforce.

TextTorrent's API documents no webhooks and no delivery callbacks (see
docs/PROVIDER.md), so whatever transport carries inbound replies — a webhook
that silently 403s every request, a poller that died, or a provider outage —
the failure looks the same from outside: inbound ``Outreach_Message__c`` rows
stop appearing. This Lambda measures exactly that, at the system of record.

Event:  ``{}`` (scheduled every 15 minutes); optionally ``{"as_of": "<ISO>"}``
        for replay and tests.
Env:    ``SALESFORCE_SECRET_ID``; optional ``BUSINESS_TIMEZONE`` (default
        ``America/New_York``), ``BUSINESS_START_HOUR`` (default 8) and
        ``BUSINESS_END_HOUR`` (default 21).
Metric: ``Outreach/Inbound InboundGapBusinessSeconds`` — business seconds
        elapsed since the newest inbound message was created.

The observability stack alarms at >= 4 business hours. Business hours are
Mon-Fri within the configured window (defaults match the send window), so the
alarm cannot fire over a quiet night or weekend. If no inbound message has
ever existed, no metric is emitted — the alarm treats missing data as
breaching, which also covers this Lambda itself dying (fail closed).
"""

import os
from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import boto3

from salesforce import http
from salesforce.auth import get_access_token, load_credentials
from salesforce.http import HttpRequest

METRIC_NAMESPACE = "Outreach/Inbound"
METRIC_NAME = "InboundGapBusinessSeconds"
LATEST_INBOUND_QUERY = (
    "SELECT CreatedDate FROM Outreach_Message__c "
    "WHERE Direction__c = 'Inbound' ORDER BY CreatedDate DESC LIMIT 1"
)


class InboundGapError(Exception):
    pass


def business_seconds(
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    start_hour: int = 8,
    end_hour: int = 21,
) -> float:
    """Seconds of Mon-Fri ``start_hour``-``end_hour`` wall time between two instants."""
    if end <= start:
        return 0.0
    start_local = start.astimezone(tz)
    end_local = end.astimezone(tz)
    total = 0.0
    day = start_local.date()
    while day <= end_local.date():
        if day.weekday() < 5:  # Monday..Friday
            opens = datetime.combine(day, time(start_hour), tzinfo=tz)
            closes = datetime.combine(day, time(end_hour), tzinfo=tz)
            low = max(start_local, opens)
            high = min(end_local, closes)
            if high > low:
                total += (high - low).total_seconds()
        day += timedelta(days=1)
    return total


def latest_inbound_at(access_token: str, instance_url: str) -> datetime | None:
    """CreatedDate of the newest inbound Outreach_Message__c, or None if none exists."""
    response = http.transport(
        HttpRequest(
            "GET",
            f"{instance_url}/services/data/v63.0/query?q={quote(LATEST_INBOUND_QUERY)}",
            {"Authorization": f"Bearer {access_token}"},
        )
    )
    if response.status != 200:
        raise InboundGapError(f"inbound query failed: {response.status} {response.body!r}")
    records = response.json()["records"]
    if not records:
        return None
    return datetime.fromisoformat(records[0]["CreatedDate"])


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    as_of = (
        datetime.fromisoformat(event["as_of"]) if "as_of" in event else datetime.now(UTC)
    )
    creds = load_credentials(os.environ["SALESFORCE_SECRET_ID"], boto3.client("secretsmanager"))
    access_token, instance_url = get_access_token(creds)
    last_inbound = latest_inbound_at(access_token, instance_url)
    if last_inbound is None:
        # Never emitting the metric makes the alarm breach on missing data.
        return {"inbound_ever": False, "gap_business_seconds": None}
    gap = business_seconds(
        last_inbound,
        as_of,
        ZoneInfo(os.environ.get("BUSINESS_TIMEZONE", "America/New_York")),
        start_hour=int(os.environ.get("BUSINESS_START_HOUR", "8")),
        end_hour=int(os.environ.get("BUSINESS_END_HOUR", "21")),
    )
    boto3.client("cloudwatch").put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=[{"MetricName": METRIC_NAME, "Value": gap, "Unit": "Seconds"}],
    )
    return {"inbound_ever": True, "gap_business_seconds": gap}
