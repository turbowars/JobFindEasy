"""Cross-module utilities.

Single source of truth for project paths, output directories, dash scrubbing,
and filename-safe string handling. Anything imported from here MUST NOT have
its own duplicate implementation elsewhere — that's the whole point.

If you find yourself reaching for `Path(__file__).parent.parent.parent` or
`re.sub(r"[^A-Za-z0-9]+", "_", ...)` somewhere else, import from here instead.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).parent.parent
"""Repo root. Use this instead of recomputing parent.parent.parent walks."""

OUTPUT_DIR: Path = PROJECT_ROOT / "data" / "exports"
"""Where generated .docx artifacts (resumes, cover letters) and their
.scores.json sidecars are written."""


# ---------------------------------------------------------------------------
# String scrubbing
# ---------------------------------------------------------------------------

_DASH_RE = re.compile(r"\s*[—–]\s*")
_DOUBLE_SPACE_RE = re.compile(r"  +")


def scrub_dashes(s: str) -> str:
    """Replace em (—) and en (–) dashes with hyphen-with-spaces.

    Resume formatting rule: no em/en dashes anywhere in the rendered output.
    Idempotent. Returns the input unchanged if it's empty/None.
    """
    if not s:
        return s
    s = _DASH_RE.sub(" - ", s)
    return _DOUBLE_SPACE_RE.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# Filename-safe strings
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")
_LOC_SUFFIX_MAX = 30  # chars before we truncate + hash
_LOC_HASH_LEN = 6  # md5 prefix length when truncating


def safe_filename_part(s: str) -> str:
    """Convert an arbitrary string (job title, company, etc.) into a
    filesystem-safe slug: non-alphanumerics collapse to underscore, leading
    and trailing underscores are stripped.

    >>> safe_filename_part("Engineering Manager, Identity Frontend")
    'Engineering_Manager_Identity_Frontend'
    """
    if not s:
        return ""
    return _NON_ALNUM_RE.sub("_", s).strip("_")


def safe_loc_suffix(location: str) -> str:
    """Build a `_<location>` suffix for the resume filename.

    Truncates at 30 characters for readability, but appends a 6-char content
    hash when truncation happens so two distinct long locations cannot
    collide on the same prefix (e.g. "New York City, New York State..." vs
    "New York City, New York Store..."). Returns "" when location is empty.
    """
    if not location:
        return ""
    raw = safe_filename_part(location)
    if not raw:
        return ""
    if len(raw) <= _LOC_SUFFIX_MAX:
        return f"_{raw}"
    digest = hashlib.md5(location.encode()).hexdigest()[:_LOC_HASH_LEN]
    return f"_{raw[: _LOC_SUFFIX_MAX - _LOC_HASH_LEN - 1]}_{digest}"


# ---------------------------------------------------------------------------
# Public-folder mirror — used by both resume and cover-letter pipelines so the
# generated .docx + .scores.json sidecar are accessible outside the repo.
# ---------------------------------------------------------------------------


def mirror_to_public(src_path: Path) -> Path | None:
    """Copy a generated artifact to the user's public folder.

    Target dir is `$PUBLIC_EXPORT_DIR` if set, else `~/Public/JobFindEasy`.
    Returns the destination path on success, or None if the copy failed
    (best-effort — generation is the primary action; mirroring is bonus).
    """
    target_dir = Path(
        os.environ.get("PUBLIC_EXPORT_DIR") or (Path.home() / "Public" / "JobFindEasy")
    ).expanduser()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / src_path.name
        shutil.copy2(src_path, dest)
        return dest
    except Exception:
        return None
