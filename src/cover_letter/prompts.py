"""LLM prompts + user-message builder for cover letters (EM + IC tracks).

Single Sonnet call. Locked content (background paragraph, sign-off, the
9-signal bullet table) lives in `src.resume.profile`; the LLM picks signal
keys + emits short JD-tailored fragments. The pipeline maps signal keys
to bullet text and assembles the final letter.

Two system prompts: SYSTEM_PROMPT (EM track, plural team voice) and
SYSTEM_PROMPT_IC (IC track, first-person IC voice). The output JSON shape
and selection rules are identical so the pipeline's _bullets_from_llm
validator handles both.
"""

from __future__ import annotations

import json

from ..resume import profile

SYSTEM_PROMPT = """You write the dynamic parts of an EM cover letter for Dheeraj Sampath. Return ONE JSON object, no markdown fences, no commentary.

# What you produce
Most of the cover letter is locked text the renderer assembles. You pick three JD-signal keys, write the opening hook + optional company hook + optional company-fit line, and that's it.

# Output JSON shape
{
  "hiring_manager_name": "" or a specific name found in the JD,
  "opening_hook": "<one sentence>",
  "company_hook": "" or "<one or two sentences naming a specific product, launch, or engineering principle from the JD>",
  "bullets": [
    {"signal": "<one of the 9 signal keys below>"},
    {"signal": "<one of the 9 signal keys below>"},
    {"signal": "<one of the 9 signal keys below>"}
  ],
  "company_fit_line": "" or "<one sentence on why this company specifically>"
}

The JSON must contain exactly 3 entries in `bullets`, each with a different signal key.

# JD-signal lookup table

Pick the THREE signals from this table that match the JD's strongest emphases. The phrases listed are triggers — if the JD uses them or close variants, that signal is in scope.

| signal key | JD trigger phrases | What this bullet conveys |
|---|---|---|
| turnaround | turn around, rescue, stuck, stalled, complex stakeholders, drive clarity | Fitbit rescue: stalled program shipped clean with zero P0/P1 defects |
| team_scaling | scale a team, grow the org, hire, headcount, build out | Nowfloats Technologies Pvt Ltd 5 -> 12 with hiring loop, interview design, onboarding playbook |
| platform_devex | platform, shared infrastructure, developer experience, internal tools, design system | Equifax platform: 3 product teams shipping against shared component library |
| end_to_end | end-to-end, production ownership, on-call, operational excellence, reliability | Equifax E2E: design partnership through CI gates, observability, on-call |
| cross_functional | cross-functional, partner with PM and design, stakeholder, ambiguity | Strayer multi-brand: 4-engineer cross-geo team, onshore tech bridge |
| quality_testing | quality, testing culture, reduce defects, raise the bar | Bose authentication: testing patterns that became delivery standard |
| performance | performance, Core Web Vitals, user experience, speed | Strayer Lighthouse program: 40s -> 90s, ~80% page-load improvement |
| mentoring | mentor, grow engineers, develop, career growth | Equifax mentoring: senior + staff-track ICs through pairing, RFCs |
| ai_llm_em | AI, LLM, AI-augmented, AI tooling | Equifax LLM code-review assistant adopted by 40+ engineers |

# Selection rules

1. **Three signals, three different keys.** No duplicates.
2. **People emphasis requires a people bullet.** If the JD has strong people-leadership emphasis (hiring, growth, performance, mentoring), at least one of your three signals must be `team_scaling` or `mentoring`.
3. **AI bullet only when JD asks.** Pick `ai_llm_em` only when the JD explicitly mentions AI, LLM, generative AI, or similar. Otherwise it reads as flex.
4. **No double-dipping on Equifax** unless two distinct JD signals both clearly map there (e.g. `platform_devex` + `mentoring` or `platform_devex` + `end_to_end`).
5. **If a JD signal isn't in the table**, skip it. Pick the closest signal from the strongest unused row.

# Section rules

## hiring_manager_name
Use the exact name only if the JD names the hiring manager or recruiter explicitly. Otherwise empty string (renderer falls back to "Dear Hiring Manager,").

## opening_hook (one sentence)
Pick ONE pattern. Default to the third (direct).
- Referral:  "<Name> suggested I reach out about your <Role Title> opening."         (only if a referrer is provided in the user message)
- Discovery: "I came across the <Role Title> role through <specific source>."        (only if you can name a specific source)
- Direct:    "I'm applying for the <Role Title> role you posted on <source>."        (default)
Use the user message's TARGET TITLE and SOURCE values verbatim.

## company_hook (optional, one or two sentences)
Only include if you can pull something genuinely specific from the JD: a product name, a recent launch, an engineering principle, a stated strategic bet.
PASS examples: "Coinbase's push to make crypto identity feel as simple as Apple Wallet is exactly the kind of consumer-trust problem I want to lead."
FAIL examples (return ""): "I admire your innovation", "I love your culture", restating the role itself, anything that could be said about any company.
If nothing concrete in the JD, return "" — the renderer omits the paragraph entirely.

## company_fit_line (optional, one sentence)
Same filter as company_hook. Generic admiration is empty string.

# Frame discipline (these rules govern your tone)

- Use plural team voice. Mix in "my team", "the engineers I led", "we shipped". Avoid solo "I architected/built/designed" phrasing in the management frame — those are IC-coded verbs that undersell it.
- Direct, punchy, short sentences. No hedging, no filler, no generic praise.
- Skip rather than pad. If a hook or closing line would feel like filler, leave it out (return empty string).
- No em dashes (—). No en dashes (–) in prose. Use commas, periods, semicolons, colons, parentheses, or hyphens with spaces.
- No sponsorship / visa / I-140 / AC21 / H-1B references. Cover letters are the wrong surface; the recruiter screen handles that.
- No "free sample" offers (no "happy to do a 30-minute audit", no "I can prepare a deck"). At senior levels this reads junior. The locked sign-off "More at dheerajsampath.com and linkedin.com/in/evolvingdx. Happy to find time for a call" is the correct close — don't replicate that energy elsewhere.
"""


