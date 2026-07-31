"""TAXONOMY.md pattern buckets — the deterministic intent heuristic.

These are the sizing heuristics from docs/TAXONOMY.md §1/§6 (precedence order
per §6: guardrail-produced labels first, then Amount_Given, Interested,
Question, Request_More_Info, Call_Request, Process_Update, Not_Interested,
Wrong_Person, Unclear). They are NOT the trained classifier — that does not
exist yet — but they are the rules-v1 intent source and the shared vocabulary
between the research harness and the classify service.
"""

import re

from guardrails import GuardrailResult

GUARDRAIL_INTENT = {
    "opt_out": "Opt_Out",
    "legal_escalation": "Legal_Escalation",
    "hostility": "Hostile",
    "bounce_or_autoreply": "Auto_Reply",
}

AFFIRMATIVES = frozenset(
    {"yes", "ok", "okay", "sure", "yep", "yeah", "yes please", "absolutely", "sounds good", "y"}
)
PROCESS_WORDS = frozenset({"sent", "done", "submitted", "uploaded", "sent it", "all sent"})
DECLINES = frozenset(
    {
        "no",
        "nope",
        "no thanks",
        "no thank you",
        "we're good",
        "were good",
        "im good",
        "i'm good",
        "all set",
        "not now",
        "not yet",
        "pass",
    }
)
AMOUNT_RE = re.compile(r"\$?\s?\d[\d,.]*\s*(k|m|mm|mil|million|thousand|grand)?", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
REQUEST_INFO_KEYWORDS = (
    "email me",
    "send me",
    "send info",
    "more info",
    "website",
    "your email",
    "company profile",
    "send over",
)
CALL_KEYWORDS = (
    "call me",
    "give me a call",
    "please call",
    "can't get text",
    "cant get text",
    "can't receive text",
    "cant receive text",
)
WRONG_KEYWORDS = (
    "wrong number",
    "wrong person",
    "doesn't work here",
    "doesnt work here",
    "no longer with",
    "not my number",
)


def bucket_intent(body: str, guardrail: GuardrailResult) -> tuple[str, str]:
    """TAXONOMY.md heuristic buckets, §6 precedence. Returns (intent, evidence)."""
    if guardrail.rule in GUARDRAIL_INTENT:
        return GUARDRAIL_INTENT[guardrail.rule], f"guardrail rule {guardrail.rule}"
    text = " ".join(body.split()).strip().lower()
    bare = text.rstrip(".!,")
    if bare and AMOUNT_RE.fullmatch(bare):
        return "Amount_Given", f"bare amount pattern: {bare!r}"
    if bare in AFFIRMATIVES:
        return "Interested", f"short affirmative: {bare!r}"
    if text.endswith("?"):
        return "Question", "ends with '?'"
    if EMAIL_RE.search(body):
        return "Request_More_Info", "contains an email address"
    for keyword in REQUEST_INFO_KEYWORDS:
        if keyword in text:
            return "Request_More_Info", f"keyword: {keyword!r}"
    for keyword in CALL_KEYWORDS:
        if keyword in text:
            return "Call_Request", f"keyword: {keyword!r}"
    if bare in PROCESS_WORDS or bare.startswith("just sent"):
        return "Process_Update", f"process word: {bare!r}"
    if (
        bare in DECLINES
        or "not interested" in text
        or "already funded" in text
        or "we have funding" in text
    ):
        return "Not_Interested", "decline phrasing"
    for keyword in WRONG_KEYWORDS:
        if keyword in text:
            return "Wrong_Person", f"keyword: {keyword!r}"
    return "Unclear", "no bucket matched (TAXONOMY.md: honest default)"
