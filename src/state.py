"""Process-level shared state for resume generation tracking + autoscrape.

Lives outside any specific UI framework so both the Streamlit app (legacy)
and the FastAPI app (new) can read/write the same singletons. State resets
only when the host Python process restarts.

Public surface:
    get_executor()                              → ThreadPoolExecutor
    submit_generation(kind, title, company, jd_text, *, location="")  → Future
    get_generations()                           → list[dict] snapshot
    clear_completed_generations()               → mutates global

    mark_pending(job_hash, kind)
    clear_pending(job_hash, kind)
    pending_started_at(job_hash, kind)          → float | None

    get_autoscrape_state()                      → dict (mutated under lock)
    AUTOSCRAPE_LOCK                             → threading.Lock for callers

    AUTO_RESUME_CAP_PER_CYCLE                   → int
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from .generate.cover_letter import generate_cover_letter
from .resume import generate_resume

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

AUTO_RESUME_CAP_PER_CYCLE = int(os.environ.get("AUTO_RESUME_CAP_PER_CYCLE", "5"))

_GENERATIONS_CAP = 200  # FIFO cap on generation log to bound memory

# ---------------------------------------------------------------------------
# Generations executor + log
# ---------------------------------------------------------------------------

_executor_lock = threading.Lock()
_executor: Optional[ThreadPoolExecutor] = None


def get_executor() -> ThreadPoolExecutor:
    """Process-wide ThreadPoolExecutor (3 workers). Cached singleton."""
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="jia-gen")
    return _executor


_GENERATIONS: list[dict] = []
_GENERATIONS_LOCK = threading.Lock()


def get_generations() -> list[dict]:
    """Snapshot list of generation records (resume + cover letter), most-recent
    first. Returns a copy so callers can iterate without lock contention."""
    with _GENERATIONS_LOCK:
        return list(_GENERATIONS)


def submit_generation(
    kind: str, title: str, company: str, jd_text: str, *, location: str = ""
) -> Future:
    """Queue a resume or cover letter generation on the shared executor.
    `kind` is "resume" or "cover".
    """
    if kind == "resume":
        fut = get_executor().submit(
            generate_resume, title, company, jd_text, location=location
        )
    else:
        fut = get_executor().submit(generate_cover_letter, title, company, jd_text)
    with _GENERATIONS_LOCK:
        _GENERATIONS.append({
            "id": uuid.uuid4().hex[:8],
            "kind": kind,
            "title": title,
            "company": company,
            "future": fut,
            "started_at": time.time(),
        })
        if len(_GENERATIONS) > _GENERATIONS_CAP:
            del _GENERATIONS[: len(_GENERATIONS) - _GENERATIONS_CAP]
    return fut


def clear_completed_generations() -> int:
    """Drop entries whose future has resolved. Returns count remaining."""
    with _GENERATIONS_LOCK:
        _GENERATIONS[:] = [g for g in _GENERATIONS if not g["future"].done()]
        return len(_GENERATIONS)


# ---------------------------------------------------------------------------
# Pending markers — UI-driven progress indicators per job
# ---------------------------------------------------------------------------

_PENDING_MARKERS: dict[tuple[str, str], float] = {}
_PENDING_LOCK = threading.Lock()


def mark_pending(job_hash: str, kind: str) -> None:
    with _PENDING_LOCK:
        _PENDING_MARKERS[(job_hash, kind)] = time.time()


def clear_pending(job_hash: str, kind: str) -> None:
    with _PENDING_LOCK:
        _PENDING_MARKERS.pop((job_hash, kind), None)


def pending_started_at(job_hash: str, kind: str) -> Optional[float]:
    with _PENDING_LOCK:
        return _PENDING_MARKERS.get((job_hash, kind))


# ---------------------------------------------------------------------------
# Autoscrape thread + state
# ---------------------------------------------------------------------------

AUTOSCRAPE_LOCK = threading.Lock()
_AUTO_STATE: Optional[dict] = None
_AUTO_THREAD: Optional[threading.Thread] = None


def _autoscrape_loop_factory():
    """Lazy-import the runner so this module doesn't pull DB on import."""
    from . import db
    from .enrichment.llm_scorer import compute_tier, make_client, score_job
    from .enrichment.prefilter import prefilter as run_prefilter
    from .resume import autogen_resume_if_missing
    from .scrapers.runner import run_all_sync
    import json

    def _run_pipeline_headless(score_limit: int) -> dict:
        out = {
            "new": 0, "skipped": 0, "prefilter_pass": 0,
            "scored": 0, "auto_resumes": 0, "ghosted": 0, "error": None,
        }
        try:
            db.init_db()
            # Ghost-sweep: applied rows older than N days that haven't moved
            # get auto-flipped to closed:ghosted. Cheap single UPDATE.
            from .status import GHOST_SWEEP_DAYS
            ghost_days = int(os.environ.get("GHOST_SWEEP_DAYS", GHOST_SWEEP_DAYS))
            out["ghosted"] = db.sweep_ghosted(ghost_days)

            jobs = run_all_sync()
            if jobs:
                new, skipped = db.upsert_many(jobs)
                out["new"], out["skipped"] = new, skipped

            for j in db.get_unfiltered():
                ok, reason, sponsorship = run_prefilter(
                    j["title"], j["description"] or ""
                )
                db.update_prefilter(j["hash"], ok, reason, sponsorship)
                if ok:
                    out["prefilter_pass"] += 1

            pending = db.get_unscored_passed()[:score_limit]
            if pending:
                model = os.environ.get("SCORING_MODEL", "anthropic/claude-haiku-4.5")
                client = make_client()
                for j in pending:
                    result = score_job(
                        client, model,
                        title=j["title"], company=j["company"], location=j["location"],
                        description=j["description"] or "",
                        sponsorship=j["sponsorship_status"],
                    )
                    if not result:
                        db.record_score_failure(j["hash"])
                        continue
                    total = int(result.get("total", 0))
                    tier = result.get("tier") or compute_tier(total)
                    breakdown = json.dumps({
                        k: result.get(k) for k in (
                            "title_match", "skills_match", "leadership_scope",
                            "domain_alignment", "location_fit", "comp_confidence",
                        )
                    })
                    db.update_score(
                        j["hash"], total, breakdown, result.get("rationale", ""), tier
                    )
                    out["scored"] += 1
                    if (
                        tier == "strong" and total >= 80
                        and out["auto_resumes"] < AUTO_RESUME_CAP_PER_CYCLE
                    ):
                        path = autogen_resume_if_missing(
                            j["title"], j["company"], j["description"] or "",
                            location=j.get("location") or "",
                        )
                        if path:
                            out["auto_resumes"] += 1
        except Exception as e:
            out["error"] = str(e)
        return out

    return _run_pipeline_headless


