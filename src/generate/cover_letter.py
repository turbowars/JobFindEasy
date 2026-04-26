"""JD-tailored cover letter generator.

Produces a 3-paragraph cover letter following the writing style rules from
the dheeraj-job-search skill: no em dashes, no en dashes in prose, direct
voice, numbers and named tools.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from docx import Document
from docx.shared import Pt, Inches

from ..llm import chat

log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "exports"

SYSTEM = """You are writing a cover letter for Dheeraj Sampath, an Engineering Manager based in Austin, TX with 14+ years of frontend engineering and engineering leadership experience.

LOCKED EXPERIENCE
- Equifax, Engineering Lead, Austin TX, Aug 2022 to Present (front-end architecture, micro-frontends, Module Federation, 10+ launches, 50% scalability gain, 35% delivery efficiency, 25% build cycle reduction)
- Midigator, Front End Architect, Austin TX (2022, embeddable React UI, 35% time-to-market reduction)
- TA Digital, UI Architect (2018-2022, 400% page load improvement, AI scalable solutions)
- 14+ years total. MBA Digital Entrepreneurship, B.Tech Computer Science.

WRITING STYLE RULES (HARD)
- No em dashes (—) anywhere
- No en dashes (–) in prose
- Replace dashes with period, comma, colon, parentheses, or semicolon
- Direct voice, short sentences
- Quantify when possible (specific numbers from the locked experience)
- No flattery about the company
- No "I am writing to apply for"
- Open with a specific hook tied to the role
- Close with a clear next step

STRUCTURE
- Paragraph 1 (3-4 sentences): hook tied to the specific role + your strongest relevant signal
- Paragraph 2 (4-6 sentences): two concrete achievements with numbers that map to the JD's top 2-3 priorities
- Paragraph 3 (2-3 sentences): why this company specifically + clear next step

OUTPUT
Return ONLY the cover letter text. No greeting line, no signature, no JSON, no markdown. Just three paragraphs separated by blank lines."""


def _scrub(s: str) -> str:
    if not s:
        return s
    return s.replace("—", ". ").replace("–", "-")


def generate_cover_letter(jd_title: str, jd_company: str, jd_text: str, model: Optional[str] = None) -> tuple[Path, str]:
    model = model or os.environ.get("GENERATION_MODEL", "anthropic/claude-sonnet-4.5")

    user = f"""TARGET TITLE: {jd_title}
TARGET COMPANY: {jd_company}

JOB DESCRIPTION:
{jd_text[:8000] if jd_text else "(JD not available - write conservatively)"}

Write the cover letter now."""

    text = chat(system=SYSTEM, user=user, model=model, max_tokens=1500).strip()
    text = _scrub(text)

    # Build .docx
    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    # Header
    doc.add_paragraph("Dheeraj Sampath")
    doc.add_paragraph("Austin, TX  |  248-873-8929  |  dheerajsampath@proton.me")
    doc.add_paragraph("")
    doc.add_paragraph(f"Re: {jd_title} at {jd_company}")
    doc.add_paragraph("")

    for para in text.split("\n\n"):
        para = para.strip()
        if para:
            doc.add_paragraph(para)

    doc.add_paragraph("")
    doc.add_paragraph("Best,")
    doc.add_paragraph("Dheeraj Sampath")

    safe_title = re.sub(r"[^A-Za-z0-9]+", "_", jd_title).strip("_")
    safe_company = re.sub(r"[^A-Za-z0-9]+", "_", jd_company).strip("_")
    filename = f"CoverLetter_Dheeraj_Sampath_{safe_title}_{safe_company}.docx"
    output_path = OUTPUT_DIR / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))

    return output_path, text
