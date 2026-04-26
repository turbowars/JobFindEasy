"""LLM-based fit scorer.

Calls Claude Haiku with the 6-dimension rubric ported from
dheeraj-job-search.skill. Returns total + breakdown + 1-sentence rationale.

We use Haiku because the rubric is well-specified and the JD is short.
Sonnet would cost 10x for marginal quality gain.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from ..llm import chat

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are scoring job listings for Dheeraj Sampath, an Engineering Manager based in Austin, TX, with 14+ years of frontend engineering and engineering leadership experience.

Use this exact 100-point rubric. Output JSON only.

CANDIDATE PROFILE
- Current role: Engineering Lead at Equifax (Aug 2022 - present)
- Stack: React, TypeScript, micro-frontend, Module Federation, platform engineering, CI/CD, developer tooling
- Track record: led front-end architecture across 3 cross-functional teams, 10+ launches, 50% scalability gains
- Salary floor: 190000 USD base
- Location: Remote US preferred, Austin hybrid OK
- Visa: requires H-1B sponsorship

TARGET ROLES (priority order)
1. Frontend Engineering Manager (best fit)
2. Technical Engineering Manager
3. Engineering Manager (general)
4. Platform Engineering Manager
5. AI Engineering Manager
6. Director of Engineering
7. VP of Engineering

RUBRIC (total max 100)

1. Title Match (0-30)
   25-30: Frontend EM, Technical EM, AI EM, Platform EM (exact)
   15-24: Engineering Manager, Director of Engineering, VP of Engineering
   5-14:  Head of Engineering, Engineering Lead, Staff Eng Manager
   0-4:   TPM, Program Manager, IC roles, anything else

2. Skills Match (0-25), additive:
   +8 React / TypeScript / frontend stack mentioned
   +7 Micro-frontends / frontend platform / Module Federation
   +5 Explicit people management responsibility
   +3 CI/CD / developer tooling / scalable architecture
   +2 Agile / distributed team experience

3. Leadership Scope (0-15)
   12-15: manages 5-20 engineers, owns roadmap, cross-functional
   7-11:  manages a team but scope unclear or narrow
   3-6:   tech lead with some management
   0-2:   IC or no leadership signal

4. Domain Alignment (0-10)
   8-10: Fintech, platform/infra, SaaS product, consumer tech
   5-7:  Enterprise software, B2B tools, cloud services
   2-4:  Adjacent (healthtech, edtech)
   0-1:  Hardware, embedded, non-software

5. Location / Sponsorship Fit (0-10)
   9-10: Remote US OR Austin, sponsorship offered or not mentioned
   6-8:  Remote US but sponsorship status unclear
   3-5:  Onsite Austin only, or sponsorship ambiguous
   0-2:  Explicitly no sponsorship OR incompatible location

6. Compensation Confidence (0-10)
   8-10: Listed >= 190000 USD base, OR FAANG / known high-comp company
   5-7:  Not listed but company type suggests 190k+ likely
   2-4:  Listed below 190k or seed/Series A startup
   0-1:  Explicitly below floor

TIERS
80-100 = "strong"
60-79  = "possible"
40-59  = "stretch"
<40    = "skip"

OUTPUT FORMAT
Return ONLY a single JSON object, no markdown fences, no commentary:
{
  "title_match": int,
  "skills_match": int,
  "leadership_scope": int,
  "domain_alignment": int,
  "location_fit": int,
  "comp_confidence": int,
  "total": int,
  "tier": "strong" | "possible" | "stretch" | "skip",
  "rationale": "one sentence, max 30 words, why this tier"
}"""


def score_job(client, model: str, title: str, company: str, location: str, description: str, sponsorship: str) -> Optional[dict]:
    """Returns the parsed JSON dict from the model, or None on failure.

    `client` is unused (kept for call-site compatibility); routing goes through
    OpenRouter via src.llm.chat.
    """
    user_msg = f"""COMPANY: {company}
TITLE: {title}
LOCATION: {location}
SPONSORSHIP_STATUS_FROM_PREFILTER: {sponsorship}

JOB DESCRIPTION:
{description[:8000] if description else "(not available - score conservatively)"}"""

    try:
        text = chat(system=SYSTEM_PROMPT, user=user_msg, model=model, max_tokens=600)
    except Exception as e:
        log.warning("LLM scoring API error: %s", e)
        return None

    text = text.strip()
    # Sometimes models still wrap in fences despite instructions
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("LLM scoring JSON decode failed: %s | text=%s", e, text[:200])
        return None

    # Validate
    required = {"title_match", "skills_match", "leadership_scope", "domain_alignment", "location_fit", "comp_confidence", "total", "tier", "rationale"}
    if not required.issubset(parsed.keys()):
        log.warning("LLM scoring missing keys: %s", parsed.keys())
        return None

    return parsed


def compute_tier(total: int) -> str:
    if total >= 80:
        return "strong"
    if total >= 60:
        return "possible"
    if total >= 40:
        return "stretch"
    return "skip"


def make_client():
    """Compatibility shim. The OpenRouter client is created per-request inside
    src.llm.chat, so this just returns None."""
    return None
