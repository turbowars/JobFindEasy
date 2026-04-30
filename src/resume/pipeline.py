"""Resume generation orchestrator.

Pipeline:
  1. Build the user message from the JD + locked profile.
  2. ONE Sonnet call -> JSON.
  3. Validate + scrub the LLM output and merge with locked profile -> Resume.
  4. ATS keyword extract + match (Haiku, cached per JD).
  5. HR simulation (Haiku).
  6. Render .docx, write .scores.json sidecar, mirror to public.

No retry loop. No track detection. No headline. Locked titles, locked
companies, locked education. The LLM picks bullets and skills only.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path

from ..enrichment.ats_match import extract_keywords, match_keywords
from ..enrichment.hr_score import hr_simulate
from ..llm import chat
from ..utils import (
    OUTPUT_DIR,
    mirror_to_public,
    safe_filename_part,
    safe_loc_suffix,
    scrub_dashes,
)
from . import profile, prompts
from .docx_builder import build_docx
from .template import Experience, Project, Resume, SkillCategory, flatten_for_match

log = logging.getLogger(__name__)

# Local alias kept for in-module readability; OUTPUT_DIR is the single source.
_scrub = scrub_dashes


# Track detection from JD title. EM-positive matches return "em"; everything
# else defaults to "ic" (Staff/Principal/Senior FE/Software Engineer/Tech Lead).
_EM_TITLE_RE = re.compile(
    r"\b("
    r"engineering\s+manager|"
    r"software\s+engineering\s+manager|"
    r"frontend\s+engineering\s+manager|"
    r"full\s*stack\s+engineering\s+manager|"
    r"product\s+engineering\s+manager|"
    r"web\s+platform\s+engineering\s+manager|"
    r"growth\s+engineering\s+manager|"
    r"director\s+of\s+engineering|"
    r"vp\s+(of\s+)?engineering|"
    r"vice\s+president,?\s+engineering|"
    r"head\s+of\s+engineering|"
    r"senior\s+engineering\s+manager|"
    r"sr\.?\s+engineering\s+manager|"
    r"\bem\b"
    r")",
    re.IGNORECASE,
)


# Common non-EM "Manager" roles that happen to mention "engineering"
# somewhere in the JD title. We exclude these from the co-occurrence
# fallback below so we don't misclassify a Project Manager as EM track.
_NON_EM_MANAGER_RE = re.compile(
    r"\b(project|product|account|customer|sales|marketing|community|program|operations)\s+manager\b",
    re.IGNORECASE,
)


def detect_track(jd_title: str) -> str:
    """Return 'em' for management titles, 'ic' otherwise.

    Two-stage detection:
      1. The primary EM regex catches contiguous patterns
         ("Engineering Manager", "Director of Engineering", "VP Engineering").
      2. Fallback: when the JD title pairs "Manager" with "Engineering" but
         in a non-contiguous shape ("Senior Manager, Platform Engineering"),
         it's still an EM role — Twilio, Stripe, and others phrase EM titles
         this way. We exclude common non-EM Manager titles (Project / Product
         / Account / etc.) so we don't sweep them in.

    Hybrid roles (Player-Coach / Staff EM) match the primary regex and
    route to EM, which is safer because EM resumes carry leadership signals
    IC resumes lack.
    """
    title = jd_title or ""
    if _EM_TITLE_RE.search(title):
        return "em"
    lower = title.lower()
    if "manager" in lower and "engineering" in lower and not _NON_EM_MANAGER_RE.search(title):
        return "em"
    return "ic"


# Separators we strip everything after when deriving the BASE role title for
# the Equifax mirror. JDs use these to tack on team/product qualifiers
# unique to the target company — "Engineering Manager, Autonomous Freight
# Systems", "Director of Engineering - Developer Ecosystem". Those qualifiers
# don't belong on Dheeraj's Equifax line because he never had that team at
# Equifax. Adjective prefixes like "Senior" are kept because they live before
# the role noun, not after.
_TITLE_QUALIFIER_SEP_RE = re.compile(r"\s*,\s*|\s*:\s*|\s*\|\s*|\s+[-–—]\s+")


def _base_role_title(jd_title: str) -> str:
    """Strip the JD's team / product qualifier so the Equifax mirror reads
    as a plausible Equifax role rather than a copy of the target company's
    team name.

    Examples:
      "Engineering Manager, Autonomous Freight Systems" -> "Engineering Manager"
      "Director of Engineering, Developer Ecosystem"    -> "Director of Engineering"
      "Engineering Manager - Platform"                  -> "Engineering Manager"
      "Senior Engineering Manager"                      -> "Senior Engineering Manager"  (no qualifier)
      "VP of Engineering"                               -> "VP of Engineering"
    """
    if not jd_title:
        return ""
    m = _TITLE_QUALIFIER_SEP_RE.search(jd_title)
    if m:
        return jd_title[: m.start()].strip()
    return jd_title.strip()


# Recognized engineering-leadership phrasings — bases that already include
# any of these survive verbatim. Bases that don't (e.g. plain "Senior
# Manager" stripped from "Senior Manager, Platform Engineering") get
# normalized to "Engineering Manager" so the Equifax mirror reads as a
# plausible Equifax role rather than a generic "Senior Manager".
_EM_BASE_PHRASING_RE = re.compile(
    r"\b(engineering|director|vp|vice\s+president|head|chief\s+technology|cto)\b",
    re.IGNORECASE,
)


def _equifax_title_override(jd_title: str, track: str) -> str:
    base = _base_role_title(jd_title)
    if track == "em" and not _EM_BASE_PHRASING_RE.search(base):
        # JD's base title doesn't itself say "engineering" or any equivalent
        # leadership phrasing (e.g., "Senior Manager"). Normalize to the
        # canonical EM form so the Equifax line tells the recruiter the role
        # was an engineering-management role, not a generic "Senior Manager".
        base = "Engineering Manager"
    paren = "Engineering Lead" if track == "em" else "Tech Lead"
    return f"{base} ({paren})"


# ---------------------------------------------------------------------------
# Build a Resume from LLM output + locked profile.
# ---------------------------------------------------------------------------


def _experience_from_llm(llm_exp: list[dict], jd_title: str, track: str) -> list[Experience]:
    """For each role in profile.EXPERIENCE, attach the LLM-selected bullets.

    Equifax title is overridden per track: `[JD title] (Tech Lead)` for IC
    or `[JD title] (Engineering Lead)` for EM. All other role titles are
    locked from profile.EXPERIENCE. Roles missing from the LLM response
    fall back to the first 3 bullets in the pool.
    """
    by_key = {e.get("key"): e for e in (llm_exp or []) if isinstance(e, dict)}
    out: list[Experience] = []
    for role in profile.EXPERIENCE:
        llm_role = by_key.get(role["key"]) or {}
        bullets = [_scrub(b) for b in (llm_role.get("bullets") or []) if b and b.strip()]
        if not bullets:
            bullets = [_scrub(b) for b in role["bullet_pool"][:3]]
        title = (
            _equifax_title_override(jd_title, track) if role["key"] == "equifax" else role["title"]
        )
        out.append(
            Experience(
                company=role["company"],
                title=title,
                location=role["location"],
                dates=role["dates"],
                descriptor=role["descriptor"],
                bullets=bullets,
            )
        )
    return out


def _projects_from_llm(llm_projects: list[dict]) -> list[Project]:
    """Filter to projects whose names match the master pool.

    LLM may rephrase the description; the name must match (case-insensitive,
    whitespace-collapsed) to anchor against fabrication. Up to 5 kept.
    """
    if not llm_projects:
        return []
    master_by_name = {p["name"].lower(): p for p in profile.PROJECTS_MASTER}
    out: list[Project] = []
    seen: set[str] = set()
    for p in llm_projects:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not name:
            continue
        master = master_by_name.get(name.lower())
        if not master or name.lower() in seen:
            continue
        seen.add(name.lower())
        desc = _scrub((p.get("description") or "").strip()) or master["description"]
        out.append(Project(name=master["name"], description=desc))
        if len(out) >= 5:
            break
    return out


_ADJACENCY_TAIL_LABEL = "Additional Skills and Technologies"
_ADJACENCY_TAIL_CAP = 8  # max items the LLM may add via the adjacency tail


def _skills_from_llm(llm_skills: list[dict]) -> list[SkillCategory]:
    """Validate LLM-emitted skills against the master tree.

    Primary categories: items must come from the master tree. The LLM picks
    which categories to keep, in what order, and which items per category.
    Adjacency tail (label == "Additional Tools and Technologies"): items are
    free-form (capped at _ADJACENCY_TAIL_CAP) so the LLM can surface JD
    keywords that aren't in the master tree but pass the prompt's
    adjacency test. The tail is always rendered last when present.
    Falls back to the master tree as-is if the LLM returns nothing usable.
    """
    master_by_label = {c["label"]: set(c["items"]) for c in profile.SKILLS_MASTER}
    primary: list[SkillCategory] = []
    tail: SkillCategory | None = None
    for cat in llm_skills or []:
        if not isinstance(cat, dict):
            continue
        label = (cat.get("label") or "").strip()
        items = [(i or "").strip() for i in (cat.get("items") or []) if i and i.strip()]
        if not label or not items:
            continue
        if label == _ADJACENCY_TAIL_LABEL:
            # Adjacency tail: trust the LLM (the prompt's adjacency test is
            # the contract) but cap to keep the section bounded.
            tail = SkillCategory(
                label=_scrub(label),
                items=[_scrub(i) for i in items[:_ADJACENCY_TAIL_CAP]],
            )
            continue
        master_items = master_by_label.get(label)
        if not master_items:
            continue  # unknown category - drop
        kept = [i for i in items if i in master_items]
        if kept:
            primary.append(SkillCategory(label=_scrub(label), items=[_scrub(i) for i in kept]))
    if not primary and tail is None:
        return [
            SkillCategory(label=c["label"], items=list(c["items"])) for c in profile.SKILLS_MASTER
        ]
    if tail is not None:
        primary.append(tail)
    return primary


def _resume_from_llm(payload: dict, jd_title: str, track: str) -> Resume:
    summary = _scrub(payload.get("summary") or "")
    # Highlights are EM-track only; IC resumes lead with Skills instead.
    highlights = (
        [_scrub(h) for h in (payload.get("highlights") or []) if h and h.strip()][:4]
        if track == "em"
        else []
    )
    cert_pick = (payload.get("conditional_cert") or "cua").lower()
    if cert_pick not in profile.CERTIFICATIONS_CONDITIONAL:
        cert_pick = "cua"
    certifications = list(profile.CERTIFICATIONS_ALWAYS) + [
        profile.CERTIFICATIONS_CONDITIONAL[cert_pick]
    ]

    return Resume(
        name=profile.NAME,
        contact_line=profile.CONTACT_LINE,
        summary=summary,
        highlights=highlights,
        experiences=_experience_from_llm(payload.get("experience") or [], jd_title, track),
        projects=_projects_from_llm(payload.get("projects") or []),
        education=list(profile.EDUCATION),
        certifications=certifications,
        skills=_skills_from_llm(payload.get("skills") or []),
        track=track,
    )


# ---------------------------------------------------------------------------
# Filenames + daily cap
# ---------------------------------------------------------------------------


def expected_resume_path(jd_title: str, jd_company: str, location: str = "") -> Path:
    safe_t = safe_filename_part(jd_title)
    safe_c = safe_filename_part(jd_company)
    suffix = safe_loc_suffix(location)
    return OUTPUT_DIR / f"Dheeraj_Sampath_{safe_t}_{safe_c}{suffix}.docx"


def existing_resume_path(jd_title: str, jd_company: str, location: str = "") -> Path:
    """Returns an existing .docx for this job, accepting either the
    location-suffixed or the legacy (no-suffix) filename. Falls back to the
    canonical (with-location) path if neither exists, so callers can still
    `.exists()` and get False.
    """
    canonical = expected_resume_path(jd_title, jd_company, location)
    if canonical.exists():
        return canonical
    legacy = expected_resume_path(jd_title, jd_company, "")
    if legacy.exists():
        return legacy
    return canonical


def today_resume_count() -> int:
    if not OUTPUT_DIR.exists():
        return 0
    today = date.today()
    n = 0
    for p in OUTPUT_DIR.glob("Dheeraj_Sampath_*.docx"):
        try:
            if datetime.fromtimestamp(p.stat().st_mtime).date() == today:
                n += 1
        except Exception:
            continue
    return n


def daily_cap_reached() -> bool:
    cap = int(os.environ.get("AUTO_RESUME_CAP_PER_DAY", "20"))
    return today_resume_count() >= cap


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ATS_RETRY_THRESHOLD = 80  # retry once if ATS match% comes in below this
HR_RETRY_THRESHOLD = 80  # retry once if HR perspective score comes in below this


def _build_retry_feedback(
    prev_ats: float,
    prev_hr: float,
    missing: dict,
    weakest_areas: list,
) -> str:
    """Compose a retry hint that addresses whichever signal(s) tripped.

    Two independent sections: keyword density (ATS) and content quality
    (HR). Only the failing dimensions are mentioned, so the LLM doesn't
    get noisy feedback. Either or both can fire.
    """
    parts: list[str] = []

    if prev_ats < ATS_RETRY_THRESHOLD:
        flat: list[str] = []
        for tier in ("required", "preferred", "soft"):
            for kw in missing.get(tier) or []:
                if kw and kw not in flat:
                    flat.append(kw)
        if flat:
            parts.append(
                f"ATS keyword match was {prev_ats}% (target: "
                f"{ATS_RETRY_THRESHOLD}%+). These JD keywords did not surface "
                f"in the resume:\n{', '.join(flat[:30])}.\n\n"
                "For each missing keyword, decide:\n"
                "1. Can you weave it naturally into a bullet, highlight, or "
                "primary skill category? Do that, using the JD's exact spelling.\n"
                "2. Otherwise, does it pass the adjacency test (would a "
                "recruiter scanning the master tree believe Dheeraj has "
                "touched it)? Add it to the 'Additional Skills and "
                "Technologies' adjacency tail.\n"
                "3. Otherwise, route it to tailoring_report.missing_signals "
                "and keep it off the resume."
            )
        else:
            parts.append(
                f"ATS keyword match was {prev_ats}% (target: "
                f"{ATS_RETRY_THRESHOLD}%+). Increase keyword density: weave "
                "more JD-named tools, domains, and methodologies into "
                "highlights, bullets, and skills."
            )

    if prev_hr < HR_RETRY_THRESHOLD:
        weak = (
            ", ".join(weakest_areas[:5])
            if weakest_areas
            else "specificity, metrics, JD-priority alignment"
        )
        parts.append(
            f"HR perspective score was {prev_hr} (target: "
            f"{HR_RETRY_THRESHOLD}+). The recruiter flagged these weak "
            f"areas: {weak}.\n\n"
            "Strengthen the resume:\n"
            "- Lead the top 3 bullets per role with content that mirrors "
            "the JD's top 2-3 priorities (use the JD's exact language for "
            "the responsibility / outcome).\n"
            "- Pull more bullets that contain numbers, percentages, scope "
            "sizes, or named tools from the bullet pool. Avoid bullets that "
            "describe activity without a measurable outcome.\n"
            "- Tighten the summary so sentence 1 leads with the JD target "
            "role and the closing sentence states a concrete value Dheeraj "
            "brings to the named target company.\n"
            "- For EM track: highlights must be quantified achievements, "
            "not generic platitudes."
        )

    return "\n\n".join(parts)


def _combined_score(match: dict, hr: dict) -> float:
    """Single number used to choose between attempts. Plain sum so a big
    ATS gain doesn't mask a big HR drop and vice versa."""
    return float(match.get("match_pct") or 0) + float(hr.get("hr_score") or 0)


