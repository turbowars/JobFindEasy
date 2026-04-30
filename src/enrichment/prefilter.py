"""Rule-based pre-filter.

Goal: drop ~80% of jobs cheaply (regex only, no LLM) so the scorer
spends tokens on viable candidates only.

A job passes if ALL of:
  - Title looks like an EM/Director/Lead/Staff role (or is ambiguous)
  - Sponsorship is not denied
  - Has at least one frontend/platform/management signal in JD or title

A job fails if ANY of:
  - Title is clearly IC junior/mid (Software Engineer II, Junior Frontend Dev)
  - Title is unrelated (Recruiter, Sales Engineer, Designer, Data Scientist)
  - Sponsorship is denied
"""

from __future__ import annotations

import re

from .sponsorship import detect_sponsorship

# Title patterns we want. Two tracks are in scope:
#   EM:  Engineering Manager / Director / VP / Head of Engineering
#   IC:  Staff / Senior Staff / Principal Engineer (frontend, product,
#        full-stack, software, web platform); Tech Lead variants.
# Bug surfaced 2026-04-30: the EM-only ruleset was rejecting Dheeraj's
# entire IC track — "Staff Software Engineer, Frontend Engineering" etc.
# were falling through to "no title match and weak signals" and never
# reaching the scorer.
TITLE_GOOD = re.compile(
    r"\b("
    # ── EM track ──
    r"engineering\s+manager|"
    r"director\s+of\s+engineering|director,\s*engineering|"
    r"head\s+of\s+engineering|"
    r"vp\s+of\s+engineering|vp,?\s*engineering|"
    r"senior\s+manager,?\s*(software\s+)?engineering|"
    r"sr\.?\s*manager,?\s*(software\s+)?engineering|"
    r"em,?\s*frontend|em,?\s*platform|"
    r"frontend\s+(engineering\s+)?lead|"
    r"engineering\s+lead|"
    r"staff\s+engineer.*manager|"
    r"tech\s+lead\s+manager|"
    r"principal\s+engineer.*manage|"
    # ── IC track: Staff / Principal Frontend / Product / Full-Stack ──
    r"(senior\s+)?staff\s+(frontend|front[\s\-]?end|product|full[\s\-]?stack|software|web\s+platform)\s+engineer|"
    r"principal\s+(frontend|front[\s\-]?end|product|full[\s\-]?stack|software|web\s+platform)\s+engineer|"
    # ── IC track: Senior Frontend/Product/Full-Stack Engineer ──
    r"senior\s+(frontend|front[\s\-]?end|product|full[\s\-]?stack)\s+engineer|"
    # ── IC track: Full-Stack Engineer (any seniority — user explicitly allowed) ──
    r"full[\s\-]?stack\s+engineer|"
    # ── IC track: AI / Forward Deployed Engineer (user explicitly allowed) ──
    r"(senior\s+|staff\s+|principal\s+)?ai\s+engineer|"
    r"(senior\s+|staff\s+|principal\s+)?forward[\s\-]?deployed\s+engineer|"
    # ── Architect track: tech / web / react / frontend architect ──
    r"(senior\s+|staff\s+|principal\s+|lead\s+)?(tech(nical)?|web|react|front[\s\-]?end|frontend)\s+architect|"
    # ── IC track: Tech Lead variants (Dheeraj's actual Equifax line) ──
    r"(staff|principal|senior\s+staff)?\s*(frontend|front[\s\-]?end|full[\s\-]?stack|software\s+engineer)\s+tech\s+lead|"
    r"tech\s+lead,?\s+(frontend|front[\s\-]?end|full[\s\-]?stack|platform)|"
    r"web\s+platform\s+engineer"
    r")\b",
    re.IGNORECASE,
)

# Titles we explicitly don't want
TITLE_BAD = re.compile(
    r"\b("
    r"junior|intern|"
    r"recruiter|"
    r"sales\s+engineer|solutions?\s+engineer|customer\s+engineer|"
    r"product\s+designer|ux\s+designer|"
    r"data\s+scientist|machine\s+learning\s+(engineer|scientist)|ml\s+engineer|"
    r"backend\s+engineer\s+i+|software\s+engineer\s+i+|"
    r"qa\s+engineer|test\s+engineer|"
    r"site\s+reliability|sre\b|devops\s+engineer|"
    r"security\s+engineer|"
    r"hardware|firmware|embedded|"
    r"technical\s+writer|"
    r"product\s+manager|program\s+manager|tpm\b"
    r")\b",
    re.IGNORECASE,
)

# Signals in the JD that the role matches Dheeraj's profile
PROFILE_SIGNALS = [
    re.compile(r"\b(react|typescript|javascript)\b", re.IGNORECASE),
    re.compile(
        r"\b(micro[\s\-]?front[\s\-]?end|module\s+federation|frontend\s+platform)\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(manage|managing|management|lead|leading|leadership)\b.*\b(team|engineers|reports)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(ci/cd|developer\s+tooling|developer\s+experience|dx|internal\s+platform)\b",
        re.IGNORECASE,
    ),
]


def prefilter(title: str, description: str) -> tuple[bool, str, str]:
    """
    Returns (passed, reason, sponsorship_status).
    If passed=False, the job is dropped before LLM scoring.
    """
    sp = detect_sponsorship(description)
    if sp == "denied":
        return False, "sponsorship denied", sp

    if TITLE_BAD.search(title):
        return False, f"title excluded ({title})", sp

    title_match = bool(TITLE_GOOD.search(title))

    # Even if title doesn't match exactly, allow EM-ambiguous titles through
    # if the description has at least 2 profile signals
    signal_count = sum(1 for p in PROFILE_SIGNALS if p.search(description or ""))

    if title_match:
        return True, "title match", sp
    if signal_count >= 2 and "manager" in title.lower():
        return True, f"title manager + {signal_count} profile signals", sp
    if signal_count >= 3:
        return True, f"strong profile signals ({signal_count})", sp

    return False, "no title match and weak signals", sp
