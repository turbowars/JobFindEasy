"""EM cover letter generator (lean rewrite, mirrors src/resume/).

Public API:
  generate_cover_letter(title, company, jd_text, model=None,
                        source="the company website",
                        referrer_name="") -> (Path, plain_text)
  expected_cover_letter_path(title, company) -> Path
"""

from .pipeline import (
    autogen_cover_letter_if_missing,
    expected_cover_letter_path,
    generate_cover_letter,
)

__all__ = [
    "autogen_cover_letter_if_missing",
    "expected_cover_letter_path",
    "generate_cover_letter",
]