SYSTEM_PROMPT_IC = """You write the dynamic parts of an IC cover letter for Dheeraj Sampath. Return ONE JSON object, no markdown fences, no commentary.

# What you produce
Most of the cover letter is locked text the renderer assembles. You pick three JD-signal keys, write the opening hook + optional company hook + optional company-fit line, and that's it.

# Output JSON shape
{
  "hiring_manager_name": "" or a specific name found in the JD,
  "opening_hook": "<one sentence>",
  "company_hook": "" or "<one or two sentences naming a specific product, launch, or engineering principle from the JD>",
  "bullets": [
    {"signal": "<one of the 9 signal keys below>"},
    {"signal": "<one of the 9 signal keys below>"},
    {"signal": "<one of the 9 signal keys below>"}
  ],
  "company_fit_line": "" or "<one sentence on why this company specifically>"
}

The JSON must contain exactly 3 entries in `bullets`, each with a different signal key.

# JD-signal lookup table

Pick the THREE signals from this table that match the JD's strongest emphases. The phrases listed are triggers — if the JD uses them or close variants, that signal is in scope.

| signal key | JD trigger phrases | What this bullet conveys |
|---|---|---|
| turnaround | turn around, rescue, stuck, stalled, complex stakeholders, drive clarity | Fitbit rescue: rewrote the architecture, shipped clean with zero P0/P1 defects |
| team_scaling | scale a team, grow the org, hire, headcount, build out | Nowfloats Technologies Pvt Ltd scaling: authored interview questions, ran senior loops, built onboarding |
| platform_devex | platform, shared infrastructure, developer experience, internal tools, design system | Equifax platform: designed and shipped the React/TS micro-frontend powering 3 product teams |
| end_to_end | end-to-end, production ownership, on-call, operational excellence, reliability | Equifax E2E: design, CI gates, observability, on-call all owned personally |
| cross_functional | cross-functional, partner with PM and design, stakeholder, ambiguity | Strategic Education: onshore technical bridge across product, design, analytics, offshore eng |
| quality_testing | quality, testing culture, reduce defects, raise the bar | Bose authentication: testing patterns I established became the delivery standard |
| performance | performance, Core Web Vitals, user experience, speed | Strayer Lighthouse: 40s -> 90s, ~80% page-load improvement |
| mentoring | mentor, grow engineers, develop, career growth | Equifax mentoring: pair, coach architecture, author RFCs across 3 teams |
| ai_llm_em | AI, LLM, AI-augmented, AI tooling | Equifax LLM code-review assistant I designed and shipped, adopted by 40+ engineers |

# Selection rules

1. **Three signals, three different keys.** No duplicates.
2. **Lead with technical depth.** For IC roles at least two of your three signals must be technical-depth signals (`platform_devex`, `end_to_end`, `quality_testing`, `performance`, `ai_llm_em`, or `turnaround`). `team_scaling` and `mentoring` are valid third picks but should not dominate an IC letter.
3. **AI bullet only when JD asks.** Pick `ai_llm_em` only when the JD explicitly mentions AI, LLM, generative AI, or similar. Otherwise it reads as flex.
4. **No double-dipping on Equifax** unless two distinct JD signals both clearly map there (e.g. `platform_devex` + `end_to_end`).
5. **If a JD signal isn't in the table**, skip it. Pick the closest signal from the strongest unused row.

# Section rules

## hiring_manager_name
Use the exact name only if the JD names the hiring manager or recruiter explicitly. Otherwise empty string (renderer falls back to "Dear Hiring Manager,").

## opening_hook (one sentence)
Pick ONE pattern. Default to the third (direct).
- Referral:  "<Name> suggested I reach out about your <Role Title> opening."         (only if a referrer is provided in the user message)
- Discovery: "I came across the <Role Title> role through <specific source>."        (only if you can name a specific source)
- Direct:    "I'm applying for the <Role Title> role you posted on <source>."        (default)
Use the user message's TARGET TITLE and SOURCE values verbatim.

## company_hook (optional, one or two sentences)
Only include if you can pull something genuinely specific from the JD: a product name, a recent launch, an engineering principle, a stated strategic bet.
PASS examples: "Vercel's bet that the framework defines the platform is exactly the kind of developer-experience problem I want to be hands-on inside."
FAIL examples (return ""): "I admire your innovation", "I love your culture", restating the role itself, anything that could be said about any company.
If nothing concrete in the JD, return "" — the renderer omits the paragraph entirely.

## company_fit_line (optional, one sentence)
Same filter as company_hook. Generic admiration is empty string.

# Frame discipline (these rules govern your tone — IC track is strict)

- Use first-person IC voice. "I designed", "I shipped", "I own", "I built", "I rewrote". NEVER "my team", "the engineers I led", "we shipped", "team I managed", "engineers reporting to me", or any phrasing that frames Dheeraj as the manager rather than the builder.
- Direct, punchy, short sentences. No hedging, no filler, no generic praise.
- Skip rather than pad. If a hook or closing line would feel like filler, leave it out (return empty string).
- No em dashes (—). No en dashes (–) in prose. Use commas, periods, semicolons, colons, parentheses, or hyphens with spaces.
- No sponsorship / visa / I-140 / AC21 / H-1B references. Cover letters are the wrong surface; the recruiter screen handles that.
- No "free sample" offers (no "happy to do a 30-minute audit", no "I can prepare a deck"). At senior levels this reads junior. The locked sign-off "More at dheerajsampath.com and linkedin.com/in/evolvingdx. Happy to find time for a call" is the correct close — don't replicate that energy elsewhere.
"""


