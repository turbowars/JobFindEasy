"""Sponsorship status detection from JD text.

Returns one of: "offered", "denied", "unknown".
'denied' is hard-blocked downstream. 'offered' boosts score.
"""

from __future__ import annotations

import re

DENIED_PATTERNS = [
    r"\bno\s+(visa\s+)?sponsorship\b",
    r"\bnot\s+(currently\s+|able\s+to\s+)?sponsor\b",
    r"\bunable\s+to\s+sponsor\b",
    r"\bdo\s+not\s+(offer|provide)\s+(visa\s+)?sponsorship\b",
    r"\bwithout\s+sponsorship\b",
    r"\bmust\s+be\s+(legally\s+)?authorized\s+to\s+work\s+(in\s+)?(the\s+)?(u\.?s\.?|united\s+states)\s+(without|with\s+no)\s+sponsorship\b",
    r"\bauthorized\s+to\s+work\s+(in\s+)?(the\s+)?(u\.?s\.?|united\s+states)\s+(without|with\s+no)\s+sponsorship\b",
    r"\bcurrent\s+u\.?s\.?\s+work\s+authorization\s+(without|with\s+no)\s+sponsorship\b",
    r"\bu\.?s\.?\s+citizens?\s+only\b",
    r"\bunited\s+states\s+citizens?\s+only\b",
    r"\bgreen\s+card\s+(holders?\s+)?only\b",
    r"\bdoes\s+not\s+sponsor\s+work\s+(visa|authorization)\b",
]

OFFERED_PATTERNS = [
    r"\bvisa\s+sponsorship\s+(is\s+)?available\b",
    r"\bsponsorship\s+(is\s+|may\s+be\s+)?available\b",
    r"\bh-?1b\s+sponsorship\b",
    r"\bopen\s+to\s+sponsor(ing|ship)\b",
    r"\bwilling\s+to\s+sponsor\b",
    r"\bwill\s+(consider\s+)?sponsor\b",
    r"\bo-?1\s+sponsorship\b",
]


def detect_sponsorship(jd_text: str) -> str:
    if not jd_text:
        return "unknown"
    text = jd_text.lower()
    for pat in DENIED_PATTERNS:
        if re.search(pat, text):
            return "denied"
    for pat in OFFERED_PATTERNS:
        if re.search(pat, text):
            return "offered"
    return "unknown"
