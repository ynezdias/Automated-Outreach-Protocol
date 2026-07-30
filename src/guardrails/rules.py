"""The guardrail rules table (CLAUDE.md) as a pure function. No ML.

Evaluation order is fixed and load-bearing:
opt_out -> legal_escalation -> hostility -> bounce_or_autoreply ->
loop_breaker -> proceed. An opt-out in a looping thread still suppresses;
a lawyer threat containing profanity is legal escalation, not hostility.

Opt-out semantics (ADR-015): a keyword matches as the WHOLE message
(carrier STOP semantics) — "stop by our office next week" must never
suppress — with punctuation stripped, whitespace-insensitive comparison,
confusable folding, and edit-distance-1 fuzz on keywords of length >= 4
(so "and" can never fuzzy-match "END"). An imperative "stop <contact verb>"
opening ("stop texting me") also suppresses; negated forms ("please don't
stop sending these") cannot match it.
"""

import re
from dataclasses import dataclass, field

from guardrails.text import canonicalize, fold, within_edit_distance_1

SUPPRESS_AND_STOP = "suppress_and_stop"
HUMAN_REVIEW = "human_review"
PROCEED = "proceed"

CHANNELS = ("sms", "email")

OPT_OUT_KEYWORDS = (
    "stop",
    "stopall",
    "unsubscribe",
    "cancel",
    "end",
    "quit",
    "revoke",
    "optout",
    "opt out",
    "remove me",
)
_MIN_FUZZY_LENGTH = 4
_STOP_IMPERATIVE_RE = re.compile(
    r"^(?:please )?stop (?:texting|sending|messaging|contacting|emailing|calling)\b"
)

_LEGAL_PREFIXES = ("attorney", "lawyer", "lawsuit", "tcpa", "fcc", "complaint", "harass")
_LEGAL_TOKENS = frozenset({"sue", "sues", "sued", "suing"})
_LEGAL_PHRASES = ("report you",)

_HOSTILITY_WORDS = frozenset({"fuck", "fucking", "shit", "bullshit", "asshole", "bitch"})
_HOSTILITY_PHRASES = ("screw you", "piss off", "go to hell", "leave me alone")

_AUTOREPLY_MARKERS = (
    "out of office",
    "out of the office",
    "automatic reply",
    "auto reply",
    "autoreply",
    "auto submitted",
    "auto generated",
    "on vacation",
    "away from the office",
)
_BOUNCE_PHRASES = (
    "delivery status notification",
    "undeliverable",
    "could not be delivered",
    "mail delivery failed",
    "mailbox unavailable",
)
_DSN_CODE_RE = re.compile(r"\b5\.\d\.\d{1,3}\b")
_SMTP_CODE_RE = re.compile(r"\b55[0-4]\b")


@dataclass(frozen=True)
class Message:
    body: str
    direction: str  # 'inbound' | 'outbound'
    is_auto_reply: bool = False


@dataclass(frozen=True)
class GuardrailResult:
    action: str
    rule: str | None = field(default=None)
    trigger: str | None = field(default=None)


def evaluate(body: str, thread: list[Message], channel: str) -> GuardrailResult:
    """Apply the rules table in order. Pure: no I/O, no state, no ML."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}; expected one of {CHANNELS}")
    canon = canonicalize(body)
    folded = fold(body)

    trigger = _match_opt_out(canon)
    if trigger is not None:
        return GuardrailResult(SUPPRESS_AND_STOP, "opt_out", trigger)
    trigger = _match_legal(canon)
    if trigger is not None:
        return GuardrailResult(HUMAN_REVIEW, "legal_escalation", trigger)
    trigger = _match_hostility(canon)
    if trigger is not None:
        return GuardrailResult(HUMAN_REVIEW, "hostility", trigger)
    trigger = _match_bounce_or_autoreply(canon, folded, channel)
    if trigger is not None:
        return GuardrailResult(HUMAN_REVIEW, "bounce_or_autoreply", trigger)
    if _auto_reply_count(thread) >= 2:
        return GuardrailResult(HUMAN_REVIEW, "loop_breaker", "2 auto-replies in thread")
    return GuardrailResult(PROCEED)


def _fuzzy_whole_match(text: str, keyword: str) -> bool:
    if text == keyword:
        return True
    return len(keyword) >= _MIN_FUZZY_LENGTH and within_edit_distance_1(text, keyword)


def _match_opt_out(canon: str) -> str | None:
    despaced = canon.replace(" ", "")
    for keyword in OPT_OUT_KEYWORDS:
        if _fuzzy_whole_match(canon, keyword):
            return keyword
        if _fuzzy_whole_match(despaced, keyword.replace(" ", "")):
            return keyword
    imperative = _STOP_IMPERATIVE_RE.match(canon)
    if imperative is not None:
        return imperative.group(0)
    return None


def _match_legal(canon: str) -> str | None:
    for phrase in _LEGAL_PHRASES:
        if phrase in canon:
            return phrase
    for token in canon.split():
        if token in _LEGAL_TOKENS:
            return token
        for prefix in _LEGAL_PREFIXES:
            if token.startswith(prefix):
                return prefix
    return None


def _match_hostility(canon: str) -> str | None:
    for phrase in _HOSTILITY_PHRASES:
        if phrase in canon:
            return phrase
    for token in canon.split():
        if token in _HOSTILITY_WORDS:
            return token
    return None


def _match_bounce_or_autoreply(canon: str, folded: str, channel: str) -> str | None:
    for marker in _AUTOREPLY_MARKERS:
        if marker in canon:
            return marker
    if channel == "email":
        for phrase in _BOUNCE_PHRASES:
            if phrase in canon:
                return phrase
        dsn = _DSN_CODE_RE.search(folded)
        if dsn is not None:
            return dsn.group(0)
        smtp = _SMTP_CODE_RE.search(folded)
        if smtp is not None:
            return smtp.group(0)
    return None


def _auto_reply_count(thread: list[Message]) -> int:
    return sum(1 for message in thread if message.direction == "outbound" and message.is_auto_reply)