def _generate_payload(
    model: str,
    jd_title: str,
    jd_company: str,
    jd_text: str,
    track: str = "em",
    keywords: dict | None = None,
    retry_feedback: str = "",
) -> dict:
    user = prompts.build_user_message(
        jd_title,
        jd_company,
        jd_text,
        track=track,
        must_cover=keywords,
        retry_feedback=retry_feedback,
    )
    raw = chat(
        system=prompts.SYSTEM_PROMPT,
        user=user,
        model=model,
        max_tokens=8192,
        # Sonnet at 8192 max_tokens with a long JD + 14k-token system prompt
        # routinely runs 60-90s. The default 60s httpx timeout was failing
        # background-executor generations as silent ReadTimeouts. 240s gives
        # comfortable headroom without holding the thread forever.
        timeout=240.0,
        cache_system=True,
    )
    return prompts.parse_response(raw)


def _score(
    resume: Resume, keywords: dict, jd_title: str, jd_company: str, jd_text: str
) -> tuple[dict, dict]:
    resume_text = flatten_for_match(resume)
    match = match_keywords(resume_text, keywords)
    hr = hr_simulate(jd_title, jd_company, jd_text, resume_text)
    return match, hr


def generate_resume(
    jd_title: str,
    jd_company: str,
    jd_text: str,
    model: str | None = None,
    location: str = "",
) -> tuple[Path, dict]:
    """Generate a tailored resume and return (path, tailoring_report).

    Tailoring report includes ATS keyword match% and HR simulation score.
    Retries the LLM call ONCE if the ATS match comes in below
    ATS_RETRY_THRESHOLD; the better of the two attempts wins.
    """
    model = model or os.environ.get("GENERATION_MODEL", "anthropic/claude-sonnet-4.5")
    cache_key = f"{jd_company}|{jd_title}"

    # 1. Pre-flight ATS keyword extract (cached per JD)
    keywords = extract_keywords(jd_text or "", cache_key)
    log.info(
        "[ats_match] keywords for %s: %d required / %d preferred / %d soft",
        cache_key,
        len(keywords.get("required", [])),
        len(keywords.get("preferred", [])),
        len(keywords.get("soft", [])),
    )

    # 2. First Sonnet call. Pass the extracted ATS keywords as must-cover so
    # the LLM works the exact terms the matcher will check for into skills /
    # bullets / highlights wherever the experience supports them.
    track = detect_track(jd_title)
    log.info("[track] %s for %s", track, cache_key)
    payload = _generate_payload(
        model, jd_title, jd_company, jd_text, track=track, keywords=keywords
    )
    resume = _resume_from_llm(payload, jd_title, track)
    match, hr = _score(resume, keywords, jd_title, jd_company, jd_text)
    log.info(
        "[scores] attempt=1 ats=%s hr=%s for %s",
        match.get("match_pct"),
        hr.get("hr_score"),
        cache_key,
    )

    # `attempts` records every Sonnet call so the sidecar shows whether retry
    # actually fired, regardless of which attempt was kept. `kept` flags the
    # one whose payload was rendered to .docx.
    attempts = [
        {
            "ats_pct": match.get("match_pct"),
            "hr_score": hr.get("hr_score"),
            "kept": True,
        }
    ]

    # 3. Retry once if EITHER ATS or HR is below threshold. The retry feedback
    # addresses whichever signal(s) failed; the kept attempt is the one with
    # the higher combined ATS+HR score (so we don't trade HR quality for ATS
    # keyword padding or vice versa).
    ats_low = (match.get("match_pct") or 0) < ATS_RETRY_THRESHOLD
    hr_low = (hr.get("hr_score") or 100) < HR_RETRY_THRESHOLD
    if ats_low or hr_low:
        feedback = _build_retry_feedback(
            match.get("match_pct") or 0,
            hr.get("hr_score") or 0,
            match.get("missing") or {},
            hr.get("weakest_areas") or [],
        )
        reasons = []
        if ats_low:
            reasons.append(f"ats={match.get('match_pct')}<{ATS_RETRY_THRESHOLD}")
        if hr_low:
            reasons.append(f"hr={hr.get('hr_score')}<{HR_RETRY_THRESHOLD}")
        log.info(
            "[retry] regenerating once (%s); feedback: %s",
            " & ".join(reasons),
            feedback[:200],
        )
        try:
            payload2 = _generate_payload(
                model,
                jd_title,
                jd_company,
                jd_text,
                track=track,
                keywords=keywords,
                retry_feedback=feedback,
            )
            resume2 = _resume_from_llm(payload2, jd_title, track)
            match2, hr2 = _score(resume2, keywords, jd_title, jd_company, jd_text)
            log.info(
                "[scores] attempt=2 ats=%s hr=%s for %s",
                match2.get("match_pct"),
                hr2.get("hr_score"),
                cache_key,
            )
            keep_retry = _combined_score(match2, hr2) > _combined_score(match, hr)
            attempts[0]["kept"] = not keep_retry
            attempts.append(
                {
                    "ats_pct": match2.get("match_pct"),
                    "hr_score": hr2.get("hr_score"),
                    "kept": keep_retry,
                }
            )
            if keep_retry:
                payload, resume, match, hr = payload2, resume2, match2, hr2
        except Exception as e:
            log.warning("[retry] regeneration failed; keeping first attempt: %s", e)
            attempts.append({"error": str(e), "kept": False})

    # If the kept attempt's HR call errored (transient API hiccup), re-run it
    # once on the winning resume so the sidecar isn't blank.
    if not hr or hr.get("hr_score") is None:
        try:
            hr = hr_simulate(jd_title, jd_company, jd_text, flatten_for_match(resume))
            for a in attempts:
                if a.get("kept"):
                    a["hr_score"] = hr.get("hr_score")
        except Exception as e:
            log.warning("[hr] retry failed: %s", e)

    # 4. Render .docx + sidecar + mirror.
    output_path = expected_resume_path(jd_title, jd_company, location)
    build_docx(resume, output_path)

    scores = {
        "ats_match": match,
        "hr": hr,
        "keywords": keywords,
        "conditional_cert": payload.get("conditional_cert") or "cua",
        "track": track,
        "attempts": attempts,
    }
    sidecar = output_path.with_suffix(".scores.json")
    try:
        sidecar.write_text(json.dumps(scores, indent=2))
    except Exception as e:
        log.warning("could not write scores sidecar: %s", e)

    public_path = mirror_to_public(output_path)
    if public_path:
        log.info("resume mirrored to %s", public_path)
    if sidecar.exists():
        mirror_to_public(sidecar)

    tailoring = payload.get("tailoring_report") or {}
    tailoring["scores"] = scores
    return output_path, tailoring


