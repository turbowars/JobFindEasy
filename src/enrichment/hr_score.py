"""HR-perspective resume scoring.

Independent signal from the ATS keyword match: would a senior hiring manager
forward this resume to the team for an interview? Catches resumes that
technically hit all the keywords but read like buzzword soup.

Borrowed conceptually from jananthan30/Resume-Builder (MIT) — implemented
here as a single Haiku call instead of vendoring their 130KB scoring module.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from ..llm import chat

log = logging.getLogger(__name__)

HR_SYSTEM = """You are a senior hiring manager screening resumes for the role described below.
Score the candidate's resume 0-100 on whether you would forward it to the team for an interview.

Score components (don't expose breakdown unless asked):
- Specificity and metrics in bullets — does each line have numbers, scope, named tools? (40 pts)
- Match to the JD's stated priorities — top 2-3 things the role really wants (30 pts)
- Readability — does it read like a human or buzzword soup? (15 pts)
- Title trajectory + scope progression — increasing responsibility over time? (15 pts)

A score of 80+ means "strong yes, definitely interview".
60-79 means "maybe, depends on pipeline volume".
Below 60 means "would not forward".

Return ONLY a single JSON object, no markdown fences:
{
  "hr_score": int,
  "rationale": "1-2 sentences, specific, no flattery",
  "weakest_areas": ["short phrase, max 3 entries"]
}"""


def hr_simulate(
    jd_title: str,
    jd_company: str,
    jd_text: str,
    resume_text: str,
    model: Optional[str] = None,
) -> dict:
    """Returns {hr_score, rationale, weakest_areas} or {} on failure."""
    model = model or os.environ.get("SCORING_MODEL", "anthropic/claude-haiku-4.5")
    # Resume text was previously truncated at 8000 chars — too tight for a
    # 14-year resume with 7+ roles. The HR scorer was complaining the resume
    # "cuts off mid-sentence at final role" because of this. 32000 chars
    # (~8000 tokens) is well within Haiku's 200K context budget and gives
    # the model the full employment history.
    user = f"""ROLE: {jd_title}
COMPANY: {jd_company}

JOB DESCRIPTION:
{jd_text[:8000] if jd_text else "(JD not provided — score conservatively)"}

CANDIDATE RESUME (plain text):
{resume_text[:32000] if resume_text else "(empty)"}

Score now."""
    try:
        raw = chat(system=HR_SYSTEM, user=user, model=model, max_tokens=500)
    except Exception as e:
        log.warning("hr_score API error: %s", e)
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except Exception as e:
        log.warning("hr_score JSON decode failed: %s | text=%s", e, text[:200])
        return {}
    score = parsed.get("hr_score")
    if not isinstance(score, (int, float)):
        return {}
    weak = parsed.get("weakest_areas") or []
    if not isinstance(weak, list):
        weak = []
    return {
        "hr_score": int(score),
        "rationale": str(parsed.get("rationale", "")).strip(),
        "weakest_areas": [str(x).strip() for x in weak if isinstance(x, str)][:5],
    }
