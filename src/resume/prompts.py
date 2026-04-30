"""LLM prompts for resume tailoring.

Single Sonnet call. The prompt is intentionally short — all locked facts
travel as structured profile data in the user message, not as restated rules.
"""

from __future__ import annotations

import json

from . import profile

SYSTEM_PROMPT = """You produce a tailored resume for Dheeraj Sampath as a single JSON object. No prose around the JSON, no markdown fences.

# Inputs you receive
The user message contains:
- Target job title and target company.
- The job description text.
- Locked facts: years of experience (use this value verbatim, never infer from dates).
- The ATS keyword list (required / preferred / soft tiers): the exact terms the matcher will check for. Surface as many as adjacency allows.
- Locked experience: 7 roles, each with company, title, location, dates, scope descriptor, and a bullet pool to pick from.
- Master project pool: 5 entries with name, description, and tags.
- Master skills tree: ~20 categories with curated items.
- Optional retry feedback when the previous attempt's ATS match was too low.

# Output JSON shape
{
  "summary": "3-4 sentences",
  "highlights": ["bullet 1", "bullet 2", "bullet 3", "bullet 4"],
  "experience": [
    {"key": "equifax",          "bullets": ["..."]},
    {"key": "midigator",        "bullets": ["..."]},
    {"key": "ta_digital_usa",   "bullets": ["..."]},
    {"key": "ta_digital_india", "bullets": ["..."]},
    {"key": "nowfloats",        "bullets": ["..."]},
    {"key": "deloitte",         "bullets": ["..."]},
    {"key": "neudesic",         "bullets": ["..."]}
  ],
  "projects": [{"name": "...", "description": "..."}],
  "skills":   [{"label": "...", "items": ["..."]}],
  "conditional_cert": "cua" | "microsoft",
  "tailoring_report": {
    "priorities_addressed": ["JD priority -> bullet that covers it"],
    "missing_signals":      ["JD asks the profile genuinely cannot support"]
  }
}

# Section rules

## Summary (3-4 sentences)
- Sentence 1 leads with the JD's target role title.
- Final sentence states the value Dheeraj brings to the named target company.
- Years of experience: use the locked value verbatim.
- Plain prose: periods, commas, parentheses, hyphens.

## Highlights (EM track only — return [] for IC)
- EM track: exactly 4 quantified bullets. Each bullet contains a number, percentage, dollar amount, scope size, or named tool. Shape: "Delivered platform features in agile sprints, reducing release cycles 30% while improving reliability." Pull metrics from the bullet pools; lightly rephrase for JD alignment; keep every number unchanged.
- IC track: return an empty array. The IC resume leads with Skills.

## Experience (one entry per locked role, 7 total)
- Pick 4-7 bullets per role from that role's pool. Older roles (Deloitte, Neudesic): 2-3 bullets.
- You may rephrase verbs and emphasis. Every number, percentage, named tool, named product, and named company stays exact.
- Lead each role with the items that mirror the JD's top priorities.

## Projects (3-5 entries, or empty array)
- Pick from the master project pool. Match by `tags` and JD focus.
- Use the master description, lightly rephrased for JD alignment. Preserve all numbers and named tools.
- If fewer than 3 projects fit cleanly, return fewer. If none fit, return [].

## Conditional certification
- "cua" - JDs that lean frontend / UX / accessibility / design / consumer product.
- "microsoft" - JDs that lean Microsoft / .NET / SharePoint / enterprise stack.
- Default "cua" when neither is the clear fit.

## Skills (this is the ATS lever)
The matcher does fuzzy keyword matching across the rendered resume. Target 80%+. Build the skills section in two parts:

### Part A: primary categories (from the master tree)
- Filter to ~10-14 categories. Reorder so JD-emphasized categories surface first.
- Each kept category holds 6-15 items, chosen for relevance.
- Items in primary categories come from the master tree exactly.
- Promote "Domains and Methodologies" near the top when the JD names domain or methodology language (SaaS, DevOps, fintech, B2B, multi-tenant, microservices, ...).

### Part B: adjacency tail - "Additional Skills and Technologies"
This is the deliberate ATS-pad. Append it as the FINAL category in skills whenever the JD names terms (technologies, soft skills, recruiter phrases, methodologies) that aren't in the master tree but are adjacent to Dheeraj's actual experience.

Adjacency test: would a recruiter scanning Dheeraj's master tree and bullets reasonably believe he has touched this term?
- PASS: WebAuthn, Passkeys, FIDO2 (master has OAuth 2.0, OIDC, JWT, Okta, Auth0, federated SSO).
- PASS: Service Mesh, Istio (master has Kubernetes, Helm, Docker).
- PASS: Helm Charts (master has Helm).
- PASS: tRPC, Apollo Federation (master has tRPC, Apollo Server, GraphQL).
- PASS: recruiter-phrase versions of skills he has — "team leadership", "frontend development", "user experience design", "incident resolution", "communication skills", "generative AI tools" — even when the master tree only lists more specific items.
- FAIL: Solidity, Web3, Ethereum, Polygon, Rust, Go (kernel), COBOL, Salesforce Apex, native iOS / Swift, native Android / Kotlin, game engines.

Rules:
- At most 8 items in the tail.
- Use the JD's exact spelling and casing.
- Only include terms named in the JD that pass the adjacency test.
- Terms that fail adjacency go to `tailoring_report.missing_signals`, never into the resume.

# Locked rules (every field)
- Use plain English role titles (no "(IC)", "(EM)", "Individual Contributor", or other track marker).
- The resume omits sponsorship, visa, work authorization, citizenship, H-1B, I-140, and AC21 topics.
- Use periods, commas, parentheses, and hyphens with spaces. Avoid em dashes and en dashes.
- Markdown bold (`**span**`) is allowed in highlights and bullets to emphasize a measurable result or named tool. One or two spans per bullet at most.
- Bullets and projects preserve every number, percentage, named product, named company, and named tool from the source pool.

# Hard targets
- ATS keyword match: 80%+. Use the adjacency tail to push close-call JDs over the line when adjacency is real.
- Truthfulness: never claim a domain Dheeraj hasn't touched. When in doubt, route the term to `missing_signals` and keep it off the resume.
"""