def refine_resume(
    jd_title: str,
    jd_company: str,
    jd_text: str,
    model: str | None = None,
    location: str = "",
) -> tuple[Path, dict]:
    """User-triggered refinement: read the existing scores sidecar, build
    explicit feedback from prior ATS/HR/missing/weakest_areas, run ONE more
    Sonnet attempt with that feedback, and overwrite the .docx + sidecar
    only if combined ATS+HR improved.

    Why this exists: the in-pipeline retry runs once per generation and
    sometimes regresses (especially when fixing HR weak areas drops ATS
    keywords). This is the manual escape hatch — same comparison rule, but
    the user gets to pull the trigger after seeing the scores.

    Raises FileNotFoundError if no prior resume exists for the given job.
    """
    output_path = expected_resume_path(jd_title, jd_company, location)
    if not output_path.exists():
        raise FileNotFoundError(
            f"No existing resume to refine at {output_path.name}. Generate it first."
        )
    sidecar = output_path.with_suffix(".scores.json")
    if not sidecar.exists():
        raise FileNotFoundError(
            f"No scores sidecar at {sidecar.name}. Refine needs prior scores "
            "to build feedback. Regenerate from scratch instead."
        )

    prior = json.loads(sidecar.read_text())
    prior_match = prior.get("ats_match") or {}
    prior_hr = prior.get("hr") or {}
    prior_attempts = list(prior.get("attempts") or [])
    track = prior.get("track") or detect_track(jd_title)

    # Build the same retry feedback the in-pipeline retry would build,
    # using the prior numbers so the LLM has the full context for what's
    # missing and what HR flagged as weak.
    prev_ats = float(prior_match.get("match_pct") or 0)
    prev_hr = float(prior_hr.get("hr_score") or 0)
    feedback = _build_retry_feedback(
        prev_ats,
        prev_hr,
        prior_match.get("missing") or {},
        prior_hr.get("weakest_areas") or [],
    )
    log.info(
        "[refine] starting (prior ats=%s hr=%s); feedback: %s",
        prev_ats,
        prev_hr,
        feedback[:200],
    )

    # Re-extract keywords using the existing cache so we score the new
    # attempt against the same keyword universe as the prior.
    cache_key = f"{jd_company}|{jd_title}"
    keywords = prior.get("keywords") or extract_keywords(jd_text or "", cache_key)

    model = model or os.environ.get("GENERATION_MODEL", "anthropic/claude-sonnet-4.5")
    payload = _generate_payload(
        model,
        jd_title,
        jd_company,
        jd_text,
        track=track,
        keywords=keywords,
        retry_feedback=feedback,
    )
    resume = _resume_from_llm(payload, jd_title, track)
    match, hr = _score(resume, keywords, jd_title, jd_company, jd_text)

    new_combined = _combined_score(match, hr)
    prior_combined = prev_ats + prev_hr
    log.info(
        "[refine] new ats=%s hr=%s (combined=%s) vs prior combined=%s",
        match.get("match_pct"),
        hr.get("hr_score"),
        new_combined,
        prior_combined,
    )

    refinement_record = {
        "ats_pct": match.get("match_pct"),
        "hr_score": hr.get("hr_score"),
        "kept": new_combined > prior_combined,
        "refinement": True,
    }

    # Mark all prior attempts as not-kept; the latest decision below picks one.
    for a in prior_attempts:
        a["kept"] = False

    if new_combined > prior_combined:
        # Refinement won — overwrite docx + scores
        build_docx(resume, output_path)
        new_attempts = prior_attempts + [refinement_record]
        scores = {
            "ats_match": match,
            "hr": hr,
            "keywords": keywords,
            "conditional_cert": payload.get("conditional_cert")
            or prior.get("conditional_cert")
            or "cua",
            "track": track,
            "attempts": new_attempts,
        }
        sidecar.write_text(json.dumps(scores, indent=2))
        mirror_to_public(output_path)
        if sidecar.exists():
            mirror_to_public(sidecar)
        log.info("[refine] kept refined attempt — wrote %s", output_path.name)
        tailoring = payload.get("tailoring_report") or {}
        tailoring["scores"] = scores
        tailoring["refinement_kept"] = True
        return output_path, tailoring

    # Refinement didn't help — keep prior. Restore the kept flag on the
    # last prior attempt so the sidecar still shows which attempt is on disk.
    if prior_attempts:
        # Whichever was originally kept stays kept. Find it.
        original_kept_idx = next(
            (i for i, a in enumerate(prior.get("attempts") or []) if a.get("kept")),
            len(prior_attempts) - 1,
        )
        for i, a in enumerate(prior_attempts):
            a["kept"] = i == original_kept_idx
    new_attempts = prior_attempts + [refinement_record]
    scores = dict(prior)
    scores["attempts"] = new_attempts
    sidecar.write_text(json.dumps(scores, indent=2))
    mirror_to_public(sidecar)
    log.info("[refine] refinement did not improve combined score — kept prior")
    return output_path, {
        "scores": scores,
        "refinement_kept": False,
        "message": (
            f"Refinement scored {match.get('match_pct')}/{hr.get('hr_score')} "
            f"(combined {new_combined:.0f}); prior was "
            f"{prev_ats:.0f}/{prev_hr:.0f} (combined {prior_combined:.0f}). "
            "Kept prior."
        ),
    }


def autogen_resume_if_missing(
    jd_title: str, jd_company: str, jd_text: str, location: str = ""
) -> Path | None:
    """Generate only if a resume for this title+company+location doesn't exist
    on disk. Respects the per-day cap. Safe to call from any thread.
    """
    existing = existing_resume_path(jd_title, jd_company, location)
    if existing.exists():
        log.info("resume exists, skipping autogen: %s", existing.name)
        return existing
    if daily_cap_reached():
        cap = int(os.environ.get("AUTO_RESUME_CAP_PER_DAY", "20"))
        log.info(
            "daily resume cap reached (%d >= %d) -- skipping autogen for %s @ %s",
            today_resume_count(),
            cap,
            jd_title,
            jd_company,
        )
        return None
    try:
        path, _ = generate_resume(jd_title, jd_company, jd_text, location=location)
        log.info("auto-generated resume: %s", path.name)
        return path
    except Exception as e:
        log.warning(
            "autogen resume failed for %s @ %s (%s): %s",
            jd_title,
            jd_company,
            location,
            e,
        )
        return None
