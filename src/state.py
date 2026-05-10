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

from .cover_letter import generate_cover_letter
from .resume import generate_resume, refine_resume

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
_executor: ThreadPoolExecutor | None = None


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
    kind: str,
    title: str,
    company: str,
    jd_text: str,
    *,
    location: str = "",
    job_hash: str = "",
) -> Future:
    """Queue a resume / cover letter / refinement on the shared executor.

    `kind`:
      - "resume"     — fresh end-to-end resume generation
      - "cover"      — cover letter generation
      - "refine"     — feedback-driven refinement of an existing resume
                       (reads prior scores sidecar; only overwrites if the
                        new combined ATS+HR score improves)

    `job_hash` is stored on the generation record so the sidebar tray can
    deep-link each row to the originating job's detail panel.
    """
    if kind == "resume":
        fut = get_executor().submit(generate_resume, title, company, jd_text, location=location)
    elif kind == "refine":
        fut = get_executor().submit(refine_resume, title, company, jd_text, location=location)
    elif kind == "cover":
        fut = get_executor().submit(generate_cover_letter, title, company, jd_text)
    else:
        raise ValueError(f"unknown generation kind: {kind!r}")

    # Always clear the pending marker when the future resolves, regardless
    # of success or failure. Without this, a silently-failed generation
    # leaves the artifacts panel polling "Generating resume…" forever
    # because the only cleanup path was "file appears on disk". Refine
    # writes the resume marker (state.mark_pending(job_hash, "resume"))
    # so we map kind="refine" -> the resume marker for cleanup too.
    pending_kind = "resume" if kind == "refine" else kind
    if job_hash:

        def _on_done(f, hash=job_hash, k=pending_kind, label=kind, t=title, c=company):
            clear_pending(hash, k)
            err = f.exception()
            if err is not None:
                # Log loud — the future swallowed the exception inside the
                # worker thread; this is the user's only visible signal.
                log.error(
                    "[%s] generation FAILED for %s @ %s: %s",
                    label,
                    t,
                    c,
                    err,
                )
                return
            try:
                result = f.result()
                path = result[0] if isinstance(result, tuple) and result else None
                if path:
                    from . import db

                    db.upsert_artifact(
                        hash,
                        k,
                        str(path.absolute()),
                        path.name,
                    )
                    db.record_application_step(
                        hash,
                        f"generate_{k}",
                        "completed",
                        path.name,
                    )
            except Exception as e:
                log.warning("[%s] artifact recording failed for %s @ %s: %s", label, t, c, e)

        fut.add_done_callback(_on_done)

    with _GENERATIONS_LOCK:
        _GENERATIONS.append(
            {
                "id": uuid.uuid4().hex[:8],
                "kind": kind,
                "title": title,
                "company": company,
                "job_hash": job_hash,
                "future": fut,
                "started_at": time.time(),
            }
        )
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


def pending_started_at(job_hash: str, kind: str) -> float | None:
    with _PENDING_LOCK:
        return _PENDING_MARKERS.get((job_hash, kind))


# ---------------------------------------------------------------------------
# Autoscrape thread + state
# ---------------------------------------------------------------------------

AUTOSCRAPE_LOCK = threading.Lock()
_AUTO_STATE: dict | None = None
_AUTO_THREAD: threading.Thread | None = None


