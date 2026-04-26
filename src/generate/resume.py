"""JD-tailored resume generator.

Calls Claude with the dheeraj-resume-generator skill content embedded as
system prompt, asks for structured JSON (headline + bullets + skills), then
assembles a .docx via python-docx following the formatting rules in the skill.

The skill file path is read at runtime so updates to the skill propagate.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

from ..llm import chat

log = logging.getLogger(__name__)

# Skill file path on Dheeraj's machine. If not present, we use a built-in
# fallback profile (still locked to his real experience).
SKILL_PATH_CANDIDATES = [
    Path.home() / ".claude" / "skills" / "dheeraj-resume-generator" / "SKILL.md",
    Path("/mnt/skills/user/dheeraj-resume-generator/SKILL.md"),
]

OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "exports"


def _load_skill() -> str:
    for p in SKILL_PATH_CANDIDATES:
        if p.exists():
            return p.read_text()
    log.warning("resume skill file not found at any candidate path; using built-in fallback")
    return _FALLBACK_SKILL


_FALLBACK_SKILL = """You are generating a resume for Dheeraj Sampath, an Engineering Manager based in Austin, TX.

LOCKED EXPERIENCE (do not invent or alter):
- Equifax, Engineering Lead, Austin TX, Aug 2022 to Present
- Midigator, Front End Architect, Austin TX, Feb 2022 to Aug 2022
- TA Digital, UI Architect, Minneapolis MN, Feb 2018 to Feb 2022
- NowFloats Technologies, Principal Engineer, India, Mar 2017 to Feb 2018
- Deloitte Digital Studio, Senior Engineer, Mumbai, Aug 2014 to Mar 2017
- Neudesic, Consultant, Hyderabad, May 2012 to Aug 2014

EDUCATION
- MBA Digital Entrepreneurship, Strayer University
- B.Tech Computer Science, Mahatma Gandhi Institute of Technology

CONTACT (EM profile)
- Austin, TX
- 248-873-8929
- dheerajsampath@proton.me
- linkedin.com/in/evolvingdx
- dheerajsampath.framer.website
- github.com/turbowars

WRITING RULES
- No em dashes anywhere in the resume
- No en dashes in prose; date ranges with " - " are fine
- Quantify every bullet (number, percent, scope, named tool)

Return JSON only with the structure described in the user message."""


SYSTEM_TEMPLATE = """{skill_content}

============================================================
OUTPUT REQUIREMENT
You must return ONLY a single JSON object. No markdown fences, no commentary.
The JSON must have this exact shape:

{{
  "track": "EM" or "IC",
  "headline": "Slot1  |  Slot2  |  Slot3",
  "summary": "3-4 sentence professional summary, no em dashes",
  "skills": ["category line 1", "category line 2", ...],
  "experience": [
    {{
      "company": "Equifax",
      "title": "the title to print, with mirror parenthetical if EM",
      "location": "Austin, TX",
      "dates": "Aug 2022 - Present",
      "bullets": ["bullet 1", "bullet 2", ...]
    }},
    ... more roles in reverse chronological order
  ],
  "education": [
    "MBA, Digital Entrepreneurship, Strayer University",
    "B.Tech, Computer Science, Mahatma Gandhi Institute of Technology"
  ],
  "tailoring_report": {{
    "profile_used": "EM" or "IC",
    "headline": "the 3-slot headline",
    "title_mirror": "Equifax line title",
    "keyword_match": "X/10 top JD keywords hit",
    "priorities_addressed": ["JD priority 1 -> bullet that covers it", ...],
    "missing_signals": ["anything the JD wants that the candidate's background doesn't cover"]
  }}
}}

Final check before returning:
- No em dashes (—) anywhere
- No en dashes (–) except in date ranges
- Headline slot 1 = JD target title, exact string
- Summary line 1 starts with the same noun phrase as headline slot 1
"""


def _generate_structured(model: str, jd_title: str, jd_company: str, jd_text: str) -> dict:
    user = f"""TARGET TITLE: {jd_title}
TARGET COMPANY: {jd_company}

