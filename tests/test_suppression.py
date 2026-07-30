"""Tests for the suppression list — a compliance control.

Written before the implementation. Covers: cross-format phone matching,
cross-channel suppression, plus-addressing, unicode homoglyphs, whitespace,
null inputs, fail-closed behavior on unnormalizable identifiers, the 60-second
freshness bound, concurrent writes, the append-only event log, and the derived
Parquet snapshot.
"""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

import suppression
from suppression import SuppressionResult, SuppressionStore
from suppression.store import EVENTS_PREFIX, REFRESH_TTL_SECONDS, SNAPSHOT_KEY
from tests.fake_s3 import FakeS3Client

BUCKET = "outreach-data"
PHONE = "+16502530000"
OCCURRED = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def s3() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(s3: FakeS3Client, clock: FakeClock) -> SuppressionStore:
    return SuppressionStore(bucket=BUCKET, s3_client=s3, monotonic=clock)


def _add(store: SuppressionStore, identifier: str) -> None:
    store.add_suppression(identifier, reason="STOP reply", source="sms", occurred_at=OCCURRED)


# --- freshness -----------------------------------------------------------------


def test_refresh_ttl_meets_60_second_requirement() -> None:
    assert REFRESH_TTL_SECONDS < 60


def test_writer_sees_own_write_immediately(store: SuppressionStore) -> None:
    assert store.is_suppressed(phone=PHONE, email=None).suppressed is False
    _add(store, PHONE)
    assert store.is_suppressed(phone=PHONE, email=None).suppressed is True


def test_other_reader_sees_write_within_60_seconds(
    s3: FakeS3Client, clock: FakeClock, store: SuppressionStore
) -> None:
    reader = SuppressionStore(bucket=BUCKET, s3_client=s3, monotonic=clock)
    assert reader.is_suppressed(phone=PHONE, email=None).suppressed is False  # cache warmed
    _add(store, PHONE)
    # Still within the reader's cache TTL: staleness is allowed, but only < 60s.
    clock.advance(REFRESH_TTL_SECONDS)
    result = reader.is_suppressed(phone=PHONE, email=None)
    assert result.suppressed is True


def test_fresh_cache_is_not_reloaded(store: SuppressionStore, clock: FakeClock) -> None:
    _add(store, PHONE)
    first = store.is_suppressed(phone=PHONE, email=None)
    clock.advance(1)  # well within TTL — served from cache
    second = store.is_suppressed(phone=PHONE, email=None)
    assert first.suppressed and second.suppressed


# --- matching semantics --------------------------------------------------------


@pytest.mark.parametrize(
    "query_format",
    ["+1 (650) 253-0000", "650-253-0000", "6502530000", "1.650.253.0000", "+16502530000"],
)
def test_optout_matches_same_number_in_any_format(
    store: SuppressionStore, query_format: str
) -> None:
    _add(store, "(650) 253 0000")
    result = store.is_suppressed(phone=query_format, email=None)
    assert result.suppressed is True
    assert result.matches[0].identifier == PHONE


def test_sms_optout_suppresses_email_channel_too(store: SuppressionStore) -> None:
    """Cross-channel: a contact who opted out by SMS must not be emailed either."""
    _add(store, PHONE)
    result = store.is_suppressed(phone=PHONE, email="prospect@example.com")
    assert result.suppressed is True


def test_email_optout_matches_plus_addressed_variants(store: SuppressionStore) -> None:
    store.add_suppression(
        "User+newsletter@Example.com", reason="unsubscribe", source="email", occurred_at=OCCURRED
    )
    assert store.is_suppressed(phone=None, email="user@example.com").suppressed is True
    assert store.is_suppressed(phone=None, email="user+other@example.com").suppressed is True


def test_homoglyph_email_query_matches(store: SuppressionStore) -> None:
    store.add_suppression(
        "user@example.com", reason="unsubscribe", source="email", occurred_at=OCCURRED
    )
    assert (
        store.is_suppressed(phone=None, email="ｕｓｅｒ＠ｅｘａｍｐｌｅ．ｃｏｍ").suppressed is True
    )