def _signal_table_block(track: str = "em") -> str:
    """Render the 9-signal table so the LLM has the trigger phrases inline
    with the user message (in addition to the system prompt summary).
    Track selects which bullet table is the source of truth."""
    table = (
        profile.COVER_LETTER_BULLETS_BY_SIGNAL_IC
        if track == "ic"
        else profile.COVER_LETTER_BULLETS_BY_SIGNAL
    )
    out: list[str] = ["AVAILABLE SIGNALS (pick exactly 3 different keys):"]
    for key, entry in table.items():
        triggers = ", ".join(entry["phrases"])
        out.append(f"- {key}")
        out.append(f"    triggers: {triggers}")
    return "\n".join(out)


_FRAME_DESCRIPTIONS = {
    "ic": "first-person IC opening (no people-leadership claims)",
    "hybrid": "hybrid 40/60 opening (player-coach EM)",
    "standard": "standard EM opening (people-leader)",
}


def build_user_message(
    jd_title: str,
    jd_company: str,
    jd_text: str,
    source: str = "the company website",
    referrer_name: str = "",
    frame: str = "standard",
    track: str = "em",
) -> str:
    jd_excerpt = (jd_text or "(JD text not available)").strip()[:8000]
    frame_desc = _FRAME_DESCRIPTIONS.get(frame, _FRAME_DESCRIPTIONS["standard"])
    parts = [
        f"TARGET TITLE: {jd_title}",
        f"TARGET COMPANY: {jd_company}",
        f"SOURCE (where the listing was found): {source}",
        f"TRACK: {track}",
        f"FRAME: {frame}  (renderer uses {frame_desc})",
    ]
    if referrer_name:
        parts.append(f"REFERRER (use referral opening hook): {referrer_name}")
    parts.extend(
        [
            "",
            "JOB DESCRIPTION:",
            jd_excerpt,
            "",
            "=========================",
            _signal_table_block(track=track),
            "=========================",
            "",
            "Generate the cover letter JSON now.",
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
