"""Canonical identifier normalization, shared by cleansing and suppression.

This is the single definition of what "the same phone number" and "the same
email address" mean anywhere in the system. The suppression list and the
cleansing pipeline both import from here — never reimplement these rules.

Rules:
- Phones normalize to E.164 (via ``phonenumbers``, default region US).
- Emails are NFKC-folded (so unicode compatibility homoglyphs like fullwidth
  letters collapse to ASCII), casefolded, stripped of zero-width characters and
  surrounding whitespace, and plus-address tags are removed (``user+tag@x`` →
  ``user@x``): over-suppression is safe, under-suppression is a violation.
- Anything that cannot be normalized returns ``None`` — callers decide how to
  fail, and compliance callers must fail closed.
"""

import unicodedata

import phonenumbers

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))


def _clean(raw: str) -> str:
    return unicodedata.normalize("NFKC", raw).translate(_ZERO_WIDTH).strip()


def normalize_phone(raw: str | None, *, default_region: str = "US") -> str | None:
    """Return the E.164 form of ``raw``, or None if it is not a valid number."""
    if raw is None:
        return None
    text = _clean(raw)
    if not text:
        return None
    try:
        parsed = phonenumbers.parse(text, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_company(raw: str | None) -> str | None:
    """Return ``raw`` NFKC-folded with whitespace collapsed, or None if empty."""
    if raw is None:
        return None
    collapsed = " ".join(_clean(raw).split())
    return collapsed or None


def normalize_email(raw: str | None) -> str | None:
    """Return the canonical form of ``raw``, or None if it is not a valid address."""
    if raw is None:
        return None
    text = _clean(raw).casefold()
    if not text or any(ch.isspace() for ch in text):
        return None
    local, sep, domain = text.rpartition("@")
    if not sep or not domain or "." not in domain:
        return None
    local = local.partition("+")[0]
    if not local:
        return None
    return f"{local}@{domain}"
