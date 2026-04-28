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

import hashlib
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ..llm import chat
from ..enrichment.ats_match import extract_keywords, match_keywords
from ..enrichment.hr_score import hr_simulate
from ..generate import mirror_to_public
from . import profile, prompts
from .template import Resume, Experience, Project, SkillCategory, flatten_for_match
from .docx_builder import build_docx

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "exports"

# `[—–]` -> ` - `; resume rules forbid em/en dashes outside dates, and the
# date field comes from the locked profile so we can scrub everywhere else.
_DASH = re.compile(r"\s*[—–]\s*")


def _scrub(s: str) -> str:
    if not s:
        return s
    s = _DASH.sub(" - ", s)
    return re.sub(r"  +", " ", s).strip()


# ---------------------------------------------------------------------------
# Build a Resume from LLM output + locked profile.
# ---------------------------------------------------------------------------

def _experience_from_llm(llm_exp: list[dict]) -> list[Experience]:
    """For each role in profile.EXPERIENCE, attach the LLM-selected bullets.

    Roles missing from the LLM response fall back to the first 3 bullets in
    the pool — defensive default so a malformed response still produces a
    coherent resume.
    """
    by_key = {e.get("key"): e for e in (llm_exp or []) if isinstance(e, dict)}
    out: list[Experience] = []
    for role in profile.EXPERIENCE:
        llm_role = by_key.get(role["key"]) or {}
        bullets = [
            _scrub(b) for b in (llm_role.get("bullets") or []) if b and b.strip()
        ]
        if not bullets:
            bullets = [_scrub(b) for b in role["bullet_pool"][:3]]
        out.append(Experience(
            company=role["company"],
            title=role["title"],
            location=role["location"],
            dates=role["dates"],
            descriptor=role["descriptor"],
            bullets=bullets,
        ))
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
    for cat in (llm_skills or []):
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
            primary.append(SkillCategory(
                label=_scrub(label), items=[_scrub(i) for i in kept]
            ))
    if not primary and tail is None:
        return [
            SkillCategory(label=c["label"], items=list(c["items"]))
            for c in profile.SKILLS_MASTER
        ]
    if tail is not None:
        primary.append(tail)
    return primary


def _resume_from_llm(payload: dict) -> Resume:
    summary = _scrub(payload.get("summary") or "")
    highlights = [
        _scrub(h) for h in (payload.get("highlights") or []) if h and h.strip()
    ][:4]
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
        experiences=_experience_from_llm(payload.get("experience") or []),
        projects=_projects_from_llm(payload.get("projects") or []),
        education=list(profile.EDUCATION),
        certifications=certifications,
        skills=_skills_from_llm(payload.get("skills") or []),
    )


# ---------------------------------------------------------------------------
# Filenames + daily cap
# ---------------------------------------------------------------------------

def _safe_loc_suffix(location: str) -> str:
    if not location:
        return ""
    raw = re.sub(r"[^A-Za-z0-9]+", "_", location).strip("_")
    if not raw:
        return ""
    if len(raw) <= 30:
        return f"_{raw}"
    digest = hashlib.md5(location.encode()).hexdigest()[:6]
    return f"_{raw[:23]}_{digest}"


def expected_resume_path(jd_title: str, jd_company: str, location: str = "") -> Path:
    safe_t = re.sub(r"[^A-Za-z0-9]+", "_", jd_title).strip("_")
    safe_c = re.sub(r"[^A-Za-z0-9]+", "_", jd_company).strip("_")
    suffix = _safe_loc_suffix(location)
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


