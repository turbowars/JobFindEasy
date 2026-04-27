"""Canonical Job record shape used across scrapers, scoring, and UI."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


# Common job-title abbreviations that scrapers see differently across sources.
# Normalized BEFORE hashing so "Sr. Engineer" and "Senior Engineer" produce the
# same hash (and don't end up as separate rows in the DB).
_TITLE_ABBREV = {
    r"\bsr\.?\b": "senior",
    r"\bjr\.?\b": "junior",
    r"\bmgr\.?\b": "manager",
    r"\beng\.?\b": "engineer",
    r"\bengr\.?\b": "engineer",
    r"\bdev\b": "developer",
    r"\bvp\b": "vice president",
    r"\bdir\.?\b": "director",
    r"\bswe\b": "software engineer",
}
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize_for_hash(s: str) -> str:
    """Lowercase, expand common abbreviations, strip non-alphanumerics, collapse
    whitespace. Used by `Job.compute_hash` so superficial title variants
    ("Sr.", "Senior", "Sr") don't create duplicate rows."""
    if not s:
        return ""
    s = s.lower().strip()
    for pattern, replacement in _TITLE_ABBREV.items():
        s = re.sub(pattern, replacement, s)
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


@dataclass
class Job:
    # Identity
    source: str                        # "greenhouse" | "lever" | "ashby" | "linkedin"
    company: str
    title: str
    location: str
    url: str

    # Content
    description: str = ""              # raw JD text
    posted_at: Optional[str] = None    # ISO8601 string
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    remote: Optional[bool] = None

    # Enrichment (filled later)
    sponsorship_status: str = "unknown"  # "offered" | "denied" | "unknown"
    prefilter_passed: bool = False
    prefilter_reason: str = ""
    score_total: Optional[int] = None    # 0-100
    score_breakdown: str = ""            # JSON string of subscores
    score_rationale: str = ""            # 1-2 sentence why
    tier: str = ""                       # "strong" | "possible" | "stretch" | "skip"

    # Tracking
    applied_at: Optional[str] = None
    notes: str = ""

    # Bookkeeping
    scraped_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = self.compute_hash()

    def compute_hash(self) -> str:
        """Stable identity. Same posting on two days = same hash.

        Inputs are normalized (lowercase, abbrev-expanded, punctuation-stripped,
        whitespace-collapsed) before hashing, so superficial title variants
        like 'Sr. Engineer' / 'Senior Engineer' / 'Senior  Engineer' all map
        to the same hash and don't end up as separate rows.
        """
        key = "|".join(
            _normalize_for_hash(part) for part in (
                self.source, self.company, self.title, self.location
            )
        ).encode()
        return hashlib.sha256(key).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)