def _autoscrape_loop_factory():
    """Lazy-import the runner so this module doesn't pull DB on import."""
    import json

    from . import db
    from .cover_letter import autogen_cover_letter_if_missing
    from .enrichment.llm_scorer import compute_tier, make_client, score_job
    from .enrichment.prefilter import prefilter as run_prefilter
    from .resume import autogen_resume_if_missing
    from .scrapers.runner import run_all_sync

    def _run_pipeline_headless(score_limit: int) -> dict:
        out = {
            "new": 0,
            "skipped": 0,
            "prefilter_pass": 0,
            "scored": 0,
            "auto_resumes": 0,
            "ghosted": 0,
            "error": None,
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
                ok, reason, sponsorship = run_prefilter(j["title"], j["description"] or "")
                db.update_prefilter(j["hash"], ok, reason, sponsorship)
                if ok:
                    out["prefilter_pass"] += 1

            pending = db.get_unscored_passed()[:score_limit]
            if pending:
                from .llm import get_model

                model = get_model("job_scoring")
                client = make_client()
                for j in pending:
                    result = score_job(
                        client,
                        model,
                        title=j["title"],
                        company=j["company"],
                        location=j["location"],
                        description=j["description"] or "",
                        sponsorship=j["sponsorship_status"],
                    )
                    if not result:
                        db.record_score_failure(j["hash"])
                        continue
                    total = int(result.get("total", 0))
                    tier = result.get("tier") or compute_tier(total)
                    breakdown = json.dumps(
                        {
                            k: result.get(k)
                            for k in (
                                "title_match",
                                "skills_match",
                                "leadership_scope",
                                "domain_alignment",
                                "location_fit",
                                "comp_confidence",
                            )
                        }
                    )
                    db.update_score(j["hash"], total, breakdown, result.get("rationale", ""), tier)
                    out["scored"] += 1
                    if (
                        tier == "strong"
                        and total >= 80
                        and out["auto_resumes"] < AUTO_RESUME_CAP_PER_CYCLE
                    ):
                        path = autogen_resume_if_missing(
                            j["title"],
                            j["company"],
                            j["description"] or "",
                            location=j.get("location") or "",
                        )
                        if path:
                            out["auto_resumes"] += 1
                            # Pair with a cover letter for top fits. Helper
                            # dispatches EM/IC internally and silently
                            # swallows errors so the loop keeps going.
                            autogen_cover_letter_if_missing(
                                j["title"],
                                j["company"],
                                j["description"] or "",
                                location=j.get("location") or "",
                            )
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
                        state["force_run_requested"]
                        or (
                            state["last_run_at"] is None
                            or now - state["last_run_at"] >= state["interval_seconds"]
                        )
                    )
                )
                score_limit = state["score_limit"]
                if should_run:
                    state["force_run_requested"] = False
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
                "enabled": True,
                "interval_seconds": 21600,  # 6 hours default
                "score_limit": 50,
                "last_run_at": None,
                "last_started_at": None,
                "last_run_summary": None,
                "last_error": None,
                "next_run_at": None,
                "in_progress": False,
                "force_run_requested": False,
                "run_count": 0,
            }
        if _AUTO_THREAD is None or not _AUTO_THREAD.is_alive():
            _AUTO_THREAD = threading.Thread(
                target=_autoscrape_loop,
                args=(_AUTO_STATE,),
                daemon=True,
                name="jia-autoscrape",
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
    strong = df[(df["score_total"].fillna(0) >= min_score) & (df["tier"] == "strong")].sort_values(
        "score_total", ascending=False
    )
    submitted = 0
    for _, r in strong.iterrows():
        title, company = r["title"], r["company"]
        location = r.get("location") or ""
        path = existing_resume_path(title, company, location)
        if path.exists():
            continue
        submit_generation(
            "resume",
            title,
            company,
            r.get("description") or "",
            location=location,
            job_hash=r["hash"],
        )
        mark_pending(r["hash"], "resume")
        submitted += 1
    return submitted


def submit_all_missing_strong_fit_cover_letters(min_score: int = 80) -> int:
    """Queue cover letter generation for every strong-fit job that doesn't
    already have one on disk. Both EM and IC tracks are generated;
    generate_cover_letter dispatches internally on detect_track.
    Returns count submitted.
    """
    from . import db
    from .cover_letter import expected_cover_letter_path

    df = db.to_dataframe()
    if df.empty:
        return 0
    strong = df[(df["score_total"].fillna(0) >= min_score) & (df["tier"] == "strong")].sort_values(
        "score_total", ascending=False
    )
    submitted = 0
    for _, r in strong.iterrows():
        title, company = r["title"], r["company"]
        path = expected_cover_letter_path(title, company)
        if path.exists():
            continue
        submit_generation(
            "cover",
            title,
            company,
            r.get("description") or "",
            location=r.get("location") or "",
            job_hash=r["hash"],
        )
        mark_pending(r["hash"], "cover")
        submitted += 1
    return submitted
