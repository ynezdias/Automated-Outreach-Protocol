"""Tests for the inbound-freshness detector (business-hours math + Lambda).

The detector is the pager for the silent-inbound failure mode: whether the
inbound transport is a webhook that 403s everything or a poller that died,
the observable symptom is identical — no inbound Outreach_Message__c rows.
"""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from salesforce.http import HttpResponse
from salesforce.inbound_gap import (
    InboundGapError,
    business_seconds,
    handler,
    latest_inbound_at,
)

ET = ZoneInfo("America/New_York")


def et(spec: str) -> datetime:
    """Wall-clock Eastern time literal."""
    return datetime.fromisoformat(spec).replace(tzinfo=ET)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        # Entirely within one business day (Tue).
        ("2026-07-28T10:00:00", "2026-07-28T12:00:00", 7200),
        # Starts before opening: only 08:00-09:00 counts.
        ("2026-07-28T05:00:00", "2026-07-28T09:00:00", 3600),
        # Overnight: Tue 20:00-21:00 plus Wed 08:00-09:00.
        ("2026-07-28T20:00:00", "2026-07-29T09:00:00", 7200),
        # Weekend gap: Fri 20:30-21:00 plus Mon 08:00-08:30.
        ("2026-07-24T20:30:00", "2026-07-27T08:30:00", 3600),
        # Entirely inside a weekend.
        ("2026-07-25T10:00:00", "2026-07-26T15:00:00", 0),
        # Entirely after hours on a single weekday.
        ("2026-07-28T21:30:00", "2026-07-28T22:30:00", 0),
        # Spans the DST spring-forward weekend (Sun 2026-03-08). Wall-clock
        # business hours are unaffected: US transitions land on weekends.
        ("2026-03-06T20:00:00", "2026-03-09T09:00:00", 7200),
    ],
)
def test_business_seconds(start: str, end: str, expected: int) -> None:
    assert business_seconds(et(start), et(end), ET) == expected


def test_business_seconds_zero_when_end_not_after_start() -> None:
    noon = et("2026-07-28T12:00:00")
    assert business_seconds(noon, noon, ET) == 0
    assert business_seconds(noon, et("2026-07-28T11:00:00"), ET) == 0


def test_business_seconds_converts_from_other_zones() -> None:
    start = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)  # 14:00 ET
    end = datetime(2026, 7, 28, 20, 0, tzinfo=UTC)  # 16:00 ET
    assert business_seconds(start, end, ET) == 7200


def test_business_seconds_custom_hours() -> None:
    start = et("2026-07-28T07:00:00")
    end = et("2026-07-28T10:00:00")
    assert business_seconds(start, end, ET, start_hour=9, end_hour=17) == 3600


def test_handler_emits_business_gap_metric(sf_env: Any) -> None:
    # Newest inbound at 10:00 ET, evaluated at 14:00 ET -> 4h business gap,
    # parsed from Salesforce's native "+0000" CreatedDate format.
    sf_env.sf.latest_inbound = "2026-07-28T14:00:00.000+0000"
    result = handler({"as_of": "2026-07-28T18:00:00+00:00"})
    assert result == {"inbound_ever": True, "gap_business_seconds": 14400.0}
    assert sf_env.cw.metric("InboundGapBusinessSeconds") == 14400.0


def test_handler_without_any_inbound_emits_nothing(sf_env: Any) -> None:
    # No inbound row has ever existed: emit no metric at all, so the alarm's
    # missing-data-is-breaching setting pages (fail closed, never quietly green).
    result = handler({})
    assert result == {"inbound_ever": False, "gap_business_seconds": None}
    assert sf_env.cw.metric("InboundGapBusinessSeconds") is None


def test_latest_inbound_query_failure_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "salesforce.http.transport", lambda request: HttpResponse(500, {}, b"boom")
    )
    with pytest.raises(InboundGapError):
        latest_inbound_at("TOKEN", "https://org.example")