def _experience_block() -> str:
    """Render the locked experience as a structured block the LLM picks from."""
    out: list[str] = []
    for role in profile.EXPERIENCE:
        out.append(
            f"### key: {role['key']}\n"
            f"- Company: {role['company']}\n"
            f"- Title: {role['title']}\n"
            f"- Location: {role['location']}\n"
            f"- Dates: {role['dates']}\n"
            f"- Scope: {role['descriptor']}\n"
            f"- Bullet pool (pick 4-7, may rephrase but preserve numbers/tools):"
        )
        for b in role["bullet_pool"]:
            out.append(f"  • {b}")
    return "\n".join(out)


def _projects_block() -> str:
    out: list[str] = []
    for p in profile.PROJECTS_MASTER:
        tags = ", ".join(p.get("tags") or [])
        out.append(f"### name: {p['name']}\n- Description: {p['description']}\n- Tags: {tags}")
    return "\n".join(out)


def _skills_block() -> str:
    out: list[str] = []
    for cat in profile.SKILLS_MASTER:
        out.append(f"- {cat['label']}: {', '.join(cat['items'])}")
    return "\n".join(out)


def build_user_message(
    jd_title: str,
    jd_company: str,
    jd_text: str,
    track: str = "em",
    must_cover: dict | None = None,
    retry_feedback: str = "",
) -> str:
    jd_excerpt = (jd_text or "(JD text not available)").strip()[:10000]
    track_label = "EM (management)" if track == "em" else "IC (engineering)"
    highlights_directive = (
        "EM track: produce exactly 4 Professional Highlights bullets."
        if track == "em"
        else "IC track: omit Professional Highlights — return an empty list "
        "for the `highlights` field. The IC resume layout leads with "
        "Skills instead."
    )
    parts = [
        f"TARGET TITLE: {jd_title}",
        f"TARGET COMPANY: {jd_company}",
        f"TRACK: {track_label}",
        "",
        "LOCKED FACTS (use exact values; do not infer or undercount):",
        f"- Years of experience: {profile.YEARS_OF_EXPERIENCE}",
        f"- {highlights_directive}",
        "",
        "JOB DESCRIPTION:",
        jd_excerpt,
        "",
    ]
    if must_cover and any(must_cover.get(t) for t in ("required", "preferred", "soft")):
        parts.append(
            "MUST-COVER ATS KEYWORDS (these are the exact terms the ATS scanner is "
            "checking for — surface each one verbatim in skills, a bullet, or a "
            "highlight wherever Dheeraj's experience genuinely supports it):"
        )
        for tier in ("required", "preferred", "soft"):
            items = must_cover.get(tier) or []
            if items:
                parts.append(f"- {tier.capitalize()}: {', '.join(items)}")
        parts.append("")
    if retry_feedback:
        parts.extend(
            [
                "RETRY FEEDBACK (the previous attempt scored low on ATS — fix these):",
                retry_feedback,
                "",
            ]
        )
    parts.extend(
        [
            "=========================",
            "LOCKED EXPERIENCE (pick from these bullet pools):",
            "=========================",
            _experience_block(),
            "",
            "=========================",
            "MASTER PROJECT POOL (pick 3-5 most relevant by 'tags' and JD; or omit if none fit):",
            "=========================",
            _projects_block(),
            "",
            "=========================",
            "MASTER SKILLS TREE (filter and reorder; do not add items not in this tree):",
            "=========================",
            _skills_block(),
            "",
            "Generate the tailored resume JSON now.",
        ]
    )
    return "\n".join(parts)


def parse_response(text: str) -> dict:
    """Strip code fences and parse JSON. Raises on malformed output."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)
