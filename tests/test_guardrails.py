"""Adversarial tests for the deterministic guardrails — written first.

Every rule is pure string logic; no ML. False positives matter as much as
true positives: suppressing an interested prospect who said "stop by" is a
real cost, so the negative cases here are load-bearing.
"""

import pytest

from guardrails import (
    HUMAN_REVIEW,
    PROCEED,
    SUPPRESS_AND_STOP,
    GuardrailResult,
    Message,
    evaluate,
)


def run(body: str, thread: list[Message] | None = None, channel: str = "sms") -> GuardrailResult:
    return evaluate(body, thread or [], channel)


# --- opt_out: true positives ----------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "stop",
        "STOP.",
        "Stop!",
        " sto p ",
        "s t o p",
        "stopp",  # edit distance 1
        "stap",  # substitution, distance 1
        "sttop",  # insertion, distance 1
        "ѕtop",  # Cyrillic dze homoglyph
        "STOPALL",
        "unsubscribe",
        "UNSUBSCRIBE!!",
        "Un-subscribe",
        "cancel",
        "END",
        "quit",
        "REVOKE",
        "optout",
        "OPT OUT",
        "opt-out",
        "remove me",
        "Remove  Me.",
        "stop texting me",
        "Please STOP messaging me",
        "stop contacting me!",
    ],
)
def test_opt_out_suppresses(body: str) -> None:
    result = run(body)
    assert result.action == SUPPRESS_AND_STOP
    assert result.rule == "opt_out"
    assert result.trigger is not None


def test_opt_out_wins_even_in_a_looping_thread() -> None:
    thread = [Message(body="t", direction="outbound", is_auto_reply=True)] * 2
    assert run("STOP", thread).rule == "opt_out"
    assert run("STOP", thread).action == SUPPRESS_AND_STOP


# --- opt_out: false positives (never suppress an interested prospect) -----------


@pytest.mark.parametrize(
    "body",
    [
        "stop by our office next week",
        "please don't stop sending these",
        "I need to cancel my 3pm, can we do 4 instead?",
        "end of quarter numbers look great",
        "quit my last job to start this company",
        "stopping",  # length delta 4 from "stop": fuzzy must not reach
        "and",  # distance 1 from "end", but 3-letter keywords match exactly only
        "stab",  # two substitutions from "stop"
        "xtopy",  # length 5, no single edit reaches any keyword
    ],
)
def test_opt_out_is_word_boundary_and_whole_message_aware(body: str) -> None:
    result = run(body)
    assert result.action != SUPPRESS_AND_STOP, f"false-positive suppression on: {body!r}"


# --- legal_escalation -----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "I'll have my attorney call you",
        "my lawyer will be in touch",
        "this is a TCPA violation",
        "reporting this to the FCC",
        "I am filing a complaint",
        "stop harassing me",
        "this is harassment",
        "I will sue you",
        "we sued the last company that did this",
        "I'm going to report you",
        "STOP or my lawyer gets involved",
    ],
)
def test_legal_escalation_routes_to_human_never_auto_reply(body: str) -> None:
    result = run(body)
    assert result.action == HUMAN_REVIEW
    assert result.rule == "legal_escalation"


def test_legal_takes_precedence_over_hostility() -> None:
    result = run("my fucking lawyer will hear about this")
    assert result.rule == "legal_escalation"


# --- hostility ------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    ["fuck off", "this is bullshit", "screw you", "piss off", "go to hell", "leave me alone"],
)
def test_hostility_routes_to_human(body: str) -> None:
    result = run(body)
    assert result.action == HUMAN_REVIEW
    assert result.rule == "hostility"


# --- bounce_or_autoreply --------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "Auto-Submitted: auto-replied",
        "X-Autoreply: yes",
        "Automatic reply: Out of Office",
        "I am out of the office until Monday with limited email access",
        "Thank you for your email. I am on vacation until 8/15.",
    ],
)
def test_autoreply_markers_route_to_human(body: str) -> None:
    result = run(body, channel="email")
    assert result.action == HUMAN_REVIEW
    assert result.rule == "bounce_or_autoreply"


@pytest.mark.parametrize(
    "body",
    [
        "550 5.1.1 The email account that you tried to reach does not exist",
        "Delivery Status Notification (Failure)",
        "Your message could not be delivered",
        "554 mailbox unavailable",
    ],
)
def test_email_bounces_route_to_human(body: str) -> None:
    result = run(body, channel="email")
    assert result.action == HUMAN_REVIEW
    assert result.rule == "bounce_or_autoreply"


def test_bare_smtp_code_is_a_bounce() -> None:
    result = run("550 user unknown", channel="email")
    assert result.rule == "bounce_or_autoreply"
    assert result.trigger == "550"


def test_edit_distance_helper_edges() -> None:
    from guardrails.text import within_edit_distance_1

    assert within_edit_distance_1("stop", "stop") is True  # distance 0
    assert within_edit_distance_1("stop", "stopping") is False  # length delta > 1
    assert within_edit_distance_1("stop", "stab") is False  # two substitutions


def test_out_of_office_detected_on_sms_too() -> None:
    assert run("out of office until monday", channel="sms").rule == "bounce_or_autoreply"


def test_dsn_codes_are_email_only() -> None:
    # A bare numeric string in SMS is not a bounce; codes only mean DSN in email.
    assert run("5.1.1", channel="sms").action == PROCEED


# --- loop_breaker ---------------------------------------------------------------


def _auto(body: str = "template reply") -> Message:
    return Message(body=body, direction="outbound", is_auto_reply=True)


def test_two_auto_replies_force_human_takeover() -> None:
    thread = [
        Message(body="first touch", direction="outbound", is_auto_reply=False),
        Message(body="tell me more", direction="inbound"),
        _auto(),
        Message(body="and pricing?", direction="inbound"),
        _auto(),
    ]
    result = run("sounds good, what are the rates?", thread)
    assert result.action == HUMAN_REVIEW
    assert result.rule == "loop_breaker"


def test_one_auto_reply_still_proceeds() -> None:
    thread = [_auto(), Message(body="ok", direction="inbound")]
    assert run("interested, send details", thread).action == PROCEED


def test_inbound_and_manual_outbound_do_not_count_toward_loop() -> None:
    thread = [
        Message(body="manual note", direction="outbound", is_auto_reply=False),
        Message(body="hi", direction="inbound", is_auto_reply=False),
        _auto(),
    ]
    assert run("what terms do you offer?", thread).action == PROCEED


# --- proceed and result shape ---------------------------------------------------


def test_clean_interested_reply_proceeds() -> None:
    result = run("Yes, I'm interested — what are the rates?")
    assert result == GuardrailResult(action=PROCEED, rule=None, trigger=None)


def test_clean_email_reply_proceeds() -> None:
    assert run("Sounds good, send over the details.", channel="email").action == PROCEED


def test_trigger_is_recorded_for_audit() -> None:
    assert run("unsubscribe").trigger == "unsubscribe"
    assert run("I'll have my attorney call you").trigger == "attorney"


def test_unknown_channel_raises() -> None:
    with pytest.raises(ValueError, match="channel"):
        run("hello", channel="fax")


def test_empty_body_proceeds() -> None:
    assert run("").action == PROCEED
