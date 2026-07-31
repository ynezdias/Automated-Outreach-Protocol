"""Contract tests for the pure rules-v1 classify handler and its buckets.

The contract: every response has exactly the eight keys, ``confidence`` is
always None (rules, not probabilities), ``auto_reply`` exists in the action
vocabulary but is never returned by rules-v1, and any internal error fails
closed to ``human_review`` — classify never raises.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from classifier.buckets import bucket_intent
from classifier.handler import (
    ACTIONS,
    DEFAULT_HANDOFF_INTENTS,
    HANDOFF_ENV,
    MODEL_VERSION,
    classify,
    fail_closed,
    handoff_intents,
    rule_set_hash,
)
from guardrails import evaluate

RESPONSE_KEYS = (
    "action",
    "intent",
    "rule",
    "trigger",
    "handoff_reason",
    "confidence",
    "model_version",
    "latency_ms",
)


def request(body: Any = "Who is this?", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "message_id": "7590443",
        "from_number": "+15512357742",
        "body": body,
        "channel": "sms",
    }
    payload.update(overrides)
    return payload


def assert_contract(response: dict[str, Any]) -> None:
    assert tuple(response) == RESPONSE_KEYS, "exact response shape, no extras, stable order"
    assert response["action"] in ACTIONS
    assert response["confidence"] is None, "rules have no probabilities — never fabricate one"
    assert response["model_version"] == MODEL_VERSION
    assert isinstance(response["latency_ms"], float)
    assert response["latency_ms"] >= 0


def test_suppress_and_stop_shape() -> None:
    response = classify(request("STOP"))
    assert_contract(response)
    assert response["action"] == "suppress_and_stop"
    assert response["rule"] == "opt_out"
    assert response["intent"] == "Opt_Out"
    assert response["trigger"] == "stop"
    assert response["handoff_reason"] is None


def test_guardrail_human_review_shape() -> None:
    response = classify(request("my attorney will hear about this"))
    assert_contract(response)
    assert response["action"] == "human_review"
    assert response["rule"] == "legal_escalation"
    assert response["intent"] == "Legal_Escalation"
    assert response["trigger"] == "attorney"
    assert response["handoff_reason"] == "guardrail:legal_escalation"


def test_handoff_human_review_shape() -> None:
    response = classify(request("Who is this?"))
    assert_contract(response)
    assert response["action"] == "human_review"
    assert response["rule"] is None
    assert response["trigger"] is None
    assert response["intent"] == "Question"
    assert response["handoff_reason"] is not None
    assert response["handoff_reason"].startswith("handoff intent Question")


def test_interested_phrase_routes_to_handoff() -> None:
    # Previously fell to Unclear/no_action — the exact failure class of the
    # Process_Update gap, pinned here after the approved bucket widening.
    response = classify(request("yes im interested"))
    assert_contract(response)
    assert response["action"] == "human_review"
    assert response["intent"] == "Interested"


def test_process_update_hands_off_by_default() -> None:
    # 859 unique observed (2nd-largest bucket): "what's the status" must reach
    # a rep, not fall through to no_action.
    response = classify(request("just sent the statements over"))
    assert_contract(response)
    assert response["action"] == "human_review"
    assert response["intent"] == "Process_Update"
    assert response["handoff_reason"] is not None
    assert response["handoff_reason"].startswith("handoff intent Process_Update")


def test_no_action_shape() -> None:
    # Not_Interested is auto-reply ELIGIBLE per TAXONOMY.md §5 — and still gets
    # no_action: nothing may return auto_reply until approved templates exist.
    response = classify(request("Not interested"))
    assert_contract(response)
    assert response["action"] == "no_action"
    assert response["intent"] == "Not_Interested"
    assert response["rule"] is None
    assert response["handoff_reason"] is None


def test_auto_reply_is_in_the_vocabulary_but_never_returned() -> None:
    assert "auto_reply" in ACTIONS
    bodies = [
        "Yes",
        "50k",
        "send me info",
        "Not interested",
        "wrong number",
        "STOP",
        "sue you",
        "?",
    ]
    for body in bodies:
        assert classify(request(body))["action"] != "auto_reply"


def test_loop_breaker_uses_the_request_thread() -> None:
    thread = [
        {"body": "opener", "direction": "outbound", "is_auto_reply": True},
        {"body": "hi", "direction": "inbound"},
        {"body": "follow-up", "direction": "outbound", "is_auto_reply": True},
    ]
    response = classify(request("Yes", thread=thread))
    assert_contract(response)
    assert response["action"] == "human_review"
    assert response["rule"] == "loop_breaker"
    assert response["intent"] == "Interested", "intent still reported for the reviewer"
    assert response["handoff_reason"] == "guardrail:loop_breaker"


def test_missing_body_fails_closed() -> None:
    response = classify({})
    assert_contract(response)
    assert response["action"] == "human_review"
    assert response["handoff_reason"] == "internal_error:KeyError"
    assert response["intent"] is None
    assert response["rule"] is None


def test_non_string_body_fails_closed() -> None:
    response = classify(request(42))
    assert response["action"] == "human_review"
    assert response["handoff_reason"] == "internal_error:TypeError"


def test_unknown_channel_fails_closed() -> None:
    response = classify(request(channel="fax"))
    assert response["action"] == "human_review"
    assert response["handoff_reason"] == "internal_error:ValueError"


def test_internal_exception_fails_closed_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr("classifier.handler.evaluate", boom)
    response = classify(request("Yes"))
    assert_contract(response)
    assert response["action"] == "human_review"
    assert response["handoff_reason"] == "internal_error:RuntimeError"


def test_channel_defaults_to_sms() -> None:
    assert classify({"body": "Yes"})["action"] == "human_review"  # Interested -> handoff


def test_handoff_list_from_env_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HANDOFF_ENV, "Question")
    assert classify(request("call me"))["action"] == "no_action"
    assert classify(request("Who is this?"))["action"] == "human_review"


def test_handoff_list_from_json_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "handoff.json"
    config.write_text(json.dumps(["Call_Request"]), encoding="utf-8")
    monkeypatch.setenv(HANDOFF_ENV, str(config))
    assert classify(request("call me"))["action"] == "human_review"
    assert classify(request("Who is this?"))["action"] == "no_action"


def test_handoff_default_when_env_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HANDOFF_ENV, "   ")
    assert handoff_intents() == DEFAULT_HANDOFF_INTENTS


def test_rule_set_hash_is_stable_and_config_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    first = rule_set_hash()
    assert len(first) == 64
    assert first == rule_set_hash()
    int(first, 16)  # hex
    monkeypatch.setenv(HANDOFF_ENV, "Question")
    assert rule_set_hash() != first, "handoff config is part of the rule set"


def test_fail_closed_helper_shape() -> None:
    response = fail_closed("internal_error:wrapper")
    assert_contract(response)
    assert response["action"] == "human_review"
    assert response["latency_ms"] == 0.0


BUCKET_CASES = [
    ("STOP", "Opt_Out"),
    ("This is harassment, expect a TCPA complaint", "Legal_Escalation"),
    ("fuck off", "Hostile"),
    ("Automatic reply: I am away from the office", "Auto_Reply"),
    ("50k", "Amount_Given"),
    ("$1,500,000", "Amount_Given"),
    ("Yes", "Interested"),
    ("sounds good!", "Interested"),
    ("Who is this?", "Question"),
    ("reach me at owner@acmellc.com", "Request_More_Info"),
    ("send me info", "Request_More_Info"),
    ("I cant get text messages on this phone. Please call", "Call_Request"),
    ("call me", "Call_Request"),
    ("Done.", "Process_Update"),
    ("just sent the statements over", "Process_Update"),
    ("No thanks", "Not_Interested"),
    ("We are already funded for the year", "Not_Interested"),
    ("we have funding", "Not_Interested"),
    ("not interested at all", "Not_Interested"),
    ("not interested", "Not_Interested"),
    ("im not interested thanks", "Not_Interested"),
    ("yes im interested", "Interested"),
    ("im interested", "Interested"),
    ("interested", "Interested"),
    ("very interested", "Interested"),
    ("you have the wrong number", "Wrong_Person"),
    ("", "Unclear"),
    ("Stop by our office next week", "Unclear"),
    ("hmm let me think", "Unclear"),
]


@pytest.mark.parametrize(("body", "expected"), BUCKET_CASES)
def test_bucket_intent_matches_taxonomy(body: str, expected: str) -> None:
    intent, evidence = bucket_intent(body, evaluate(body, [], "sms"))
    assert intent == expected
    assert evidence
