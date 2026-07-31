"""Pure rules-only classify handler. This is what goes into Lambda in WO-24.

``classify(request) -> response`` — no web framework, no network, no
Salesforce, no send path of any kind. Logic order (work order):

1. Guardrails (``src/guardrails``). Terminal verdicts return immediately.
2. TAXONOMY.md pattern buckets for intent.
3. Handoff: intents in the configured handoff list -> ``human_review``.
4. Everything else -> ``no_action``. ``auto_reply`` exists in the action
   vocabulary but is NEVER returned by rules-v1: no approved templates exist
   in Salesforce yet, so nothing is eligible for an automated reply.

``confidence`` is always None — these are rules, not probabilities.
Fail closed: any internal error returns ``human_review``; classify never
raises.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from classifier.buckets import bucket_intent
from guardrails import HUMAN_REVIEW, SUPPRESS_AND_STOP, Message, evaluate

MODEL_VERSION = "rules-v1"
ACTIONS = ("suppress_and_stop", "human_review", "auto_reply", "no_action")
NO_ACTION = "no_action"

HANDOFF_ENV = "CLASSIFY_HANDOFF_INTENTS"
DEFAULT_HANDOFF_INTENTS = frozenset({"Interested", "Call_Request", "Amount_Given", "Question"})

_RULE_SOURCES = (
    Path(__file__).resolve().parent.parent / "guardrails" / "rules.py",
    Path(__file__).resolve().parent.parent / "guardrails" / "text.py",
    Path(__file__).resolve().parent / "buckets.py",
    Path(__file__).resolve(),
)


def handoff_intents() -> frozenset[str]:
    """Handoff list from env: comma-separated values, or a path to a JSON list.

    ``CLASSIFY_HANDOFF_INTENTS="Interested,Question"`` or
    ``CLASSIFY_HANDOFF_INTENTS="C:/path/handoff.json"`` (a JSON array).
    Unset -> the work-order default.
    """
    configured = os.environ.get(HANDOFF_ENV, "").strip()
    if not configured:
        return DEFAULT_HANDOFF_INTENTS
    if configured.lower().endswith(".json"):
        loaded = json.loads(Path(configured).read_text(encoding="utf-8"))
        return frozenset(str(intent) for intent in loaded)
    return frozenset(part.strip() for part in configured.split(",") if part.strip())


def rule_set_hash() -> str:
    """SHA-256 over the rule sources + handoff config: changes when rules change."""
    digest = hashlib.sha256()
    for source in _RULE_SOURCES:
        digest.update(source.read_bytes())
    digest.update(",".join(sorted(handoff_intents())).encode())
    return digest.hexdigest()


def fail_closed(reason: str, started: float | None = None) -> dict[str, Any]:
    """The fail-closed response: a human looks at it (CLAUDE.md), never a 500."""
    return _response(HUMAN_REVIEW, None, None, None, reason, started)


def classify(request: dict[str, Any]) -> dict[str, Any]:
    """Classify one inbound reply. Pure; never raises; never sends anything."""
    started = time.perf_counter()
    try:
        body = request["body"]
        if not isinstance(body, str):
            raise TypeError("body must be a string")
        channel = request.get("channel") or "sms"
        verdict = evaluate(body, _thread(request.get("thread")), channel)
        intent, evidence = bucket_intent(body, verdict)
        if verdict.action == SUPPRESS_AND_STOP:
            return _response(verdict.action, intent, verdict.rule, verdict.trigger, None, started)
        if verdict.action == HUMAN_REVIEW:
            reason = f"guardrail:{verdict.rule}"
            return _response(verdict.action, intent, verdict.rule, verdict.trigger, reason, started)
        if intent in handoff_intents():
            reason = f"handoff intent {intent} ({evidence})"
            return _response(HUMAN_REVIEW, intent, None, None, reason, started)
        return _response(NO_ACTION, intent, None, None, None, started)
    except Exception as error:  # fail closed, per the hard constraint
        return fail_closed(f"internal_error:{type(error).__name__}", started)


def _thread(raw: Any) -> list[Message]:
    """Lenient thread parse: [{body, direction, is_auto_reply}] -> [Message]."""
    thread: list[Message] = []
    for item in raw or ():
        thread.append(
            Message(
                body=str(item.get("body", "")),
                direction=str(item.get("direction", "")),
                is_auto_reply=bool(item.get("is_auto_reply", False)),
            )
        )
    return thread


def _response(
    action: str,
    intent: str | None,
    rule: str | None,
    trigger: str | None,
    handoff_reason: str | None,
    started: float | None,
) -> dict[str, Any]:
    latency = 0.0 if started is None else round((time.perf_counter() - started) * 1000, 3)
    return {
        "action": action,
        "intent": intent,
        "rule": rule,
        "trigger": trigger,
        "handoff_reason": handoff_reason,
        "confidence": None,  # rules, not probabilities — never fabricated
        "model_version": MODEL_VERSION,
        "latency_ms": latency,
    }
