"""Cover letter generation orchestrator (EM + IC tracks).

Pipeline:
  1. Detect track from JD title (em vs ic).
  2. EM-only: frame check — count people-leadership vs IC signals.
        people >= 2*ic     -> frame = "standard"
        ic >= 2*people     -> raise (skill says "flag back to user")
        otherwise          -> frame = "hybrid"  (player-coach opening)
     IC: frame is always "ic" (skip frame check — IC titles are IC-framed).
  3. Build user message from JD + locked profile + frame hint.
  4. ONE Sonnet call -> JSON.
  5. Validate signal keys against the track-specific 9-signal table.
  6. Build CoverLetter dataclass merging LLM output with locked text.
  7. Render .docx, mirror to public.
  8. Return (path, report) where report describes frame + bullet picks +
     anything the user might want to swap.

No retry loop. No scoring. The skill's discipline checklist is enforced
through the prompt; the bullet anchor (validated signal keys) prevents
fabrication.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..llm import chat
from ..resume import profile
from ..resume.pipeline import detect_track
from ..utils import (
    OUTPUT_DIR,
    mirror_to_public,
    safe_filename_part,
    scrub_dashes,
)
from . import prompts
from .docx_builder import build_docx
from .template import BulletPick, CoverLetter

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Filename
# ---------------------------------------------------------------------------


def expected_cover_letter_path(jd_title: str, jd_company: str) -> Path:
    safe_t = safe_filename_part(jd_title)
    safe_c = safe_filename_part(jd_company)
    return OUTPUT_DIR / f"CoverLetter_Dheeraj_Sampath_{safe_t}_{safe_c}.docx"


# ---------------------------------------------------------------------------
# Frame check
# ---------------------------------------------------------------------------


def _count_phrases(text: str, phrases: tuple[str, ...]) -> int:
    """Case-insensitive phrase count. Each phrase counted once even if it
    appears multiple times — we're measuring breadth of signal, not volume."""
    if not text:
        return 0
    lower = text.lower()
    return sum(1 for p in phrases if p in lower)


def frame_check(jd_text: str) -> str:
    """Return "standard" | "hybrid" | "ic_dominated" per the skill's rule:

    people >= 2*ic     -> standard  (people-leadership-dominant JD)
    ic >= 2*people     -> ic_dominated  (refuse; flag back to user)
    otherwise          -> hybrid  (balanced; use 40%-code/60%-leading opener)
    """
    p = _count_phrases(jd_text, profile.COVER_LETTER_PEOPLE_PHRASES)
    i = _count_phrases(jd_text, profile.COVER_LETTER_IC_PHRASES)
    if p == 0 and i == 0:
        # No signal either way — default to standard, the safer choice for
        # an explicitly EM-titled JD.
        return "standard"
    if p >= 2 * i:
        return "standard"
    if i >= 2 * p:
        return "ic_dominated"
    return "hybrid"


# ---------------------------------------------------------------------------
# LLM output validation
# ---------------------------------------------------------------------------


def _bullet_table_for(track: str) -> dict:
    """Single entry-point for "which signal table does this track use".
    Keeps the EM/IC choice in one place so the validator and any future
    introspection share the same lookup."""
    return (
        profile.COVER_LETTER_BULLETS_BY_SIGNAL_IC
        if track == "ic"
        else profile.COVER_LETTER_BULLETS_BY_SIGNAL
    )


def _bullets_from_llm(llm_bullets: list, track: str = "em") -> list[BulletPick]:
    """Validate that the LLM picked 3 different signal keys from the locked
    table for this track. Maps each key to the canonical bullet text.
    Defensive defaults fill in if the LLM drifts (returns fewer than 3,
    returns unknown keys), so the renderer always has 3 bullets to work
    with."""
    table = _bullet_table_for(track)
    picked: list[BulletPick] = []
    seen: set[str] = set()
    for entry in llm_bullets or []:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("signal") or "").strip()
        if not key or key not in table or key in seen:
            continue
        seen.add(key)
        picked.append(BulletPick(signal=key, bullet=scrub_dashes(table[key]["bullet"])))
        if len(picked) >= 3:
            break
    # Defensive backfill — pull from these defaults in order if the LLM
    # under-delivered. Keeps the rendered letter coherent even on a flaky
    # response. Each filler is a different signal so we don't dup.
    fallback_order = ("end_to_end", "team_scaling", "platform_devex", "cross_functional")
    for key in fallback_order:
        if len(picked) >= 3:
            break
        if key in seen:
            continue
        seen.add(key)
        picked.append(BulletPick(signal=key, bullet=scrub_dashes(table[key]["bullet"])))
    return picked[:3]


