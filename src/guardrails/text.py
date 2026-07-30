"""Text canonicalization for guardrail matching. Pure functions, no ML.

Two levels:
- ``fold``: NFKC + casefold + explicit unicode-confusables mapping + zero-width
  strip. Punctuation preserved (DSN codes like 5.1.1 need it).
- ``canonicalize``: ``fold`` then punctuation collapsed to single spaces —
  the form keyword rules match against.
"""

import re
import unicodedata

#: Common Cyrillic/Greek lookalikes an adversary can substitute for latin
#: letters. Explicit and reviewable — never inferred.
_CONFUSABLES = str.maketrans(
    {
        "а": "a",  # Cyrillic a
        "в": "b",
        "е": "e",
        "к": "k",
        "м": "m",
        "о": "o",
        "р": "p",
        "с": "c",
        "ѕ": "s",  # Cyrillic dze
        "т": "t",
        "у": "y",
        "х": "x",
        "і": "i",
        "ј": "j",
        "α": "a",  # Greek alpha
        "ο": "o",  # Greek omicron
        "ν": "v",
    }
)

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def fold(text: str) -> str:
    return (
        unicodedata.normalize("NFKC", text)
        .casefold()
        .translate(_CONFUSABLES)
        .translate(_ZERO_WIDTH)
    )


def canonicalize(text: str) -> str:
    return _NON_WORD_RE.sub(" ", fold(text)).strip()


def within_edit_distance_1(a: str, b: str) -> bool:
    """Levenshtein distance <= 1, without building a matrix."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b, strict=True) if x != y) == 1
    if len(a) > len(b):
        a, b = b, a
    prefix = 0
    while prefix < len(a) and a[prefix] == b[prefix]:
        prefix += 1
    return a[prefix:] == b[prefix + 1 :]