def test_whitespace_padded_identifiers_match(store: SuppressionStore) -> None:
    _add(store, "  +1 650 253 0000 ")
    assert store.is_suppressed(phone=" 6502530000\t", email=None).suppressed is True


def test_unknown_identifiers_are_not_suppressed(store: SuppressionStore) -> None:
    result = store.is_suppressed(phone="+16505550100", email="other@example.com")
    assert result == SuppressionResult(suppressed=False, matches=(), indeterminate_identifiers=())


def test_match_carries_reason_source_and_time(store: SuppressionStore) -> None:
    """Reconstructability: a suppression hit reports why, from where, and when."""
    _add(store, PHONE)
    match = store.is_suppressed(phone=PHONE, email=None).matches[0]
    assert (match.identifier, match.reason, match.source) == (PHONE, "STOP reply", "sms")
    assert match.occurred_at == OCCURRED


# --- null and adversarial inputs ----------------------------------------------


def test_both_identifiers_none_raises(store: SuppressionStore) -> None:
    with pytest.raises(ValueError, match="at least one"):
        store.is_suppressed(phone=None, email=None)


def test_single_identifier_queries_are_valid(store: SuppressionStore) -> None:
    assert store.is_suppressed(phone=PHONE, email=None).suppressed is False
    assert store.is_suppressed(phone=None, email="a@b.co").suppressed is False


def test_unnormalizable_phone_fails_closed(store: SuppressionStore) -> None:
    """A phone we cannot normalize cannot be verified against the list → do not send."""
    result = store.is_suppressed(phone="definitely-not-a-number", email=None)
    assert result.suppressed is True
    assert result.matches == ()
    assert result.indeterminate_identifiers == ("definitely-not-a-number",)


def test_unnormalizable_email_fails_closed(store: SuppressionStore) -> None:
    result = store.is_suppressed(phone=None, email="not-an-email")
    assert result.suppressed is True
    assert result.indeterminate_identifiers == ("not-an-email",)


def test_add_unnormalizable_identifier_raises(store: SuppressionStore) -> None:
    with pytest.raises(ValueError, match="normalize"):
        _add(store, "???")


def test_add_naive_datetime_raises(store: SuppressionStore) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        store.add_suppression(
            PHONE,
            reason="STOP",
            source="sms",
            occurred_at=datetime(2026, 7, 1),
        )


# --- event log is the source of truth ------------------------------------------


def test_event_log_is_append_only(store: SuppressionStore, s3: FakeS3Client) -> None:
    """Re-suppressing the same identifier appends a new event; nothing is overwritten."""
    _add(store, PHONE)
    _add(store, PHONE)
    keys = s3.keys(BUCKET, EVENTS_PREFIX)
    assert len(keys) == 2


def test_events_are_valid_json_with_raw_and_normalized(
    store: SuppressionStore, s3: FakeS3Client
) -> None:
    _add(store, "(650) 253-0000")
    [key] = s3.keys(BUCKET, EVENTS_PREFIX)
    event = json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    assert event["identifier_raw"] == "(650) 253-0000"
    assert event["identifier"] == PHONE
    assert event["kind"] == "phone"


def test_earliest_optout_wins_for_duplicate_identifier(s3: FakeS3Client, clock: FakeClock) -> None:
    store = SuppressionStore(bucket=BUCKET, s3_client=s3, monotonic=clock)
    t1, t2, t3 = (OCCURRED + timedelta(days=n) for n in (1, 0, 2))
    for t in (t1, t2, t3):  # arrival order differs from occurrence order
        store.add_suppression(PHONE, reason="STOP", source="sms", occurred_at=t)
    fresh = SuppressionStore(bucket=BUCKET, s3_client=s3, monotonic=clock)
    match = fresh.is_suppressed(phone=PHONE, email=None).matches[0]
    assert match.occurred_at == t2