JOB DESCRIPTION:
{jd_text[:10000] if jd_text else "(JD text not available - score conservatively and ask for any missing JD signals via the missing_signals field)"}

Generate the tailored resume JSON now."""

    skill = _load_skill()
    sys_prompt = SYSTEM_TEMPLATE.format(skill_content=skill)

    text = chat(system=sys_prompt, user=user, model=model, max_tokens=4096).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _scrub_dashes(s: str) -> str:
    """Hard guarantee: replace em/en dashes in prose. Date ranges keep ' - '."""
    if not s:
        return s
    # Replace em dashes with period+space
    s = s.replace("—", ". ")
    # Replace en dashes — but preserve in date ranges (look for context like "2022 - 2024")
    # Simpler: replace all en dashes with " - " (space-hyphen-space) which is fine in dates
    s = s.replace("–", "-")
    return s


def _scrub_recursive(obj):
    if isinstance(obj, str):
        return _scrub_dashes(obj)
    if isinstance(obj, list):
        return [_scrub_recursive(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub_recursive(v) for k, v in obj.items()}
    return obj


def _build_docx(payload: dict, output_path: Path) -> None:
    """Assemble a .docx from the structured payload, following skill formatting rules."""
    doc = Document()

    # Margins 0.7" all sides
    for section in doc.sections:
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)

    # Default font Calibri 11pt body
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    # Name
    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_run = name_p.add_run("Dheeraj Sampath")
    name_run.bold = True
    name_run.font.size = Pt(14)

    # Contact line(s)
    contact_p = doc.add_paragraph()
    contact_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    contact_p.add_run("Austin, TX  |  248-873-8929  |  dheerajsampath@proton.me").font.size = Pt(10)
    links_p = doc.add_paragraph()
    links_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    links_p.add_run("linkedin.com/in/evolvingdx  |  dheerajsampath.framer.website  |  github.com/turbowars").font.size = Pt(10)

    # Headline (3-slot)
    headline_p = doc.add_paragraph()
    headline_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    headline_run = headline_p.add_run(payload["headline"])
    headline_run.bold = True
    headline_run.font.size = Pt(12)

    def add_section_header(text: str):
        p = doc.add_paragraph()
        run = p.add_run(text)
        run.bold = True
        run.font.size = Pt(12)

    # Professional Summary
    add_section_header("Professional Summary")
    doc.add_paragraph(payload["summary"])

    # Skills
    add_section_header("Core Technical Skills")
    for line in payload.get("skills", []):
        doc.add_paragraph(line, style="List Bullet")

    # Experience
    add_section_header("Professional Experience")
    for role in payload.get("experience", []):
        # Title line: bold "Company  |  Title"
        p = doc.add_paragraph()
        title_run = p.add_run(f"{role['company']}  |  {role['title']}")
        title_run.bold = True
        # Location and dates (italic, 10pt)
        meta_p = doc.add_paragraph()
        meta_run = meta_p.add_run(f"{role['location']}  |  {role['dates']}")
        meta_run.italic = True
        meta_run.font.size = Pt(10)
        # Bullets
        for bullet in role.get("bullets", []):
            doc.add_paragraph(bullet, style="List Bullet")

    # Education
    add_section_header("Education & Certifications")
    for line in payload.get("education", []):
        doc.add_paragraph(line, style="List Bullet")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))


def generate_resume(jd_title: str, jd_company: str, jd_text: str, model: Optional[str] = None) -> tuple[Path, dict]:
    """Returns (path_to_docx, tailoring_report_dict)."""
    model = model or os.environ.get("GENERATION_MODEL", "anthropic/claude-sonnet-4.5")

    payload = _generate_structured(model, jd_title, jd_company, jd_text)
    payload = _scrub_recursive(payload)

    safe_title = re.sub(r"[^A-Za-z0-9]+", "_", jd_title).strip("_")
    safe_company = re.sub(r"[^A-Za-z0-9]+", "_", jd_company).strip("_")
    filename = f"Dheeraj_Sampath_{safe_title}_{safe_company}.docx"
    output_path = OUTPUT_DIR / filename
    _build_docx(payload, output_path)

    return output_path, payload.get("tailoring_report", {})
