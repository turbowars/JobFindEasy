"""Application pipeline status — single source of truth.

Each job has one of nine states. Transitions are unrestricted (the user
is the sole authority for what state a job is in); the UI offers
conveniences for the common forward path but allows arbitrary moves.

Three terminal states:
  - `closed`           — engaged, then ended (see closed_reason)
  - `not_interested`   — never engaged; user triaged out from `new`
  - `no_sponsorship`   — JD denied / does not offer visa sponsorship;
                         distinct from `not_interested` so the user can
                         filter the "I'd take this if they sponsored"
                         pile separately.
"""

from __future__ import annotations

STATUSES = (
    "new",  # default — scraped, possibly scored, no action taken
    "not_interested",  # terminal — user triaged out without engaging
    "no_sponsorship",  # terminal — sponsorship denied / not offered
    "shortlisted",  # manually saved for later
    "blocked_missing_artifacts",  # apply automation paused until resume + cover exist
    "applying",  # apply flow in progress (form open)
    "applied",  # form submitted
    "interviewing",  # any active back-and-forth — recruiter, HM, panel
    "offer",  # offer extended
    "closed",  # terminal — see closed_reason
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
    "not_interested": "Not Interested",
    "no_sponsorship": "No Sponsorship",
    "shortlisted": "Shortlisted",
    "blocked_missing_artifacts": "Blocked: Missing Artifacts",
    "applying": "Applying",
    "applied": "Applied",
    "interviewing": "Interviewing",
    "offer": "Offer",
    "closed": "Closed",
}

# Used by the UI for non-color distinction (3-color palette discipline).
STATUS_GLYPH = {
    "new": "○",
    "not_interested": "⊘",
    "no_sponsorship": "∅",
    "shortlisted": "★",
    "blocked_missing_artifacts": "□",
    "applying": "▶",
    "applied": "✓",
    "interviewing": "⟳",
    "offer": "◆",
    "closed": "—",
}

ACTIVE = {
    "shortlisted",
    "blocked_missing_artifacts",
    "applying",
    "applied",
    "interviewing",
    "offer",
}
TERMINAL = {"closed", "not_interested", "no_sponsorship"}

# Sweep window for "applied" rows that have heard nothing back.
GHOST_SWEEP_DAYS = 21


def is_valid_status(s: str) -> bool:
    return s in STATUSES


def is_valid_closed_reason(r: str) -> bool:
    return r in CLOSED_REASONS
