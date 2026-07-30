"""Reconciliation tests: our suppression list vs TextTorrent's blocked list.

Zero divergence is the only acceptable number. Anyone in theirs-but-not-ours
means an opt-out happened that our system missed: add immediately and alarm.
"""

from datetime import UTC, datetime
from typing import Any

from suppression import SuppressionStore
from texttorrent.client import TextTorrentClient, load_credentials
from texttorrent.reconcile import handler

BUCKET = "outreach-data"
OCCURRED = datetime(2026, 7, 1, tzinfo=UTC)


def _suppress_locally(env: Any, *identifiers: str) -> None:
    store = SuppressionStore(BUCKET, s3_client=env.s3)
    for identifier in identifiers:
        store.add_suppression(identifier, reason="STOP reply", source="sms", occurred_at=OCCURRED)


def test_in_sync_lists_report_zero_divergence(tt_env: Any) -> None:
    _suppress_locally(tt_env, "+16502530301")
    tt_env.tt.opt_outs = ["+16502530301"]
    out = handler({})
    assert out["divergence"] == 0
    assert out["added_from_vendor"] == []
    assert out["pushed_to_vendor"] == []
    assert tt_env.cw.metric("OptOutDivergence") == 0.0


def test_format_differences_are_not_divergence(tt_env: Any) -> None:
    """Their list in vendor formatting, ours in E.164 — shared normalization."""
    _suppress_locally(tt_env, "+16502530302")
    tt_env.tt.opt_outs = ["(650) 253-0302"]
    assert handler({})["divergence"] == 0


def test_vendor_only_optout_added_immediately_and_alarmed(tt_env: Any) -> None:
    """The acceptance scenario: opted out through TextTorrent only."""
    tt_env.tt.opt_outs = ["+16502530303"]
    out = handler({})
    assert out["added_from_vendor"] == ["+16502530303"]
    assert out["divergence"] == 1
    assert tt_env.cw.metric("OptOutDivergence") == 1.0  # alarm threshold is > 0
    # Added immediately: queryable from a fresh store right now.
    result = SuppressionStore(BUCKET, s3_client=tt_env.s3).is_suppressed(
        phone="+16502530303", email=None
    )
    assert result.suppressed is True
    assert result.matches[0].source == "texttorrent_reconciliation"


def test_local_only_optout_pushed_to_vendor_and_alarmed(tt_env: Any) -> None:
    _suppress_locally(tt_env, "+16502530304")
    out = handler({})
    assert out["pushed_to_vendor"] == ["+16502530304"]
    assert out["divergence"] == 1
    assert tt_env.tt.pushed_numbers == ["6502530304"]  # 10-digit wire format
    assert tt_env.cw.metric("OptOutDivergence") == 1.0


def test_email_suppressions_are_not_pushed_to_sms_vendor(tt_env: Any) -> None:
    _suppress_locally(tt_env, "optout@example.com")
    out = handler({})
    assert out["divergence"] == 0
    assert tt_env.tt.pushed_numbers == []


def test_unparseable_vendor_entry_counts_as_divergence(tt_env: Any) -> None:
    tt_env.tt.opt_outs = ["not-a-number"]
    out = handler({})
    assert out["unparseable"] == ["not-a-number"]
    assert out["divergence"] == 1
    assert tt_env.cw.metric("OptOutDivergence") == 1.0


def test_export_file_mode(tt_env: Any) -> None:
    """If the API pull is unavailable, a dropped CSV export works instead."""
    tt_env.s3.put_object(
        Bucket=BUCKET,
        Key="raw/texttorrent/optouts.csv",
        Body=b"phone\n+16502530305\n(650) 253-0306\n",
    )
    _suppress_locally(tt_env, "+16502530306")
    out = handler({"export_key": "raw/texttorrent/optouts.csv"})
    assert out["added_from_vendor"] == ["+16502530305"]
    assert out["divergence"] == 1


def test_export_without_phone_header_uses_first_column(tt_env: Any) -> None:
    tt_env.s3.put_object(
        Bucket=BUCKET,
        Key="raw/texttorrent/export2.csv",
        Body=b"number,note\n6502530307,x\n,empty-row-skipped\n",
    )
    out = handler({"export_key": "raw/texttorrent/export2.csv"})
    assert out["added_from_vendor"] == ["+16502530307"]


def test_empty_export_file_still_pushes_ours(tt_env: Any) -> None:
    tt_env.s3.put_object(Bucket=BUCKET, Key="raw/texttorrent/empty.csv", Body=b"")
    _suppress_locally(tt_env, "+16502530310")
    out = handler({"export_key": "raw/texttorrent/empty.csv"})
    assert out["pushed_to_vendor"] == ["+16502530310"]


def test_both_directions_in_one_run(tt_env: Any) -> None:
    _suppress_locally(tt_env, "+16502530307")
    tt_env.tt.opt_outs = ["+16502530308"]
    out = handler({})
    assert out["added_from_vendor"] == ["+16502530308"]
    assert out["pushed_to_vendor"] == ["+16502530307"]
    assert out["divergence"] == 2
    assert tt_env.cw.metric("OptOutsAddedFromVendor") == 1.0
    assert tt_env.cw.metric("OptOutsPushedToVendor") == 1.0


def test_blocked_list_pull_paginates(tt_env: Any) -> None:
    tt_env.tt.opt_outs = [f"+1650253{n:04d}" for n in range(5)]
    client = TextTorrentClient(
        load_credentials("outreach/texttorrent", tt_env.boto_clients["secretsmanager"])
    )
    numbers = client.blocked_numbers(page_size=2)
    assert len(numbers) == 5
    assert tt_env.tt.blocked_list_requests == 3  # 2 + 2 + 1, short page ends the loop


def test_store_exposes_current_identifiers(tt_env: Any) -> None:
    _suppress_locally(tt_env, "+16502530309", "user@example.com")
    identifiers = SuppressionStore(BUCKET, s3_client=tt_env.s3).current_identifiers()
    assert {"+16502530309", "user@example.com"} <= identifiers