def _letter_from_llm(
    payload: dict,
    jd_title: str,
    jd_company: str,
    frame: str,
    track: str = "em",
) -> CoverLetter:
    return CoverLetter(
        hiring_manager_name=scrub_dashes((payload.get("hiring_manager_name") or "").strip()),
        opening_hook=scrub_dashes((payload.get("opening_hook") or "").strip()),
        company_hook=scrub_dashes((payload.get("company_hook") or "").strip()),
        bullets=_bullets_from_llm(payload.get("bullets") or [], track=track),
        company_fit_line=scrub_dashes((payload.get("company_fit_line") or "").strip()),
        jd_title=jd_title,
        jd_company=jd_company,
        frame=frame,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_cover_letter(
    jd_title: str,
    jd_company: str,
    jd_text: str,
    model: str | None = None,
    source: str = "the company website",
    referrer_name: str = "",
) -> tuple[Path, dict]:
    """Generate a cover letter — dispatches on track.

    EM track:
      - Runs frame_check; raises ValueError on IC-dominated JD bodies.
      - Frame is "standard" (people-leadership-dominant) or "hybrid"
        (balanced player-coach).

    IC track:
      - Frame is always "ic" — no frame check (IC titles are always
        IC-framed; signal balance in the JD doesn't change that).

    Returns (output_path, report). The report includes:
      - track: "em" | "ic"
      - frame: "standard" | "hybrid" | "ic"
      - bullet_picks: [{signal, bullet}, ...]  (so caller can show selection
        rationale and swap quickly)
      - source, referrer_name (echo of inputs)
    """
    track = detect_track(jd_title)
    if track == "em":
        frame = frame_check(jd_text)
        if frame == "ic_dominated":
            raise ValueError(
                "JD reads as IC-dominated (technical-depth signals outweigh "
                "people-leadership signals 2:1 or more). Per the skill rules, "
                "flag this back to the user before drafting an EM letter — the "
                "EM frame would undersell the technical signal. Consider an "
                "IC-track template instead."
            )
        system = prompts.SYSTEM_PROMPT
    else:
        # IC track: no frame check (the title is the gate). The renderer
        # uses frame == "ic" to pick the IC background paragraph.
        frame = "ic"
        system = prompts.SYSTEM_PROMPT_IC

    model = model or os.environ.get("GENERATION_MODEL", "anthropic/claude-sonnet-4.5")

    user = prompts.build_user_message(
        jd_title,
        jd_company,
        jd_text,
        source=source,
        referrer_name=referrer_name,
        frame=frame,
        track=track,
    )
    raw = chat(
        system=system,
        user=user,
        model=model,
        max_tokens=1500,
        cache_system=False,  # CL prompt is small, below cache threshold
    )
    payload = prompts.parse_response(raw)
    letter = _letter_from_llm(payload, jd_title, jd_company, frame, track=track)

    output_path = expected_cover_letter_path(jd_title, jd_company)
    build_docx(letter, output_path)

    public_path = mirror_to_public(output_path)
    if public_path:
        log.info("cover letter mirrored to %s", public_path)

    report = {
        "track": track,
        "frame": frame,
        "bullet_picks": [{"signal": b.signal, "bullet": b.bullet} for b in letter.bullets],
        "company_hook_used": letter.has_company_hook,
        "company_fit_line_used": letter.has_company_fit_line,
        "source": source,
        "referrer_name": referrer_name,
        "plain_text": _render_plain_text(letter),
    }
    log.info(
        "[cover_letter] generated: track=%s frame=%s signals=%s",
        track,
        frame,
        ",".join(b.signal for b in letter.bullets),
    )
    return output_path, report


def autogen_cover_letter_if_missing(
    jd_title: str,
    jd_company: str,
    jd_text: str,
    location: str = "",
) -> Path | None:
    """Generate a cover letter only when one for this job doesn't exist on
    disk. Both EM and IC tracks are supported; generate_cover_letter
    dispatches internally. Errors are logged and swallowed so a single
    job's failure can't kill the score loop.

    Returns the path on success (or pre-existing), None on failure.
    """
    existing = expected_cover_letter_path(jd_title, jd_company)
    if existing.exists():
        return existing
    try:
        path, _ = generate_cover_letter(jd_title, jd_company, jd_text)
        log.info("auto-generated cover letter: %s", path.name)
        return path
    except Exception as e:
        log.warning(
            "autogen cover letter failed for %s @ %s: %s",
            jd_title,
            jd_company,
            e,
        )
        return None


def _render_plain_text(letter: CoverLetter) -> str:
    """Plain-text equivalent for previews and tests."""
    background = profile.cover_letter_background_for_frame(letter.frame)
    name = letter.hiring_manager_name.strip()
    greeting = f"Dear {name}," if name else "Dear Hiring Manager,"
    parts = [
        greeting,
        "",
        letter.opening_hook,
    ]
    if letter.has_company_hook:
        parts.extend(["", letter.company_hook])
    parts.extend(["", background, "", profile.COVER_LETTER_BULLETS_LEAD])
    for b in letter.bullets:
        parts.append(f"- {b.bullet}")
    if letter.has_company_fit_line:
        parts.extend(["", letter.company_fit_line])
    parts.extend(["", profile.COVER_LETTER_SIGNOFF, "", profile.COVER_LETTER_CLOSING])
    return "\n".join(parts)
