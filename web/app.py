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
from src.cover_letter import expected_cover_letter_path  # noqa: E402
from src.resume import (  # noqa: E402
    existing_resume_path,
    expected_resume_path,
)
from src.resume import profile as resume_profile  # noqa: E402
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
    doesn't already have one. IC-track titles are skipped silently inside
    the helper. Goes through the same executor as resumes (3 workers),
    so cover-letter and resume jobs interleave naturally."""
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


@app.post("/actions/autoscrape/config")
def action_autoscrape_config(interval_seconds: int = Form(...), score_limit: int = Form(...)):
    s = state.get_autoscrape_state()
    with state.AUTOSCRAPE_LOCK:
        s["interval_seconds"] = max(60, int(interval_seconds))
        s["score_limit"] = max(1, int(score_limit))
    return Response(status_code=204, headers=_hx_trigger("jia-autoscrape-changed"))


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
    return FileResponse(path, filename=path.name)


@app.get("/files/cover/{job_hash}")
def download_cover(job_hash: str):
    from fastapi.responses import FileResponse

    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    path = expected_cover_letter_path(job["title"], job["company"])
    if not path.exists():
        raise HTTPException(404, "cover letter not generated yet")
    return FileResponse(path, filename=path.name)


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
        headers={"Content-Disposition": f'attachment; filename="{bundle_name}"'},
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
            }
        )
    return JSONResponse(out)


@app.get("/api/claude-prompt.txt", response_class=Response)
def api_claude_prompt():
    """Self-contained prompt to paste into the Claude-for-Chrome sidebar.

    Embeds the active queue (status IN shortlisted, applying) so Claude
    has the URLs and hashes it needs to work through the list and call
    back to mark each row applied via /actions/status/{hash}.
    """
    df = db.to_dataframe()
    if df.empty:
        queue: list[dict] = []
    else:
        active = df[df["status"].isin(["shortlisted", "applying"])].copy()
        active = active.sort_values(
            by=["score_total", "scraped_at"],
            ascending=[False, False],
            na_position="last",
        )
        queue = active.to_dict(orient="records")

    lines: list[str] = []
    lines.append("You are operating inside Dheeraj's personal job-hunt dashboard.")
    lines.append(
        "Dashboard URL: http://127.0.0.1:8826  (this is your control surface — keep it open)"
    )
    lines.append("")
    lines.append("This is Dheeraj's personal job hunt dashboard. Your job is to apply to")
    lines.append("the jobs listed at the bottom of this prompt and ALERT HIM BEFORE")
    lines.append("SUBMITTING each one so he can verify the information. Work through the")
    lines.append("entire list continuously — only pause for input or to wait for his")
    lines.append('"go ahead" before submit. After he submits one job, do NOT stop:')
    lines.append("immediately move on to the next one.")
    lines.append("")
    # Pull facts from src.resume.profile — single source of truth for years,
    # current role, application-form answers. Edit profile.py to update these
    # everywhere (resume + this prompt + future autofill tooling).
    current_role = resume_profile.EXPERIENCE[0]
    appl = resume_profile.APPLICATION_DEFAULTS
    lines.append("## About Dheeraj")
    lines.append(
        f"- {appl['work_authorization']} If a posting denies sponsorship, skip it and tell him why."
    )
    lines.append(f"- Targeting: {appl['targeting']}.")
    lines.append(f"- Email: {appl['email']}")
    lines.append(
        f"- {resume_profile.YEARS_OF_EXPERIENCE} years engineering, "
        f"current role: {current_role['title']} at {current_role['company']}. "
        "Strong frontend platform / design-systems / micro-frontend background."
    )
    lines.append(
        f"- Comp expectation: {appl['comp_expectation']} (use 'competitive' "
        "if asked; defer to recruiter on specifics)."
    )
    lines.append(f"- {appl['notice_period']}.")
    lines.append(f"- Open to: {appl['open_to']}")
    lines.append("")
    lines.append("## Tab discipline")
    lines.append("")
    lines.append("Two tabs are in play at all times:")
    lines.append(
        "- **Dashboard tab** (http://127.0.0.1:8826) — already open, stays open. Use it to read the queue, find each job's row, and click status pills to mark it applied."
    )
    lines.append(
        "- **Job tab** — a NEW tab per job. ALWAYS open the apply URL in a new tab (right-click → Open in new tab, or middle-click). Never navigate the dashboard tab away from the dashboard."
    )
    lines.append("")
    lines.append(
        "After Dheeraj submits a job, you may close that job's tab and switch back to the dashboard tab to mark it applied. Then open the NEXT job in a fresh new tab."
    )
    lines.append("")
    lines.append("## Tailored artifacts per job")
    lines.append("")
    lines.append(
        "Each queue entry below lists direct-download URLs for the resume and (when present) the cover letter that have already been generated for that specific job. Visiting one of those URLs triggers a real browser download — the .docx lands in Dheeraj's `~/Downloads/` folder under its full filename. Use these per-job tailored files; do NOT use a generic resume from elsewhere."
    )
    lines.append("")
    lines.append(
        "## Workflow — repeat for every job in the queue below, without pausing between jobs"
    )
    lines.append("")
    lines.append("1. Open the job's **Apply** URL in a NEW TAB (do not replace the dashboard).")
    lines.append(
        "2. **Download the artifacts** — open the **Resume** URL from the queue entry in another new tab; it auto-downloads. If a **Cover letter** URL is listed, do the same for it. Note the exact filenames Chrome saves to `~/Downloads/` (they look like `Dheeraj_Sampath_<title>_<company>.docx` and `CoverLetter_Dheeraj_Sampath_<title>_<company>.docx`). Close those download tabs after the files land."
    )
    lines.append(
        "3. Switch to the job tab. Wait for the Simplify Chrome extension to autofill the form. Give it ~5–10 seconds; some forms take longer."
    )
    lines.append("4. Verify the answers Simplify filled. If any are wrong, fix them.")
    lines.append(
        "5. Answer remaining text fields Simplify missed, using the context above. If a question is not covered by the context, PAUSE and ASK Dheeraj — do not guess."
    )
    lines.append(
        '6. **Resume upload field**: file-input widgets cannot be filled programmatically by the extension. PAUSE and tell Dheeraj the EXACT filename to drag in: "Drag `Dheeraj_Sampath_<title>_<company>.docx` from your Downloads folder into the Resume / CV field." Wait for him to confirm it\'s attached.'
    )
    lines.append(
        "7. **Cover letter upload field** (only if the form has one and a cover letter URL was listed for this job): same pattern — name the file (`CoverLetter_Dheeraj_Sampath_<title>_<company>.docx`) and ask him to drag it in. Wait for confirmation. If the form has no cover letter field, skip this step."
    )
    lines.append(
        '8. ALERT Dheeraj: "Job [N] ready to submit" + a brief summary of every filled answer + which files he attached. Wait for an explicit "go ahead" reply. Never submit without it.'
    )
    lines.append("9. After Dheeraj confirms, click Submit (or let him click — either is fine).")
    lines.append(
        '10. Switch to the dashboard tab. Use the search box at the top of the grid (the input with the ⌕ icon) to type part of the company name or title — this filters the grid to one row. Click that row to open the detail pane on the right, then click the **✓ Applied** pill in the status strip. The grid chip will update to "Applied" — that confirms success. Clear the search box afterwards before moving on.'
    )
    lines.append("11. Optionally close the job's tab to keep the browser tidy.")
    lines.append(
        "12. Immediately open the NEXT job in a new tab and start at step 1. Do NOT wait for an acknowledgement between jobs."
    )
    lines.append("")
    lines.append(
        "After the last job, post a final summary: how many were submitted, how many were skipped (and why)."
    )
    lines.append("")
    lines.append("## When to pause (and only when)")
    lines.append("- Step 5: a form field's answer is not in the context. ASK.")
    lines.append(
        "- Steps 6–7: file-upload widgets need Dheeraj's manual drag-and-drop. Name the exact filename, wait for confirmation."
    )
    lines.append('- Step 8: every "ready to submit" moment. WAIT for "go ahead".')
    lines.append(
        '- Otherwise: keep moving. Do not announce "starting job N" or ask "shall I continue". Just continue.'
    )
    lines.append("")
    lines.append("## Hard rules")
    lines.append('- Never submit without an explicit "go ahead".')
    lines.append("- Never navigate the dashboard tab away from http://127.0.0.1:8826.")
    lines.append("- Never invent data. If the context above doesn't cover a question, ask.")
    lines.append(
        "- If a posting denies sponsorship, skip it (don't apply). Mention it in the final summary."
    )
    lines.append("")
    lines.append(f"## Queue ({len(queue)} job{'s' if len(queue) != 1 else ''})")
    lines.append("")
    if not queue:
        lines.append(
            "(empty — Dheeraj hasn't shortlisted any jobs yet. Tell him to shortlist some from the dashboard first.)"
        )
    else:
        for i, j in enumerate(queue, start=1):
            score = j.get("score_total")
            score_str = f" [{int(score)}]" if score is not None and score == score else ""
            loc = j.get("location") or ""
            loc_str = f" · {loc}" if loc else ""
            jhash = j.get("hash") or ""
            # Only list download URLs when the artifact actually exists on disk —
            # giving Claude a URL to a missing file would just produce a 404.
            r_path = existing_resume_path(j["title"], j["company"], j.get("location") or "")
            c_path = expected_cover_letter_path(j["title"], j["company"])
            lines.append(f"[{i}] {j['title']} - {j['company']}{score_str}{loc_str}")
            lines.append(
                f"    Apply:        {j.get('url') or '(no URL — search the company careers page)'}"
            )
            if r_path.exists() and jhash:
                lines.append(
                    f"    Resume:       http://127.0.0.1:8826/files/resume/{jhash}    ({r_path.name})"
                )
            else:
                lines.append(
                    "    Resume:       (NOT GENERATED — tell Dheeraj to click Generate on the dashboard before applying)"
                )
            if c_path.exists() and jhash:
                lines.append(
                    f"    Cover letter: http://127.0.0.1:8826/files/cover/{jhash}     ({c_path.name})"
                )
            lines.append("")
    lines.append("Begin with job [1] now. Open it in a new tab.")

    body = "\n".join(lines)
    return Response(content=body, media_type="text/plain; charset=utf-8")


@app.get("/healthz")
def healthz():
    return {"ok": True}
