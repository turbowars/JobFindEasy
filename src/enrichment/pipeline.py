"""Shared prefilter → score → persist orchestration.

This sequence (run the regex prefilter, persist it, and — if it passes —
LLM-score and persist that) was duplicated in the dashboard inject route
(`web/app.py`) and the `score` CLI command. The bulk `inject-csv` command
made it a third copy, so per the project's "3+ usages → extract" rule it
lives here once.

The entry point takes the four job fields it needs as keyword arguments
rather than a `Job`, because the callers carry the data in different shapes
(a `Job` dataclass from the inject paths, a sqlite row dict from the `score`
command) and reconstructing a `Job` just to satisfy a signature would risk
silently dropping fields.
"""

from __future__ import annotations

import json
import logging
from typing import TypedDict

from .. import db
from ..llm import get_model
from .llm_scorer import compute_tier, make_client, score_job
from .prefilter import prefilter

log = logging.getLogger(__name__)

# The six rubric dimensions persisted into jobs.score_breakdown. The dashboard
# detail pane reads exactly these keys (web/templates/partials/detail.html);
# keep this list as the single writer-side source of truth.
SCORE_DIMENSIONS = (
    "title_match",
    "skills_match",
    "leadership_scope",
    "domain_alignment",
    "location_fit",
    "comp_confidence",
)


class EnrichResult(TypedDict):
    prefilter_passed: bool
    reason: str
    scored: bool
    tier: str | None
    total: int | None
    score_fail_count: int


def enrich_scored(
    *,
    job_hash: str,
    title: str,
    company: str,
    location: str,
    description: str,
) -> EnrichResult:
    """Prefilter the job, persist the verdict, and LLM-score it if it passes.

    On a successful score the row's score/tier/breakdown are written via
    `db.update_score`. On a scoring failure (LLM error or unparseable
    response) `db.record_score_failure` increments the dead-letter counter so
    a JD that never parses stops burning tokens.

    Idempotent: re-running on an already-prefiltered row recomputes the same
    regex verdict and is safe.
    """
    passed, reason, sponsorship = prefilter(title, description or "")
    db.update_prefilter(job_hash, passed, reason, sponsorship)

    if not passed:
        return EnrichResult(
            prefilter_passed=False,
            reason=reason,
            scored=False,
            tier=None,
            total=None,
            score_fail_count=0,
        )

    result = score_job(
        make_client(),
        get_model("job_scoring"),
        title=title,
        company=company,
        location=location,
        description=description or "",
        sponsorship=sponsorship,
    )

    if not result:
        n = db.record_score_failure(job_hash)
        return EnrichResult(
            prefilter_passed=True,
            reason=reason,
            scored=False,
            tier=None,
            total=None,
            score_fail_count=n,
        )

    total = int(result.get("total", 0))
    tier = result.get("tier") or compute_tier(total)
    breakdown = json.dumps({k: result.get(k) for k in SCORE_DIMENSIONS})
    db.update_score(job_hash, total, breakdown, result.get("rationale", ""), tier)

    return EnrichResult(
        prefilter_passed=True,
        reason=reason,
        scored=True,
        tier=tier,
        total=total,
        score_fail_count=0,
    )
