"""Cover letter generator — EM + IC tracks (lean, mirrors src/resume/).

Public API:
  generate_cover_letter(title, company, jd_text, model=None,
                        source="the company website",
                        referrer_name="") -> (Path, report)
  expected_cover_letter_path(title, company) -> Path

generate_cover_letter dispatches on detect_track(title): EM-track titles
get the people-leadership template (with frame_check), IC-track titles
get the first-person IC template.
"""

from .pipeline import (
    autogen_cover_letter_if_missing,
    expected_cover_letter_path,
    expected_cover_sidecar_path,
    generate_cover_letter,
)

__all__ = [
    "autogen_cover_letter_if_missing",
    "expected_cover_letter_path",
    "expected_cover_sidecar_path",
    "generate_cover_letter",
]