def _autoscrape_loop(state: dict) -> None:
    """Daemon: every 5s checks if it's time to run; pipeline runs outside lock."""
    pipeline = _autoscrape_loop_factory()
    while True:
        try:
            with AUTOSCRAPE_LOCK:
                now = time.time()
                should_run = (
                    state["enabled"]
                    and not state["in_progress"]
                    and (
                        state["last_run_at"] is None
                        or now - state["last_run_at"] >= state["interval_seconds"]
                    )
                )
                score_limit = state["score_limit"]
                if should_run:
                    state["in_progress"] = True
                    state["last_started_at"] = time.time()
            if should_run:
                summary = pipeline(score_limit)
                with AUTOSCRAPE_LOCK:
                    state["last_run_summary"] = summary
                    state["last_error"] = summary.get("error")
                    state["last_run_at"] = time.time()
                    state["next_run_at"] = state["last_run_at"] + state["interval_seconds"]
                    state["in_progress"] = False
                    state["run_count"] += 1
        except Exception as e:
            with AUTOSCRAPE_LOCK:
                state["last_error"] = f"loop: {e}"
                state["in_progress"] = False
        time.sleep(5)


def get_autoscrape_state() -> dict:
    """Singleton autoscrape state dict. Spawns the daemon on first call.
    Mutate fields under AUTOSCRAPE_LOCK."""
    global _AUTO_STATE, _AUTO_THREAD
    with AUTOSCRAPE_LOCK:
        if _AUTO_STATE is None:
            _AUTO_STATE = {
                "enabled": False,
                "interval_seconds": 21600,  # 6 hours default
                "score_limit": 50,
                "last_run_at": None,
                "last_started_at": None,
                "last_run_summary": None,
                "last_error": None,
                "next_run_at": None,
                "in_progress": False,
                "run_count": 0,
            }
        if _AUTO_THREAD is None or not _AUTO_THREAD.is_alive():
            _AUTO_THREAD = threading.Thread(
                target=_autoscrape_loop, args=(_AUTO_STATE,),
                daemon=True, name="jia-autoscrape",
            )
            _AUTO_THREAD.start()
    return _AUTO_STATE


def submit_all_missing_strong_fits(min_score: int = 80) -> int:
    """Queue resume generation for every strong-fit job that doesn't already
    have a resume on disk. Returns count submitted."""
    from . import db
    from .resume import existing_resume_path

    df = db.to_dataframe()
    if df.empty:
        return 0
    strong = df[
        (df["score_total"].fillna(0) >= min_score) & (df["tier"] == "strong")
    ].sort_values("score_total", ascending=False)
    submitted = 0
    for _, r in strong.iterrows():
        title, company = r["title"], r["company"]
        location = r.get("location") or ""
        path = existing_resume_path(title, company, location)
        if path.exists():
            continue
        submit_generation(
            "resume", title, company, r.get("description") or "", location=location
        )
        mark_pending(r["hash"], "resume")
        submitted += 1
    return submitted
