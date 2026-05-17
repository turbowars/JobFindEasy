"""FastAPI + HTMX UI for JobFindEasy.

Replaces ui/app.py (Streamlit). All backend logic lives in src/ and is shared
with the legacy Streamlit app — this layer is purely presentation.

Run:
    .venv/bin/uvicorn web.app:app --reload --port 8826

Route convention:
    GET  /                          → full page
    GET  /partials/<thing>          → HTMX-swappable HTML fragment
    POST /actions/<thing>           → mutation, returns HTMX swap or HTTP 204

The legacy Streamlit app keeps running on :8501 in parallel during cutover.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

import mammoth  # noqa: E402  (used by partial_artifacts to render .docx previews)

from src import db, state  # noqa: E402
from src.apply import build_claude_prompt  # noqa: E402
from src.cover_letter import (  # noqa: E402
    expected_cover_letter_path,
    expected_cover_sidecar_path,
)
from src.resume import (  # noqa: E402
    existing_resume_path,
    expected_resume_path,
)
from src.resume import profile as resume_profile  # noqa: E402
from src.resume.pipeline import detect_track  # noqa: E402
from src.scrapers.base import BaseScraper  # noqa: E402
from src.utils import safe_filename_part  # noqa: E402

# ---------------------------------------------------------------------------
# App + static + templates
# ---------------------------------------------------------------------------

app = FastAPI(title="JobFindEasy", docs_url="/api/docs", redoc_url=None)

WEB_DIR = ROOT / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")


def _static_url(filename: str) -> str:
    """Return /static/<filename>?v=<mtime> so the browser cache invalidates
    automatically whenever the file is edited. Beats manually bumping a
    `?v=7` query string on every static change.
    """
    p = WEB_DIR / "static" / filename
    try:
        mtime = int(p.stat().st_mtime)
    except FileNotFoundError:
        mtime = 0
    return f"/static/{filename}?v={mtime}"


templates.env.globals["static_url"] = _static_url


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    # Spawn the autoscrape thread so it's running by the time the user opens
    # the page (matches Streamlit behavior).
    state.get_autoscrape_state()
    log.info("JobFindEasy FastAPI server ready on :8826")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIER_MAP = {
    "strong": ("🟢", "STRONG"),
    "possible": ("🟡", "POSSIBLE"),
    "stretch": ("🟠", "STRETCH"),
    "skip": ("⚫", "SKIP"),
}
SPONSOR_MAP = {
    "offered": "✅ Sponsorship offered",
    "denied": "❌ No sponsorship",
    "unknown": "❓ Sponsorship unclear",
}


def _score_color(score: float | None) -> str:
    if score is None:
        return "#6b7280"
    s = int(score)
    if s >= 80:
        return "#10b981"
    if s >= 60:
        return "#f59e0b"
    if s >= 40:
        return "#f97316"
    return "#6b7280"


def _fmt_relative(ts: float | None) -> str:
    if ts is None:
        return "never"
    delta = int(datetime.now().timestamp() - ts)
    if delta < 0:
        return f"in {-delta}s"
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h {(delta % 3600) // 60}m ago"


def _fmt_size(path: Path) -> str:
    if not path.exists():
        return ""
    n = path.stat().st_size
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _fmt_file_age(path: Path) -> str:
    if not path.exists():
        return ""
    age = int(datetime.now().timestamp() - path.stat().st_mtime)
    if age < 60:
        return f"{age}s ago"
    if age < 3600:
        return f"{age // 60}m ago"
    if age < 86400:
        return f"{age // 3600}h ago"
    return f"{age // 86400}d ago"


# HTMX HX-Trigger response header — comma-separated event names that fire
# on document.body when the response lands. Polled containers in index.html
# subscribe to these via `hx-trigger="..., <event> from:body"` so user-driven
# mutations refresh instantly without forcing fast polling.
def _hx_trigger(*events: str) -> dict[str, str]:
    return {"HX-Trigger": ", ".join(events)} if events else {}


def _filtered_df(filters: dict):
    """Pull jobs from DB and apply filters. Returns pandas DataFrame."""
    df = db.to_dataframe()
    if df.empty:
        return df
    if not filters.get("show_rejects"):
        # Same rule as /api/jobs.json: user-curated jobs (status != 'new')
        # always pass the prefilter gate. The user's explicit engagement
        # overrides the regex prefilter's verdict.
        df = df[(df["prefilter_passed"] == 1) | (df["status"] != "new")]
    if filters.get("source"):
        df = df[df["source"].isin(filters["source"])]
    tiers = filters.get("tier", [])
    if tiers:
        scored = [t for t in tiers if t != "(unscored)"]
        include_unscored = "(unscored)" in tiers
        mask = df["tier"].isin(scored)
        if include_unscored:
            mask = mask | df["tier"].fillna("").eq("")
        df = df[mask]
    if filters.get("min_score") is not None:
        df = df[df["score_total"].fillna(0) >= filters["min_score"]]
    if filters.get("sponsor"):
        df = df[df["sponsorship_status"].isin(filters["sponsor"])]
    statuses = filters.get("status")
    if statuses:
        df = df[df["status"].isin(statuses)]
    if filters.get("q"):
        q = filters["q"].lower()
        df = df[
            df["company"].str.lower().str.contains(q, na=False)
            | df["title"].str.lower().str.contains(q, na=False)
        ]
    df = df.sort_values(
        by=["score_total", "scraped_at"],
        ascending=[False, False],
        na_position="last",
    )
    return df


# ---------------------------------------------------------------------------
# Routes — full page
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    df = db.to_dataframe()
    sources = sorted(df["source"].dropna().unique().tolist()) if not df.empty else []
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "sources": sources,
            "tiers": ["strong", "possible", "stretch", "skip", "(unscored)"],
            "sponsorships": ["offered", "unknown", "denied"],
            "total_jobs": len(df),
            "relevant": int((df["prefilter_passed"] == 1).sum()) if not df.empty else 0,
        },
    )


# ---------------------------------------------------------------------------
# Routes — HTMX partials
# ---------------------------------------------------------------------------


@app.get("/partials/detail/{job_hash}", response_class=HTMLResponse)
def partial_detail(request: Request, job_hash: str):
    from src.status import (
        CLOSED_REASONS,
        GHOST_SWEEP_DAYS,
        STATUS_GLYPH,
        STATUS_LABEL,
        STATUSES,
    )

    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    job["_clean_description"] = BaseScraper.clean_html(job.get("description") or "")[:8000]
    job["_score_breakdown"] = (
        json.loads(job["score_breakdown"]) if job.get("score_breakdown") else None
    )
    # Days since the last status transition (used for "applied 3 days ago,
    # auto-ghosts in 18 days" hint).
    days_since_status = None
    if job.get("status_at"):
        try:
            t = datetime.fromisoformat(job["status_at"])
            days_since_status = max(0, int((datetime.utcnow() - t).total_seconds() // 86400))
        except Exception:
            pass
    return templates.TemplateResponse(
        request,
        "partials/detail.html",
        {
            "job": job,
            "TIER_MAP": TIER_MAP,
            "SPONSOR_MAP": SPONSOR_MAP,
            "score_color": _score_color,
            "STATUSES": STATUSES,
            "STATUS_LABEL": STATUS_LABEL,
            "STATUS_GLYPH": STATUS_GLYPH,
            "CLOSED_REASONS": CLOSED_REASONS,
            "GHOST_SWEEP_DAYS": GHOST_SWEEP_DAYS,
            "days_since_status": days_since_status,
        },
    )


@app.get("/partials/artifacts/{job_hash}", response_class=HTMLResponse)
def partial_artifacts(request: Request, job_hash: str):
    """Auto-polled by HTMX every 2.5s while a generation is in flight or a
    recent file exists. Renders the resume + cover letter previews + scores."""
    job = db.get_job(job_hash)
    if not job:
        return HTMLResponse("<div></div>")

    resume_path = existing_resume_path(job["title"], job["company"], job.get("location") or "")
    cover_path = expected_cover_letter_path(job["title"], job["company"])
    pending_resume = state.pending_started_at(job_hash, "resume")
    pending_cover = state.pending_started_at(job_hash, "cover")

    if resume_path.exists():
        state.clear_pending(job_hash, "resume")
        pending_resume = None
    if cover_path.exists():
        state.clear_pending(job_hash, "cover")
        pending_cover = None

    resume_html = None
    scores = None
    if resume_path.exists():
        try:
            with open(resume_path, "rb") as f:
                resume_html = mammoth.convert_to_html(f).value
        except Exception as e:
            resume_html = f"<em>Preview failed: {e}</em>"
        sidecar = resume_path.with_suffix(".scores.json")
        if sidecar.exists():
            try:
                scores = json.loads(sidecar.read_text())
            except Exception:
                pass

    cover_html = None
    if cover_path.exists():
        try:
            with open(cover_path, "rb") as f:
                cover_html = mammoth.convert_to_html(f).value
        except Exception as e:
            cover_html = f"<em>Preview failed: {e}</em>"

    return templates.TemplateResponse(
        request,
        "partials/artifacts.html",
        {
            "job": job,
            "resume_path": resume_path if resume_path.exists() else None,
            "cover_path": cover_path if cover_path.exists() else None,
            "resume_html": resume_html,
            "cover_html": cover_html,
            "scores": scores,
            "pending_resume": pending_resume,
            "pending_cover": pending_cover,
            "fmt_size": _fmt_size,
            "fmt_age": _fmt_file_age,
            "score_color": _score_color,
        },
    )


@app.get("/partials/generations", response_class=HTMLResponse)
def partial_generations(request: Request):
    """Sidebar tray, polled every 2s."""
    gens = state.get_generations()
    in_flight = sum(1 for g in gens if not g["future"].done())
    return templates.TemplateResponse(
        request,
        "partials/generations.html",
        {
            "gens": gens,
            "in_flight": in_flight,
            "now": datetime.now().timestamp(),
        },
    )


def _badges_context() -> dict | None:
    """Compute the data the badges partial renders.

    Returns None when there are no jobs (caller short-circuits to a
    minimal placeholder). Single source of truth for both the GET partial
    and the OOB fragment that mutation endpoints append to their response.
    """
    from src.status import STATUS_GLYPH, STATUS_LABEL, STATUSES

    df = db.to_dataframe()
    if df.empty:
        return None
    strong = int((df["score_total"].fillna(0) >= 80).sum())
    counts = db.get_status_counts()
    # Follow-up: applied >= 7 days ago, status still 'applied' (not yet
    # advanced to interviewing/offer or auto-ghosted).
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
    pending_followup = int(
        (
            (df["status"] == "applied")
            & (df["status_at"].fillna("") < cutoff)
            & (df["status_at"].fillna("") != "")
        ).sum()
    )
    pipeline = [
        {"key": s, "label": STATUS_LABEL[s], "glyph": STATUS_GLYPH[s], "count": counts.get(s, 0)}
        for s in STATUSES
    ]
    # "Target" = the actionable queue: not-yet-triaged + saved + in-flight.
    # Sums in-process so a status rename doesn't silently drop from the count.
    target_statuses = ("new", "shortlisted", "applying")
    target_count = sum(counts.get(s, 0) for s in target_statuses)
    return {
        "strong": strong,
        "applied": counts.get("applied", 0),
        "pending_followup": pending_followup,
        "total": len(df),
        "pipeline": pipeline,
        "target_count": target_count,
    }


@app.get("/partials/badges", response_class=HTMLResponse)
def partial_badges(request: Request):
    """Live count badges + pipeline counts in the sidebar.

    Used for initial page render and the slow-fallback poll (autoscrape
    catch-up). Per-mutation refresh happens via OOB swaps appended to
    action endpoint responses (see ``_badges_oob_html``).
    """
    ctx = _badges_context()
    if ctx is None:
        return HTMLResponse('<div id="badges"><small>0 jobs</small></div>')
    return templates.TemplateResponse(request, "partials/badges.html", ctx)


def _badges_oob_html(request: Request) -> str:
    """Render the badges partial wrapped in an HTMX out-of-band swap container.

    Mutation endpoints append this string to their HTML response body so
    HTMX swaps the sidebar #badges element in place — no extra round trip,
    no polling needed for state-changing user actions.
    """
    ctx = _badges_context()
    if ctx is None:
        return '<div id="badges" hx-swap-oob="true"><small>0 jobs</small></div>'
    inner = templates.get_template("partials/badges.html").render({"request": request, **ctx})
    return f'<div id="badges" hx-swap-oob="true">{inner}</div>'


@app.get("/partials/autoscrape", response_class=HTMLResponse)
def partial_autoscrape(request: Request):
    """Autoscrape status panel, polled every 5s."""
    s = state.get_autoscrape_state()
    with state.AUTOSCRAPE_LOCK:
        snap = dict(s)
    return templates.TemplateResponse(
        request,
        "partials/autoscrape.html",
        {
            "s": snap,
            "fmt_relative": _fmt_relative,
            "intervals": [
                ("15 min", 900),
                ("30 min", 1800),
                ("1 hour", 3600),
                ("3 hours", 10800),
                ("6 hours", 21600),
                ("12 hours", 43200),
            ],
        },
    )


# ---------------------------------------------------------------------------
# Routes — actions (mutations)
# ---------------------------------------------------------------------------


@app.post("/actions/status/{job_hash}", response_class=HTMLResponse)
def action_status(
    request: Request,
    job_hash: str,
    status: str = Form(...),
    closed_reason: str = Form(""),
):
    """Generic status transition. closed_reason is only honored when
    status='closed' (server clears it for any other status).

    Returns the badges partial as an HTMX OOB swap fragment so HTMX-driven
    callers update the sidebar counts in the same round trip.
    """
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    try:
        db.set_status(job_hash, status, closed_reason or None)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return HTMLResponse(_badges_oob_html(request))


@app.post("/actions/apply/{job_hash}")
def action_apply(job_hash: str):
    """Mark a job as 'applying' if it's still in an early-stage status.

    Only transitions from 'new' or 'shortlisted' -> 'applying'. Jobs that
    are already further along the pipeline (applying / applied /
    interviewing / offer / closed) are left untouched, so a user clicking
    the grid's posting link or the "Apply with Claude" button on a
    progressed job doesn't silently undo their state.

    Returns the current status so the client can flash an honest message.
    """
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    current = job.get("status") or "new"
    transitioned = current in ("new", "shortlisted")
    if transitioned:
        db.set_status(job_hash, "applying")
        current = "applying"
    return JSONResponse(
        {
            "ok": True,
            "url": job.get("url") or "",
            "transitioned": transitioned,
            "status": current,
        }
    )


@app.post("/actions/notes/{job_hash}")
def action_notes(job_hash: str, notes: str = Form("")):
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    db.set_notes(job_hash, notes)
    return Response(status_code=204)


@app.post("/actions/generate/{kind}/{job_hash}")
def action_generate(kind: str, job_hash: str):
    if kind not in ("resume", "cover"):
        raise HTTPException(400, "kind must be 'resume' or 'cover'")
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    state.submit_generation(
        kind,
        job["title"],
        job["company"],
        job.get("description") or "",
        location=job.get("location") or "",
        job_hash=job_hash,
    )
    state.mark_pending(job_hash, kind)
    return Response(status_code=204, headers=_hx_trigger("jia-generations-changed"))


@app.post("/actions/regenerate/{kind}/{job_hash}")
def action_regenerate(kind: str, job_hash: str):
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    if kind == "resume":
        path = expected_resume_path(job["title"], job["company"], job.get("location") or "")
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".scores.json").unlink(missing_ok=True)
        except Exception:
            pass
    else:
        path = expected_cover_letter_path(job["title"], job["company"])
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    state.submit_generation(
        kind,
        job["title"],
        job["company"],
        job.get("description") or "",
        location=job.get("location") or "",
        job_hash=job_hash,
    )
    state.mark_pending(job_hash, kind)
    return Response(status_code=204, headers=_hx_trigger("jia-generations-changed"))


@app.post("/actions/refine-resume/{job_hash}")
def action_refine_resume(job_hash: str):
    """Feedback-driven refinement: reads the existing scores sidecar,
    builds explicit retry feedback from prior ATS/HR/missing/weakest_areas,
    runs ONE Sonnet attempt with that feedback, and overwrites the .docx +
    sidecar only if combined ATS+HR improves. Surfaces the attempt in the
    generations tray under kind='refine' so the user sees progress."""
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")

    # Confirm there's something to refine
    resume_path = expected_resume_path(job["title"], job["company"], job.get("location") or "")
    if not resume_path.exists():
        raise HTTPException(409, "no existing resume to refine — generate it first")
    if not resume_path.with_suffix(".scores.json").exists():
        raise HTTPException(
            409,
            "no scores sidecar to seed feedback — regenerate from scratch instead",
        )

    state.submit_generation(
        "refine",
        job["title"],
        job["company"],
        job.get("description") or "",
        location=job.get("location") or "",
        job_hash=job_hash,
    )
    # Use the resume pending-marker so the artifacts panel polls for updates
    # while refinement is in flight (same UX as a fresh generation).
    state.mark_pending(job_hash, "resume")
    return Response(status_code=204, headers=_hx_trigger("jia-generations-changed"))


@app.post("/actions/bulk-generate")
def action_bulk_generate():
    n = state.submit_all_missing_strong_fits()
    return JSONResponse({"queued": n}, headers=_hx_trigger("jia-generations-changed"))


@app.post("/actions/bulk-generate-covers")
def action_bulk_generate_covers():
    """Parallel cover-letter generation for every strong-fit job that
    doesn't already have one. EM and IC tracks are both supported; the
    pipeline dispatches internally. Goes through the same executor as
    resumes (3 workers), so cover-letter and resume jobs interleave
    naturally."""
    n = state.submit_all_missing_strong_fit_cover_letters()
    return JSONResponse({"queued": n}, headers=_hx_trigger("jia-generations-changed"))


@app.post("/actions/inject-url")
def action_inject_url(url: str = Form(...)):
    """Paste-a-link injection. Fetches the URL, LLM-extracts the job fields,
    upserts into the DB, and runs prefilter + score inline so the row is
    fully usable the moment it appears in the grid.
    """
    from src.enrichment.llm_scorer import compute_tier, make_client, score_job
    from src.enrichment.prefilter import prefilter as run_prefilter
    from src.scrapers.url_inject import inject_from_url

    job, status = inject_from_url(url)
    if not job:
        return JSONResponse({"ok": False, "error": status}, status_code=400)

    inserted = db.upsert_job(job)
    if not inserted:
        return JSONResponse(
            {
                "ok": True,
                "hash": job.hash,
                "duplicate": True,
                "title": job.title,
                "company": job.company,
            }
        )

    # NOTE: this prefilter→score block is the same sequence now owned by
    # src.enrichment.pipeline.enrich_scored (used by the CLI). Follow-up:
    # route this route through it too so there's a single copy.
    ok, reason, sponsorship = run_prefilter(job.title, job.description or "")
    db.update_prefilter(job.hash, ok, reason, sponsorship)

    if ok:
        try:
            from src.llm import get_model

            client = make_client()
            model = get_model("job_scoring")
            result = score_job(
                client,
                model,
                title=job.title,
                company=job.company,
                location=job.location,
                description=job.description or "",
                sponsorship=sponsorship,
            )
            if result:
                total = int(result.get("total", 0))
                tier = result.get("tier") or compute_tier(total)
                breakdown = json.dumps(
                    {
                        k: result.get(k)
                        for k in [
                            "title_match",
                            "skills_match",
                            "leadership_scope",
                            "domain_alignment",
                            "location_fit",
                            "comp_confidence",
                        ]
                    }
                )
                db.update_score(job.hash, total, breakdown, result.get("rationale", ""), tier)
        except Exception as e:
            log.warning("inline score after inject failed: %s", e)

    return JSONResponse(
        {
            "ok": True,
            "hash": job.hash,
            "duplicate": False,
            "title": job.title,
            "company": job.company,
            "prefilter_passed": ok,
        }
    )


@app.post("/actions/clear-completed-generations")
def action_clear_completed():
    n = state.clear_completed_generations()
    return JSONResponse({"remaining": n}, headers=_hx_trigger("jia-generations-changed"))


@app.post("/actions/autoscrape/toggle")
def action_autoscrape_toggle(enabled: str = Form(...)):
    s = state.get_autoscrape_state()
    with state.AUTOSCRAPE_LOCK:
        s["enabled"] = enabled == "on"
    return Response(status_code=204, headers=_hx_trigger("jia-autoscrape-changed"))


@app.post("/actions/autoscrape/run")
def action_autoscrape_run():
    s = state.get_autoscrape_state()
    with state.AUTOSCRAPE_LOCK:
        s["enabled"] = True
        s["force_run_requested"] = True
    return Response(status_code=204, headers=_hx_trigger("jia-autoscrape-changed"))


@app.post("/actions/autoscrape/config")
def action_autoscrape_config(interval_seconds: int = Form(...), score_limit: int = Form(...)):
    s = state.get_autoscrape_state()
    with state.AUTOSCRAPE_LOCK:
        s["interval_seconds"] = max(60, int(interval_seconds))
        s["score_limit"] = max(1, int(score_limit))
    return Response(status_code=204, headers=_hx_trigger("jia-autoscrape-changed"))


# Downloads need no-cache so browsers don't serve a stale .docx from
# heuristic cache after a regeneration (Chrome happily caches attachments
# with Last-Modified for ~hours without revalidating). With this header,
# every click does a conditional GET — server returns 304 if unchanged,
# 200 with fresh bytes if the file was just regenerated.
DOWNLOAD_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}


@app.get("/files/resume/{job_hash}")
def download_resume(job_hash: str):
    """Stream the .docx for download."""
    from fastapi.responses import FileResponse

    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    path = existing_resume_path(job["title"], job["company"], job.get("location") or "")
    if not path.exists():
        raise HTTPException(404, "resume not generated yet")
    return FileResponse(path, filename=path.name, headers=DOWNLOAD_HEADERS)


@app.get("/files/cover/{job_hash}")
def download_cover(job_hash: str):
    from fastapi.responses import FileResponse

    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    path = expected_cover_letter_path(job["title"], job["company"])
    if not path.exists():
        raise HTTPException(404, "cover letter not generated yet")
    return FileResponse(path, filename=path.name, headers=DOWNLOAD_HEADERS)


@app.get("/files/bundle/{job_hash}")
def download_bundle(job_hash: str):
    """Stream a .zip containing the resume + cover letter for one-click
    download of both artifacts. Errors with 404 if either file is missing
    so the user knows to generate the missing one before bundling."""
    import io
    import zipfile

    from fastapi.responses import StreamingResponse

    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    resume_path = existing_resume_path(job["title"], job["company"], job.get("location") or "")
    cover_path = expected_cover_letter_path(job["title"], job["company"])
    missing = [
        n for n, p in (("resume", resume_path), ("cover letter", cover_path)) if not p.exists()
    ]
    if missing:
        raise HTTPException(404, f"missing artifacts: {', '.join(missing)}")

    # In-memory zip — both files are small (~50KB combined) so this is fine
    # without spilling to disk. ZipFile context manager guarantees the
    # central directory is written before the stream is consumed.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(resume_path, arcname=resume_path.name)
        zf.write(cover_path, arcname=cover_path.name)
    buf.seek(0)
    safe_company = safe_filename_part(job["company"])
    safe_title = safe_filename_part(job["title"])
    bundle_name = f"Dheeraj_Sampath_{safe_title}_{safe_company}_bundle.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{bundle_name}"',
            **DOWNLOAD_HEADERS,
        },
    )


@app.get("/api/jobs.json")
def api_jobs_json(show_rejects: bool = False):
    """Full job dataset as a flat JSON array — consumed by AG Grid.

    Default view: prefilter survivors PLUS any job whose status has moved
    past 'new' (user-curated state — shortlisted / applying / applied /
    interviewing / offer / closed). The user's explicit engagement
    overrides the prefilter judgment, otherwise the sidebar's pipeline
    counts show jobs the grid hides — confusing UX. `show_rejects=True`
    bypasses the gate entirely.
    """
    df = db.to_dataframe()
    if df.empty:
        return JSONResponse([])
    if not show_rejects:
        df = df[(df["prefilter_passed"] == 1) | (df["status"] != "new")]
    df = df.sort_values(
        by=["score_total", "scraped_at"],
        ascending=[False, False],
        na_position="last",
    )

    # One filesystem scan instead of per-row Path.exists() probes — the grid
    # renders has_resume/has_cover_letter for every visible row, and we need
    # to scale to ~500 rows without N filesystem syscalls. The set lookup
    # below is O(1) per row.
    from src.utils import OUTPUT_DIR as _EXPORTS_DIR

    on_disk: set[str] = set()
    if _EXPORTS_DIR.exists():
        on_disk = {p.name for p in _EXPORTS_DIR.iterdir() if p.suffix == ".docx"}

    def _clean(v):
        # pandas turns NULL text columns into float NaN; json.dumps rejects them.
        if v is None:
            return None
        if isinstance(v, float) and v != v:  # NaN check
            return None
        return v

    out = []
    for r in df.to_dict(orient="records"):
        score = r.get("score_total")
        # Compute expected filenames; check membership in the on-disk set.
        # Uses expected_resume_path (canonical), then falls back to the
        # legacy no-location-suffix variant — same fallback chain as
        # existing_resume_path. OSError on pathological titles → no match.
        try:
            canonical_r = expected_resume_path(
                r.get("title") or "", r.get("company") or "", r.get("location") or ""
            ).name
            legacy_r = expected_resume_path(r.get("title") or "", r.get("company") or "", "").name
            has_resume = canonical_r in on_disk or legacy_r in on_disk
        except OSError:
            has_resume = False
        try:
            cover_name = expected_cover_letter_path(
                r.get("title") or "", r.get("company") or ""
            ).name
            has_cover_letter = cover_name in on_disk
        except OSError:
            has_cover_letter = False
        out.append(
            {
                "hash": r["hash"],
                "score": int(score) if score is not None and score == score else None,
                "tier": r.get("tier") or "",
                "title": r.get("title") or "",
                "company": r.get("company") or "",
                "location": r.get("location") or "",
                "salary_min": _clean(r.get("salary_min")),
                "salary_max": _clean(r.get("salary_max")),
                "remote": bool(r.get("remote")) if r.get("remote") is not None else None,
                "sponsorship": r.get("sponsorship_status") or "unknown",
                "posted": (str(r.get("posted_at") or ""))[:10],
                "source": r.get("source") or "",
                "status": _clean(r.get("status")) or "new",
                "status_at": _clean(r.get("status_at")),
                "closed_reason": _clean(r.get("closed_reason")),
                "url": r.get("url") or "",
                "has_resume": has_resume,
                "has_cover_letter": has_cover_letter,
                # In-flight generation markers — drive the grid's
                # "Running…" cell state so the user sees that a click
                # actually queued work even while the Sonnet call is
                # still executing (60–120s for resume). Cleared by the
                # done-callback in state.submit_generation regardless
                # of success/failure, so a stuck row can't lie.
                "pending_resume": state.pending_started_at(r["hash"], "resume") is not None,
                "pending_cover": state.pending_started_at(r["hash"], "cover") is not None,
            }
        )
    return JSONResponse(out)


# Queue ordering for the Claude prompt. Set during the rewrite that gave
# Claude an ordered, batched workflow (5 jobs at a time).
#   1. Tier:   strong → possible → stretch → skip → (unscored last)
#   2. Status: shortlisted → new → applying (within tier)
#   3. Score:  desc within (tier, status) bucket
# Terminal states (closed, not_interested, no_sponsorship) are excluded —
# Claude should never see them.
_TIER_PRIORITY = {"strong": 0, "possible": 1, "stretch": 2, "skip": 3}
_STATUS_PRIORITY = {"shortlisted": 0, "new": 1, "applying": 2, "blocked_missing_artifacts": 3}
_BATCH_SIZE = 5


def _build_claude_queue(df) -> list[dict]:
    """Filter + sort the dashboard rows into the queue Claude works through.

    Same visibility rule as the dashboard's grid: prefilter survivors plus
    any job whose status has moved past `new` (user-curated state wins
    over the regex prefilter's verdict). Without this, the queue includes
    every freshly-scraped `new` row before scoring — thousands of jobs
    Claude shouldn't touch yet.

    Then trims to actionable statuses (shortlisted ∪ new ∪ applying) and
    sorts by tier → status → score desc. Excludes terminal states.
    """
    if df.empty:
        return []
    active = df[
        (df["status"].isin(_STATUS_PRIORITY.keys()))
        & ((df["prefilter_passed"] == 1) | (df["status"] != "new"))
    ].copy()
    if active.empty:
        return []
    active["_tier_pri"] = active["tier"].fillna("zzz").map(_TIER_PRIORITY).fillna(99).astype(int)
    active["_status_pri"] = active["status"].map(_STATUS_PRIORITY).astype(int)
    active = active.sort_values(
        by=["_tier_pri", "_status_pri", "score_total", "scraped_at"],
        ascending=[True, True, False, False],
        na_position="last",
    )
    return active.to_dict(orient="records")


def _job_bundle(job_hash: str) -> dict:
    """One-shot per-job JSON bundle used by Claude-in-Chrome."""
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")

    title = job.get("title") or ""
    company = job.get("company") or ""
    location = job.get("location") or ""
    try:
        rp = existing_resume_path(title, company, location)
        resume_path = str(rp.absolute()) if rp.exists() else None
        resume_filename = rp.name if rp.exists() else None
    except OSError:
        resume_path = resume_filename = None
    try:
        cp = expected_cover_letter_path(title, company)
        cover_letter_path = str(cp.absolute()) if cp.exists() else None
        cover_letter_filename = cp.name if cp.exists() else None
    except OSError:
        cover_letter_path = cover_letter_filename = None

    scores = None
    if resume_path:
        sidecar = Path(resume_path).with_suffix(".scores.json")
        if sidecar.exists():
            try:
                raw = json.loads(sidecar.read_text())
                scores = {
                    "ats_match_pct": (raw.get("ats_match") or {}).get("match_pct"),
                    "hr_score": (raw.get("hr") or {}).get("hr_score"),
                }
            except (OSError, json.JSONDecodeError):
                pass

    cover_letter_content = None
    try:
        cs = expected_cover_sidecar_path(title, company)
        if cs.exists():
            cover_letter_content = json.loads(cs.read_text())
    except (OSError, json.JSONDecodeError):
        pass

    return {
        "hash": job_hash,
        "title": title,
        "company": company,
        "location": location,
        "url": job.get("url") or "",
        "description": job.get("description") or "",
        "sponsorship_status": job.get("sponsorship_status") or "unknown",
        "score_total": job.get("score_total"),
        "tier": job.get("tier") or "",
        "status": job.get("status") or "new",
        "status_at": job.get("status_at"),
        "track": detect_track(title),
        "artifacts": {
            "resume_path": resume_path,
            "resume_filename": resume_filename,
            "cover_letter_path": cover_letter_path,
            "cover_letter_filename": cover_letter_filename,
            "scores": scores,
        },
        "cover_letter_content": cover_letter_content,
        "application_defaults": resume_profile.APPLICATION_DEFAULTS,
        "actions": {
            "mark_applied": f"/api/applied/{job_hash}",
            "mark_no_sponsorship": f"/api/no-sponsorship/{job_hash}",
            "open_apply_url": job.get("url") or "",
        },
    }


def _queue_missing_artifacts(job: dict, bundle: dict) -> list[str]:
    """Queue missing generated docs for one job and return missing kinds."""
    missing: list[str] = []
    artifacts = bundle["artifacts"]
    job_hash = job["hash"]
    if not artifacts.get("resume_path"):
        missing.append("resume")
        if state.pending_started_at(job_hash, "resume") is None:
            state.mark_pending(job_hash, "resume")
            state.submit_generation(
                "resume",
                job.get("title") or "",
                job.get("company") or "",
                job.get("description") or "",
                location=job.get("location") or "",
                job_hash=job_hash,
            )
    if not artifacts.get("cover_letter_path"):
        missing.append("cover")
        if state.pending_started_at(job_hash, "cover") is None:
            state.mark_pending(job_hash, "cover")
            state.submit_generation(
                "cover",
                job.get("title") or "",
                job.get("company") or "",
                job.get("description") or "",
                location=job.get("location") or "",
                job_hash=job_hash,
            )
    return missing


@app.get("/api/queue.json")
def api_queue_json(limit: int = 25):
    """Ordered actionable queue Claude pops one job at a time from.

    Reuses _build_claude_queue (terminal states excluded; sorted tier →
    status → score). Trimmed to the fields Claude needs to decide whether
    to start the per-iteration fetch — full bundle is in /api/job/{hash}.json.
    """
    df = db.to_dataframe()
    full = _build_claude_queue(df)
    cap = max(1, min(limit, 200))
    out = []
    for j in full[:cap]:
        score = j.get("score_total")
        out.append(
            {
                "hash": j["hash"],
                "title": j.get("title") or "",
                "company": j.get("company") or "",
                "score": int(score) if score is not None and score == score else None,
                "tier": j.get("tier") or "",
                "status": j.get("status") or "new",
                "apply_url": j.get("url") or "",
            }
        )
    return JSONResponse({"queue": out, "total": len(full), "returned": len(out)})


@app.get("/api/apply/next")
def api_apply_next():
    """Return the next application-ready job.

    A job is ready only when both generated artifacts exist on disk. If the
    next queued job is missing either artifact, queue the missing generation
    work, mark the row blocked_missing_artifacts, and return no job.
    """
    df = db.to_dataframe()
    queue = _build_claude_queue(df)
    if not queue:
        return JSONResponse(
            {
                "job": None,
                "queue_empty": True,
                "blocked_missing_artifacts": False,
            }
        )

    queued = queue[0]
    job_hash = queued["hash"]
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")

    bundle = _job_bundle(job_hash)
    missing = _queue_missing_artifacts(job, bundle)
    if missing:
        db.set_status(job_hash, "blocked_missing_artifacts", None)
        db.record_application_step(
            job_hash,
            "artifact_gate",
            "blocked_missing_artifacts",
            ",".join(missing),
        )
        return JSONResponse(
            {
                "job": None,
                "queue_empty": False,
                "blocked_missing_artifacts": True,
                "hash": job_hash,
                "missing": missing,
            }
        )

    if (job.get("status") or "new") in ("new", "shortlisted", "blocked_missing_artifacts"):
        db.set_status(job_hash, "applying", None)
        db.record_application_step(job_hash, "artifact_gate", "ready", "resume,cover")
        bundle = _job_bundle(job_hash)
    return JSONResponse(
        {
            "job": bundle,
            "queue_empty": False,
            "blocked_missing_artifacts": False,
        }
    )


@app.get("/api/job/{job_hash}.json")
def api_job_json(job_hash: str):
    """One-shot bundle Claude reads per-iteration. Includes everything
    needed to fill a job application form without scraping the dashboard
    or parsing the cover letter .docx — JD text, absolute artifact paths,
    pre-extracted "why this company" answer, application defaults.
    """
    return JSONResponse(_job_bundle(job_hash))


@app.post("/api/applied/{job_hash}")
def api_applied(job_hash: str):
    """JSON-friendly alias for /actions/status/{hash}?status=applied.
    The dashboard's badge OOB swap fires from the form-encoded HTML
    endpoint; this JSON variant is for Claude-in-Chrome's fetch() flow
    and relies on the existing 60s badge poll for the user-visible
    refresh — eventual consistency is fine for an automation loop.
    """
    if not db.get_job(job_hash):
        raise HTTPException(404, "job not found")
    db.set_status(job_hash, "applied", None)
    return JSONResponse({"ok": True, "status": "applied"})


@app.post("/api/no-sponsorship/{job_hash}")
def api_no_sponsorship(job_hash: str):
    """JSON-friendly alias to mark a job as no_sponsorship. Used when
    Claude reads a JD that explicitly denies sponsorship and skips the
    application instead of submitting it.
    """
    if not db.get_job(job_hash):
        raise HTTPException(404, "job not found")
    db.set_status(job_hash, "no_sponsorship", None)
    return JSONResponse({"ok": True, "status": "no_sponsorship"})


@app.get("/api/claude-prompt.txt", response_class=Response)
def api_claude_prompt():
    """Self-contained prompt to paste into the Claude-for-Chrome sidebar.

    The long prompt lives in src.apply.prompt so the route is only transport.
    """
    body = build_claude_prompt(resume_profile.APPLICATION_DEFAULTS, batch_size=_BATCH_SIZE)
    return Response(content=body, media_type="text/plain; charset=utf-8")


@app.get("/healthz")
def healthz():
    return {"ok": True}
