"""Canonical Resume shape — the dataclasses the docx_builder consumes.

The pipeline merges LOCKED facts from profile.py with LLM-generated content
(summary, highlights, per-role bullets, skills selection, conditional cert)
into one of these `Resume` instances. The docx_builder is the only consumer.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SkillCategory:
    label: str
    items: list[str] = field(default_factory=list)


@dataclass
class Experience:
    company: str        # locked
    title: str          # locked
    location: str       # locked
    dates: str          # locked
    descriptor: str = ""    # locked one-liner about scope (italic in .docx)
    bullets: list[str] = field(default_factory=list)  # selected from pool


@dataclass
class Project:
    name: str
    description: str = ""


@dataclass
class Resume:
    name: str
    contact_line: str
    summary: str                       # 3-4 sentences, no I-140 line
    highlights: list[str]              # 4 bullets, JD-tailored, metric-heavy
    experiences: list[Experience]
    projects: list[Project]            # 3-5 selected by LLM per JD; may be empty
    education: list[str]               # locked
    certifications: list[str]          # always-include + one conditional
    skills: list[SkillCategory]        # filtered + reordered tree


def flatten_for_match(resume: Resume) -> str:
    """Concatenate every renderable field into one searchable blob.

    Used by ATS keyword match and HR simulation (avoids round-tripping through
    .docx + mammoth). Strips `**bold**` markers so partial_ratio sees clean text.
    """
    parts: list[str] = [resume.summary, *resume.highlights]
    for exp in resume.experiences:
        parts.append(f"{exp.company} {exp.title} {exp.location}")
        parts.append(exp.descriptor)
        for b in exp.bullets:
            parts.append(b.replace("**", ""))
    for proj in resume.projects:
        parts.append(f"{proj.name} {proj.description}".replace("**", ""))
    for cat in resume.skills:
        parts.append(f"{cat.label}: {', '.join(cat.items)}")
    parts.extend(resume.education)
    parts.extend(resume.certifications)
    return "\n".join(p for p in parts if p)
