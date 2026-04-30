"""Canonical CoverLetter shape — the dataclass the docx_builder consumes.

The pipeline merges LOCKED facts from `src.resume.profile` with LLM-generated
content (opening hook, optional company hook, three signal-tagged bullets,
optional company-fit line) into one of these instances. The docx_builder
is the only consumer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BulletPick:
    """One of the three body bullets — tagged with the JD signal that drove
    the pick so the user can see the selection rationale."""

    signal: str  # key from profile.COVER_LETTER_BULLETS_BY_SIGNAL
    bullet: str  # rendered bullet text


@dataclass
class CoverLetter:
    # Recipient — locked default is "Hiring Manager" if no name is known.
    hiring_manager_name: str

    # Opening — picked from a locked variant, with role + source filled in.
    opening_hook: str

    # Optional second paragraph: a specific product / launch / engineering
    # principle from the JD that drew Dheeraj in. Empty string if no
    # genuinely-specific signal in the JD (omitted from the rendered .docx).
    company_hook: str

    # Three bullets, each tagged with the JD signal it addresses.
    bullets: list[BulletPick] = field(default_factory=list)

    # Optional final-paragraph one-liner naming a specific reason this
    # company fits. Empty string if the LLM can't produce a non-filler line.
    company_fit_line: str = ""

    # Job context (locked through from caller).
    jd_title: str = ""
    jd_company: str = ""

    # "standard" (people-leadership-dominant JD) or "hybrid" (balanced JD,
    # 40% code / 60% leadership opening). Drives which background paragraph
    # the docx_builder renders.
    frame: str = "standard"

    @property
    def has_company_hook(self) -> bool:
        return bool(self.company_hook and self.company_hook.strip())

    @property
    def has_company_fit_line(self) -> bool:
        return bool(self.company_fit_line and self.company_fit_line.strip())
