"""Deterministic pre-classifier guardrails — pure string logic, no ML.

These run BEFORE the classifier, never after, and their output is never
probabilistic (CLAUDE.md). Rules evaluate in a fixed order:
opt_out -> legal_escalation -> hostility -> bounce_or_autoreply ->
loop_breaker -> proceed.
"""

from guardrails.rules import (
    HUMAN_REVIEW,
    PROCEED,
    SUPPRESS_AND_STOP,
    GuardrailResult,
    Message,
    evaluate,
)

__all__ = [
    "HUMAN_REVIEW",
    "PROCEED",
    "SUPPRESS_AND_STOP",
    "GuardrailResult",
    "Message",
    "evaluate",
]
