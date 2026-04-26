"""ATS keyword extraction (Haiku) + fuzzy matching (rapidfuzz).

Two stages:
1. extract_keywords(jd_text, jd_cache_key) — one Haiku call returning
   {required, preferred, soft}. LRU-cached so repeat generations against the
   same JD don't re-spend tokens.
2. match_keywords(resume_text, keywords) — pure-Python rapidfuzz scan,
   weighted (required=2, preferred=1.5, soft=1). Returns coverage % and
   matched/missing per tier.

Borrowed conceptually from srbhr/Resume-Matcher (Apache-2.0).
"""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Optional

from rapidfuzz import fuzz, utils

from ..llm import chat

log = logging.getLogger(__name__)

EXTRACT_SYSTEM = """You extract ATS keywords from a job description.

Return ONLY a single JSON object, no markdown fences:
{"required": [...], "preferred": [...], "soft": [...]}

RULES
- 15 to 25 keywords TOTAL across all three lists.
- Each keyword 1 to 4 words. Prefer the JD's exact spelling.
- For common acronyms emit BOTH forms as separate entries
  (e.g. "CI/CD" and "continuous integration", "API" and "application programming interface").
- No filler ("team player", "fast-paced", "passionate", "self-starter").
- No years-of-experience phrases ("5+ years", "senior").
- Normalize plurals to singular when natural ("APIs" -> "API").
- Skip the company name and the literal job title.
- Required = hard must-haves. Preferred = nice-to-haves / "bonus" / "plus".
  Soft = methodologies, soft skills, domain terms."""


@lru_cache(maxsize=256)
def _extract_cached(jd_cache_key: str, jd_text: str, model: str) -> str:
    """Raw JSON string returned by Haiku. Cached on (cache_key, model)."""
    return chat(
        system=EXTRACT_SYSTEM,
        user=jd_text[:8000] if jd_text else "",
        model=model,
        max_tokens=600,
    )


def extract_keywords(
    jd_text: str, jd_cache_key: str, model: Optional[str] = None
) -> dict:
    """Returns {required: [], preferred: [], soft: []}.

    Returns empty dict on failure — generation can proceed without seeded
    keywords (the existing skill-driven prompt is still in play).
    """
    if not jd_text or not jd_text.strip():
        return _empty()
    model = model or os.environ.get("SCORING_MODEL", "anthropic/claude-haiku-4.5")
    try:
        raw = _extract_cached(jd_cache_key, jd_text, model)
    except Exception as e:
        log.warning("ats keyword extract API error: %s", e)
        return _empty()
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except Exception as e:
        log.warning("ats keyword extract JSON decode failed: %s | text=%s", e, text[:200])
        return _empty()
    return {
        "required": _clean_list(parsed.get("required")),
        "preferred": _clean_list(parsed.get("preferred")),
        "soft": _clean_list(parsed.get("soft")),
    }


def match_keywords(
    resume_text: str, keywords: dict, fuzz_threshold: int = 85
) -> dict:
    """rapidfuzz partial_ratio per keyword, weighted by tier.

    Returns:
      {match_pct: int, matched: {required:[],preferred:[],soft:[]},
       missing: {required:[],preferred:[],soft:[]}}
    """
    matched = {"required": [], "preferred": [], "soft": []}
    missing = {"required": [], "preferred": [], "soft": []}
    weights = {"required": 2.0, "preferred": 1.5, "soft": 1.0}
    if not _has_any(keywords):
        return {"match_pct": 0, "matched": matched, "missing": missing}

    norm_resume = _normalize_for_match(resume_text)
    hit, total = 0.0, 0.0
    for tier in ("required", "preferred", "soft"):
        w = weights[tier]
        for kw in keywords.get(tier, []):
            total += w
            norm_kw = _normalize_for_match(kw)
            if not norm_kw:
                missing[tier].append(kw)
                continue
            score = fuzz.partial_ratio(norm_kw, norm_resume)
            if score >= fuzz_threshold:
                hit += w
                matched[tier].append(kw)
            else:
                missing[tier].append(kw)
    pct = round(100 * hit / total) if total else 0
    return {"match_pct": pct, "matched": matched, "missing": missing}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _empty() -> dict:
    return {"required": [], "preferred": [], "soft": []}


def _has_any(keywords: dict) -> bool:
    return any(keywords.get(t) for t in ("required", "preferred", "soft"))


_PUNCT_RE = re.compile(r"[-_/]+")
_WS_RE = re.compile(r"\s+")


def _normalize_for_match(s: str) -> str:
    """Lowercase, replace - / _ with space, collapse whitespace.

    Critical for fuzzy matching: 'CI/CD' -> 'ci cd', 'micro-frontends'
    -> 'micro frontends'. Without this, partial_ratio has trouble crossing
    punctuation boundaries.
    """
    if not s:
        return ""
    s = utils.default_process(s) or ""  # lowercase + remove non-alphanum
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _clean_list(items) -> list:
    if not isinstance(items, list):
        return []
    out = []
    seen = set()
    for x in items:
        if not isinstance(x, str):
            continue
        s = x.strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out
