"""Nightly opt-out reconciliation: our suppression list vs TextTorrent's (Conflict B).

Our suppression store (ADR-009) is authoritative; the vendor's blocked list is
a backstop. The two WILL diverge — their opt-out-word engine auto-blocks
replies our pipeline may have missed, and our multi-channel suppressions never
reach them on their own. This job closes both gaps nightly and alarms on ANY
divergence: zero is the only acceptable number.

- In theirs, not ours  -> add to our store immediately (we missed an opt-out).
- In ours, not theirs  -> push to their blocked list.
- Unparseable vendor entry -> counted as divergence (a human must look).

Event:  ``{}`` (scheduled nightly); or ``{"export_key": "<s3 key>"}`` to
        reconcile against a dropped vendor CSV export instead of the API.
Env:    ``DATA_BUCKET``, ``TEXTTORRENT_SECRET_ID``.
Metrics (``Outreach/Suppression``): ``OptOutDivergence`` (alarmed at > 0),
        ``OptOutsAddedFromVendor``, ``OptOutsPushedToVendor``.
"""

import csv
import io
import os
from datetime import UTC, datetime
from typing import Any

import boto3

from cleansing.normalize import normalize_phone
from suppression import SuppressionStore
from texttorrent.client import TextTorrentClient, load_credentials

METRIC_NAMESPACE = "Outreach/Suppression"


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    bucket = os.environ["DATA_BUCKET"]
    s3 = boto3.client("s3")
    store = SuppressionStore(bucket, s3_client=s3)
    client = TextTorrentClient(
        load_credentials(os.environ["TEXTTORRENT_SECRET_ID"], boto3.client("secretsmanager"))
    )

    if "export_key" in event:
        raw_vendor = _numbers_from_export(s3, bucket, str(event["export_key"]))
    else:
        raw_vendor = client.blocked_numbers()

    vendor: set[str] = set()
    unparseable: list[str] = []
    for raw in raw_vendor:
        normalized = normalize_phone(raw)
        if normalized is None:
            unparseable.append(raw)
        else:
            vendor.add(normalized)

    ours_phones = {i for i in store.current_identifiers() if "@" not in i}

    added_from_vendor = sorted(vendor - ours_phones)
    now = datetime.now(UTC)
    for number in added_from_vendor:
        store.add_suppression(
            number,
            reason="present on TextTorrent blocked list",
            source="texttorrent_reconciliation",
            occurred_at=now,
        )
    pushed_to_vendor = sorted(ours_phones - vendor)
    client.block_numbers(pushed_to_vendor)

    divergence = len(added_from_vendor) + len(pushed_to_vendor) + len(unparseable)
    boto3.client("cloudwatch").put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=[
            {"MetricName": "OptOutDivergence", "Value": float(divergence), "Unit": "Count"},
            {
                "MetricName": "OptOutsAddedFromVendor",
                "Value": float(len(added_from_vendor)),
                "Unit": "Count",
            },
            {
                "MetricName": "OptOutsPushedToVendor",
                "Value": float(len(pushed_to_vendor)),
                "Unit": "Count",
            },
        ],
    )
    return {
        "divergence": divergence,
        "added_from_vendor": added_from_vendor,
        "pushed_to_vendor": pushed_to_vendor,
        "unparseable": unparseable,
    }


def _numbers_from_export(s3: Any, bucket: str, key: str) -> list[str]:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode()
    reader = csv.DictReader(io.StringIO(body))
    fields = reader.fieldnames or [""]
    column = "phone" if "phone" in fields else fields[0]
    return [row[column] for row in reader if row.get(column)]
