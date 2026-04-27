"""Application pipeline status — single source of truth.

Each job has one of seven states. Transitions are unrestricted (the user
is the sole authority for what state a job is in); the UI offers
conveniences for the common forward path but allows arbitrary moves.
"""
from __future__ import annotations

STATUSES = (
    "new",            # default — scraped, possibly scored, no action taken
    "shortlisted",    # manually saved for later
    "applying",       # apply flow in progress (form open)
    "applied",        # form submitted
    "interviewing",   # any active back-and-forth — recruiter, HM, panel
    "offer",          # offer extended
    "closed",         # terminal — see closed_reason
)

CLOSED_REASONS = (
    "rejected",
    "withdrew",
    "ghosted",
    "declined_offer",
    "accepted_elsewhere",
)

STATUS_LABEL = {
    "new": "New",
    "shortlisted": "Shortlisted",
    "applying": "Applying",
    "applied": "Applied",
    "interviewing": "Interviewing",
    "offer": "Offer",
    "closed": "Closed",
}

# Used by the UI for non-color distinction (3-color palette discipline).
STATUS_GLYPH = {
    "new": "○",
    "shortlisted": "★",
    "applying": "▶",
    "applied": "✓",
    "interviewing": "⟳",
    "offer": "◆",
    "closed": "—",
}

ACTIVE = {"shortlisted", "applying", "applied", "interviewing", "offer"}
TERMINAL = {"closed"}

# Sweep window for "applied" rows that have heard nothing back.
GHOST_SWEEP_DAYS = 21


def is_valid_status(s: str) -> bool:
    return s in STATUSES


def is_valid_closed_reason(r: str) -> bool:
    return r in CLOSED_REASONS
