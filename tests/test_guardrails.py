"""Adversarial tests for the deterministic guardrails.

The shared truth table (tests/fixtures/guardrail_truth_table.csv) is the
cross-language contract: this file runs every row against the Python
reference implementation, and OutreachGuardrailsTest.cls runs the same rows
(as a byte-identical static resource) against the Apex port. CI fails if the
two implementations disagree on any row, or if the two copies of the CSV
drift apart.

False positives matter as much as true positives: suppressing an interested
prospect who said "stop by" is a real cost, so the negative rows are
load-bearing. A handful of Python-native tests below cover semantics the
CSV's (body, channel, prior_auto_replies) columns cannot express.
"""

import csv
from pathlib import Path

import pytest

from guardrails import (
    HUMAN_REVIEW,
    PROCEED,
    SUPPRESS_AND_STOP,
    GuardrailResult,
    Message,
    evaluate,
)

FIXTURE = Path(__file__).parent / "fixtures" / "guardrail_truth_table.csv"
STATIC_RESOURCE = (
    Path(__file__).parent.parent
    / "salesforce"
    / "force-app"
    / "main"
    / "default"
    / "staticresources"
    / "Guardrail_Truth_Table.csv"
)


def _rows() -> list[dict[str, str]]:
    with FIXTURE.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


_ROWS = _rows()


def _thread(prior_auto_replies: int) -> list[Message]:
    return [Message(body="t", direction="outbound", is_auto_reply=True)] * prior_auto_replies


@pytest.mark.parametrize("row", _ROWS, ids=[row["id"] for row in _ROWS])
def test_truth_table(row: dict[str, str]) -> None:
    result = evaluate(row["message_body"], _thread(int(row["prior_auto_replies"])), row["channel"])
    assert result.action == row["expected_action"], f"{row['id']}: {row['note']}"
    assert (result.rule or "") == row["expected_rule"], f"{row['id']}: {row['note']}"


def test_truth_table_covers_every_action() -> None:
    actions = {row["expected_action"] for row in _ROWS}
    assert actions == {SUPPRESS_AND_STOP, HUMAN_REVIEW, PROCEED}


def test_truth_table_matches_salesforce_static_resource() -> None:
    assert FIXTURE.read_bytes() == STATIC_RESOURCE.read_bytes(), (
        "tests/fixtures/guardrail_truth_table.csv and the Guardrail_Truth_Table "
        "static resource must be byte-identical — they are the contract that "
        "keeps the Python and Apex implementations from drifting"
    )


# --- semantics the CSV columns cannot express -----------------------------------


def test_edit_distance_helper_edges() -> None:
    from guardrails.text import within_edit_distance_1

    assert within_edit_distance_1("stop", "stop") is True  # distance 0
    assert within_edit_distance_1("stop", "stopping") is False  # length delta > 1
    assert within_edit_distance_1("stop", "stab") is False  # two substitutions


def test_inbound_and_manual_outbound_do_not_count_toward_loop() -> None:
    thread = [
        Message(body="manual note", direction="outbound", is_auto_reply=False),
        Message(body="hi", direction="inbound", is_auto_reply=False),
        Message(body="template reply", direction="outbound", is_auto_reply=True),
    ]
    assert evaluate("what terms do you offer?", thread, "sms").action == PROCEED


def test_mixed_thread_with_two_auto_replies_forces_human_takeover() -> None:
    thread = [
        Message(body="first touch", direction="outbound", is_auto_reply=False),
        Message(body="tell me more", direction="inbound"),
        Message(body="template reply", direction="outbound", is_auto_reply=True),
        Message(body="and pricing?", direction="inbound"),
        Message(body="template reply", direction="outbound", is_auto_reply=True),
    ]
    result = evaluate("sounds good, what are the rates?", thread, "sms")
    assert result.action == HUMAN_REVIEW
    assert result.rule == "loop_breaker"


def test_clean_reply_result_shape() -> None:
    result = evaluate("Yes, I'm interested — what are the rates?", [], "sms")
    assert result == GuardrailResult(action=PROCEED, rule=None, trigger=None)


def test_trigger_is_recorded_for_audit() -> None:
    assert evaluate("unsubscribe", [], "sms").trigger == "unsubscribe"
    assert evaluate("I'll have my attorney call you", [], "sms").trigger == "attorney"
    assert evaluate("550 user unknown", [], "email").trigger == "550"


def test_unknown_channel_raises() -> None:
    with pytest.raises(ValueError, match="channel"):
        evaluate("hello", [], "fax")


def test_fixture_rows_are_well_formed() -> None:
    for row in _ROWS:
        assert row["channel"] in ("sms", "email"), row["id"]
        assert int(row["prior_auto_replies"]) >= 0, row["id"]
        assert (row["expected_rule"] == "") == (row["expected_action"] == PROCEED), row["id"]
