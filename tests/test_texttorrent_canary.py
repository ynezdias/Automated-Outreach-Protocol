"""Canary tests: the stored message must be byte-identical to what we sent.

A vendor can silently re-enable an AI rewriter in an update; the canary's odd
punctuation and unusual phrasing is exactly the text such a feature would
"improve", so any mutation flips the metric the same day.
"""

from typing import Any

import pytest

from texttorrent.canary import CANARY_BODY_TEMPLATE, handler
from texttorrent.client import TextTorrentApiError


def test_canary_body_contains_unusual_phrasing_and_punctuation() -> None:
    body = CANARY_BODY_TEMPLATE.format(nonce="X")
    for oddity in ("—", "…", "''", '"', ";:", "!!", "~", "[", "{", "mIxEd", "\n"):
        assert oddity in body, f"canary must contain {oddity!r} to tempt a rewriter"


def test_untouched_send_passes_byte_identity(tt_env: Any) -> None:
    out = handler({"nonce": "run-1"})
    assert out["identical"] is True
    assert out["delivered"] == out["submitted"]
    assert out["submitted"].encode() == tt_env.tt.messages[out["message_id"]].encode()
    assert tt_env.cw.metric("CanaryByteIdentical") == 1.0


def test_rewriter_mutation_is_detected(tt_env: Any) -> None:
    # A "helpful" rewriter: normalizes dashes, straightens quotes, tidies !!
    tt_env.tt.rewrite_hook = lambda body: (
        body.replace("—", "-").replace("!!", "!").replace("''", "'")
    )
    out = handler({"nonce": "run-2"})
    assert out["identical"] is False
    assert out["delivered"] != out["submitted"]
    assert tt_env.cw.metric("CanaryByteIdentical") == 0.0


def test_second_run_reuses_the_existing_chat(tt_env: Any) -> None:
    first = handler({"nonce": "run-1"})
    second = handler({"nonce": "run-2"})
    assert second["chat_id"] == first["chat_id"]
    assert len(tt_env.tt.chats) == 1


def test_default_nonce_is_generated(tt_env: Any) -> None:
    out = handler({})
    assert out["identical"] is True
    assert out["submitted"] != CANARY_BODY_TEMPLATE  # nonce interpolated


def test_env_sender_override_wins(tt_env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXTTORRENT_FROM_NUMBER", "+16505550199")
    out = handler({"nonce": "run-1"})
    assert tt_env.tt.chats[out["chat_id"]]["sender"] == "+16505550199"


def test_blacklisted_canary_recipient_fails_loudly(tt_env: Any) -> None:
    # If the canary target ever opts out, the canary must fail, not silently pass.
    tt_env.tt.opt_outs = ["+16505550100"]
    with pytest.raises(TextTorrentApiError):
        handler({"nonce": "run-1"})


def test_vendor_5xx_raises_loudly(tt_env: Any) -> None:
    tt_env.tt.reject_statuses = [503]
    with pytest.raises(TextTorrentApiError) as excinfo:
        handler({"nonce": "run-3"})
    assert excinfo.value.status == 503


def test_readback_miss_is_a_failure(tt_env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "texttorrent.client.TextTorrentClient.chat_messages",
        lambda self, chat_id, limit=20: [],
    )
    out = handler({"nonce": "run-1"})
    assert out["identical"] is False
    assert tt_env.cw.metric("CanaryByteIdentical") == 0.0


def test_unresolvable_chat_id_raises(tt_env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    handler({"nonce": "seed"})  # chat now exists, so the next create returns None
    monkeypatch.setattr(
        "texttorrent.client.TextTorrentClient.find_chat_id", lambda self, number: None
    )
    with pytest.raises(TextTorrentApiError):
        handler({"nonce": "run-2"})
