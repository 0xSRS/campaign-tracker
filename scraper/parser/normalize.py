"""Controlled title normalisation. Deliberately NOT fuzzy: equal-after-normalisation only."""

from __future__ import annotations

import unicodedata

_TRANSLATE = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " ",
})


def collapse_ws(text: str) -> str:
    return " ".join(text.split())


def normalize_title(title: str) -> str:
    """NFKC, unify dashes/quotes, collapse whitespace, casefold. Nothing else is stripped."""
    return collapse_ws(unicodedata.normalize("NFKC", title).translate(_TRANSLATE)).casefold()