def _build_retry_feedback(prev_pct: float, missing: dict) -> str:
    """Format missing keywords as a retry hint the LLM can act on.

    Required keywords lead, then preferred, then soft. Capped at 30 terms.
    Tells the LLM to (a) weave terms into bullets/skills where natural, or
    (b) drop them into the 'Additional Tools and Technologies' adjacency
    tail when natural placement isn't possible but adjacency holds.
    """
    flat: list[str] = []
    for tier in ("required", "preferred", "soft"):
        for kw in missing.get(tier) or []:
            if kw and kw not in flat:
                flat.append(kw)
    if not flat:
        return (
            f"Previous attempt scored {prev_pct}%, target is 80%. Increase "
            "keyword density: weave more JD-named tools, domains, and "
            "methodologies into highlights, bullets, and skills."
        )
    head = flat[:30]
    return (
        f"Previous attempt scored {prev_pct}%, target is 80%. These JD "
        "keywords did not surface in the resume:\n"
        + ", ".join(head) + ".\n\n"
        "For each missing keyword, decide:\n"
        "1. Can you weave it naturally into a bullet, highlight, or primary "
        "skill category? Do that, using the JD's exact spelling.\n"
        "2. Otherwise, does it pass the adjacency test (would a recruiter "
        "scanning the master tree believe Dheeraj has touched it)? Add it "
        "to the 'Additional Tools and Technologies' skills tail.\n"
        "3. Otherwise, route it to tailoring_report.missing_signals and "
        "keep it off the resume."
    )


def _generate_payload(
    model: str,
    jd_title: str,
    jd_company: str,
    jd_text: str,
    keywords: dict | None = None,
    retry_feedback: str = "",
) -> dict:
    user = prompts.build_user_message(
        jd_title, jd_company, jd_text,
        must_cover=keywords,
        retry_feedback=retry_feedback,
    )
    raw = chat(
        system=prompts.SYSTEM_PROMPT,
        user=user,
        model=model,
        max_tokens=8192,
        cache_system=True,
    )
    return prompts.parse_response(raw)


def _score(resume: Resume, keywords: dict, jd_title: str, jd_company: str, jd_text: str) -> tuple[dict, dict]:
    resume_text = flatten_for_match(resume)
    match = match_keywords(resume_text, keywords)
    hr = hr_simulate(jd_title, jd_company, jd_text, resume_text)
    return match, hr


def generate_resume(
    jd_title: str,
    jd_company: str,
    jd_text: str,
    model: Optional[str] = None,
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
    payload = _generate_payload(model, jd_title, jd_company, jd_text, keywords=keywords)
    resume = _resume_from_llm(payload)
    match, hr = _score(resume, keywords, jd_title, jd_company, jd_text)
    log.info(
        "[scores] attempt=1 ats=%s hr=%s for %s",
        match.get("match_pct"), hr.get("hr_score"), cache_key,
    )

    # `attempts` records every Sonnet call so the sidecar shows whether retry
    # actually fired, regardless of which attempt was kept. `kept` flags the
    # one whose payload was rendered to .docx.
    attempts = [{
        "ats_pct": match.get("match_pct"),
        "hr_score": hr.get("hr_score"),
        "kept": True,
    }]

    # 3. Retry once if ATS is low; keep whichever attempt scores higher.
    if (match.get("match_pct") or 0) < ATS_RETRY_THRESHOLD:
        feedback = _build_retry_feedback(
            match.get("match_pct") or 0, match.get("missing") or {}
        )
        log.info("[retry] regenerating once (ats=%s < %s); feedback: %s",
                 match.get("match_pct"), ATS_RETRY_THRESHOLD, feedback[:200])
        try:
            payload2 = _generate_payload(
                model, jd_title, jd_company, jd_text,
                keywords=keywords, retry_feedback=feedback,
            )
            resume2 = _resume_from_llm(payload2)
            match2, hr2 = _score(resume2, keywords, jd_title, jd_company, jd_text)
            log.info(
                "[scores] attempt=2 ats=%s hr=%s for %s",
                match2.get("match_pct"), hr2.get("hr_score"), cache_key,
            )
            keep_retry = (match2.get("match_pct") or 0) > (match.get("match_pct") or 0)
            attempts[0]["kept"] = not keep_retry
            attempts.append({
                "ats_pct": match2.get("match_pct"),
                "hr_score": hr2.get("hr_score"),
                "kept": keep_retry,
            })
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


def autogen_resume_if_missing(
    jd_title: str, jd_company: str, jd_text: str, location: str = ""
) -> Optional[Path]:
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
            today_resume_count(), cap, jd_title, jd_company,
        )
        return None
    try:
        path, _ = generate_resume(jd_title, jd_company, jd_text, location=location)
        log.info("auto-generated resume: %s", path.name)
        return path
    except Exception as e:
        log.warning(
            "autogen resume failed for %s @ %s (%s): %s",
            jd_title, jd_company, location, e,
        )
        return None
