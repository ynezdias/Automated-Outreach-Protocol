"""Adversarial tests for the shared normalization module.

This module is the single definition of identifier normalization used by both
the cleansing pipeline and the suppression list — the tests pin its behavior.
"""

import pytest

from cleansing.normalize import normalize_email, normalize_phone

E164 = "+16502530000"

FIVE_PHONE_FORMATS = [
    "+1 (650) 253-0000",
    "650-253-0000",
    "6502530000",
    "1.650.253.0000",
    "+16502530000",
]


@pytest.mark.parametrize("raw", FIVE_PHONE_FORMATS)
def test_same_number_in_five_formats_normalizes_identically(raw: str) -> None:
    assert normalize_phone(raw) == E164


def test_phone_with_leading_trailing_whitespace() -> None:
    assert normalize_phone("  +1 650 253 0000\t") == E164


def test_phone_with_zero_width_characters() -> None:
    assert normalize_phone("+1​650‍253﻿0000") == E164


@pytest.mark.parametrize("raw", ["not-a-phone", "12", "999999", ""])
def test_invalid_phone_returns_none(raw: str) -> None:
    assert normalize_phone(raw) is None


def test_phone_none_returns_none() -> None:
    assert normalize_phone(None) is None


def test_email_lowercased_and_stripped() -> None:
    assert normalize_email("  User@Example.COM \n") == "user@example.com"


def test_email_plus_addressing_canonicalized() -> None:
    assert normalize_email("user+promo@example.com") == "user@example.com"
    assert normalize_email("USER+a+b@Example.com") == "user@example.com"


def test_email_unicode_homoglyphs_fold_to_ascii() -> None:
    # Fullwidth letters, fullwidth @ and fullwidth dot (NFKC compatibility forms).
    assert normalize_email("ｕｓｅｒ＠ｅｘａｍｐｌｅ．ｃｏｍ") == "user@example.com"


def test_email_zero_width_characters_removed() -> None:
    assert normalize_email("user​@exam﻿ple.com") == "user@example.com"


@pytest.mark.parametrize(
    "raw",
    [
        "no-at-sign",
        "@example.com",
        "user@",
        "user@nodot",
        "us er@example.com",
        "+tag@example.com",
        "",
        "   ",
    ],
)
def test_invalid_email_returns_none(raw: str) -> None:
    assert normalize_email(raw) is None


def test_email_none_returns_none() -> None:
    assert normalize_email(None) is None