def test_concurrent_writes_are_all_persisted(store: SuppressionStore, s3: FakeS3Client) -> None:
    numbers = [f"+1650253{n:04d}" for n in range(16)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda p: _add(store, p), numbers))
    assert len(s3.keys(BUCKET, EVENTS_PREFIX)) == 16
    fresh = SuppressionStore(bucket=BUCKET, s3_client=s3, monotonic=FakeClock())
    for number in numbers:
        assert fresh.is_suppressed(phone=number, email=None).suppressed is True


def test_event_listing_paginates(clock: FakeClock) -> None:
    s3 = FakeS3Client(page_size=2)
    store = SuppressionStore(bucket=BUCKET, s3_client=s3, monotonic=clock)
    for n in range(5):
        _add(store, f"+1650253{n:04d}")
    fresh = SuppressionStore(bucket=BUCKET, s3_client=s3, monotonic=clock)
    assert fresh.is_suppressed(phone="+16502530004", email=None).suppressed is True


# --- snapshot is derived -------------------------------------------------------


def _read_snapshot(s3: FakeS3Client) -> pl.DataFrame:
    body = s3.get_object(Bucket=BUCKET, Key=SNAPSHOT_KEY)["Body"].read()
    return pl.read_parquet(io.BytesIO(body))


def test_export_snapshot_writes_current_parquet(store: SuppressionStore, s3: FakeS3Client) -> None:
    _add(store, PHONE)
    store.add_suppression(
        "user+x@example.com", reason="unsubscribe", source="email", occurred_at=OCCURRED
    )
    store.export_snapshot()
    assert SNAPSHOT_KEY == "suppression/optout/current.parquet"
    df = _read_snapshot(s3)
    assert df.columns == ["identifier", "kind", "reason", "source", "occurred_at"]
    assert sorted(df["identifier"].to_list()) == [PHONE, "user@example.com"]


def test_snapshot_deduplicates_identifiers(store: SuppressionStore, s3: FakeS3Client) -> None:
    _add(store, PHONE)
    _add(store, "650 253 0000")
    store.export_snapshot()
    assert _read_snapshot(s3)["identifier"].to_list() == [PHONE]


def test_empty_snapshot_is_written(store: SuppressionStore, s3: FakeS3Client) -> None:
    store.export_snapshot()
    assert _read_snapshot(s3).height == 0


def test_snapshot_is_rederivable_after_new_events(
    store: SuppressionStore, s3: FakeS3Client
) -> None:
    _add(store, PHONE)
    store.export_snapshot()
    assert _read_snapshot(s3).height == 1
    _add(store, "+16502530001")
    store.export_snapshot()
    assert _read_snapshot(s3).height == 2


# --- module-level API ----------------------------------------------------------


def test_module_level_api_via_configure(store: SuppressionStore, s3: FakeS3Client) -> None:
    suppression.configure(store)
    try:
        suppression.add_suppression(PHONE, reason="STOP", source="sms", occurred_at=OCCURRED)
        assert suppression.is_suppressed(phone=PHONE, email=None).suppressed is True
        assert suppression.is_suppressed(phone=None, email="a@b.co").suppressed is False
        suppression.export_snapshot()
        assert SNAPSHOT_KEY in s3.keys(BUCKET)
    finally:
        suppression.configure(None)


def test_module_level_api_builds_store_from_env(
    monkeypatch: pytest.MonkeyPatch, s3: FakeS3Client
) -> None:
    suppression.configure(None)
    monkeypatch.setenv("SUPPRESSION_BUCKET", BUCKET)
    monkeypatch.setattr("suppression.store.boto3.client", lambda service: s3)
    suppression.add_suppression(PHONE, reason="STOP", source="sms", occurred_at=OCCURRED)
    assert suppression.is_suppressed(phone=PHONE, email=None).suppressed is True  # reuses store
    suppression.configure(None)
