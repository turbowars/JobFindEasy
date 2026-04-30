"""JD-tailored resume generator (lean rewrite).

Public API:
  generate_resume(title, company, jd_text, model=None, location="") -> (Path, report)
  autogen_resume_if_missing(title, company, jd_text, location="") -> Path | None
  expected_resume_path(title, company, location="") -> Path
  existing_resume_path(title, company, location="") -> Path
  today_resume_count() -> int
  daily_cap_reached() -> bool
"""

from .pipeline import (
    autogen_resume_if_missing,
    daily_cap_reached,
    existing_resume_path,
    expected_resume_path,
    generate_resume,
    refine_resume,
    today_resume_count,
)

__all__ = [
    "autogen_resume_if_missing",
    "daily_cap_reached",
    "existing_resume_path",
    "expected_resume_path",
    "generate_resume",
    "today_resume_count",
]
