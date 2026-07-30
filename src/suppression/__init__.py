"""Opt-out suppression list — compliance-critical.

Module-level functions operate on a process-wide default store, created from the
``SUPPRESSION_BUCKET`` environment variable on first use (Lambda-friendly).
Call :func:`configure` to inject a store explicitly (e.g. in tests).
"""

import os
from datetime import datetime

from suppression.store import (
    REFRESH_TTL_SECONDS,
    SuppressionMatch,
    SuppressionResult,
    SuppressionStore,
)

__all__ = [
    "REFRESH_TTL_SECONDS",
    "SuppressionMatch",
    "SuppressionResult",
    "SuppressionStore",
    "add_suppression",
    "configure",
    "export_snapshot",
    "is_suppressed",
]

_default_store: SuppressionStore | None = None


def configure(store: SuppressionStore | None) -> None:
    """Set (or with None, reset) the store used by the module-level functions."""
    global _default_store
    _default_store = store


def _get_store() -> SuppressionStore:
    global _default_store
    if _default_store is None:
        _default_store = SuppressionStore(bucket=os.environ["SUPPRESSION_BUCKET"])
    return _default_store


def is_suppressed(phone: str | None, email: str | None) -> SuppressionResult:
    return _get_store().is_suppressed(phone=phone, email=email)


def add_suppression(identifier: str, reason: str, source: str, occurred_at: datetime) -> None:
    _get_store().add_suppression(identifier, reason=reason, source=source, occurred_at=occurred_at)


def export_snapshot() -> str:
    return _get_store().export_snapshot()
