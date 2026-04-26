"""Canonical Job record shape used across scrapers, scoring, and UI."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


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
    applied: bool = False
    applied_at: Optional[str] = None
    notes: str = ""

    # Bookkeeping
    scraped_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = self.compute_hash()

    def compute_hash(self) -> str:
        """Stable identity. Same posting on two days = same hash."""
        key = f"{self.source}|{self.company.lower()}|{self.title.lower()}|{self.location.lower()}".encode()
        return hashlib.sha256(key).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)
