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
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Query, Request
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

from src import db, state  # noqa: E402
from src.generate.cover_letter import expected_cover_letter_path  # noqa: E402
from src.generate.resume import (  # noqa: E402
    expected_resume_path,
    existing_resume_path,
)
from src.scrapers.base import BaseScraper  # noqa: E402

# ---------------------------------------------------------------------------
# App + static + templates
# ---------------------------------------------------------------------------

app = FastAPI(title="JobFindEasy", docs_url="/api/docs", redoc_url=None)

WEB_DIR = ROOT / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")


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


def _score_color(score: Optional[float]) -> str:
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


def _fmt_relative(ts: Optional[float]) -> str:
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


def _filtered_df(filters: dict):
    """Pull jobs from DB and apply filters. Returns pandas DataFrame."""
    df = db.to_dataframe()
    if df.empty:
        return df
    if not filters.get("show_rejects"):
        df = df[df["prefilter_passed"] == 1]
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
    applied = filters.get("applied")
    if applied == "yes":
        df = df[df["applied"] == 1]
    elif applied == "no":
        df = df[df["applied"] == 0]
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
# Routes — HTMX partials (filled in over Phases 2-4)
# ---------------------------------------------------------------------------

@app.get("/partials/table", response_class=HTMLResponse)
def partial_table(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    show_rejects: bool = False,
    source: list[str] = Query(default=[]),
    tier: list[str] = Query(default=[]),
    min_score: int = 0,
    sponsor: list[str] = Query(default=[]),
    applied: str = "all",  # "all" | "yes" | "no"
    q: str = "",
):
    filters = {
        "show_rejects": show_rejects,
        "source": source,
        "tier": tier,
        "min_score": min_score,
        "sponsor": sponsor,
        "applied": applied,
        "q": q.strip(),
    }
    df = _filtered_df(filters)
    total = len(df)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    page_df = df.iloc[start : start + page_size]
    rows = page_df.to_dict(orient="records") if not page_df.empty else []
    return templates.TemplateResponse(
        request,
        "partials/table.html",
        {
            "rows": rows,
            "page": page,
            "pages": pages,
            "page_size": page_size,
            "total": total,
            "TIER_MAP": TIER_MAP,
            "SPONSOR_MAP": SPONSOR_MAP,
            "score_color": _score_color,
            "filters": filters,
        },
    )


@app.get("/partials/detail/{job_hash}", response_class=HTMLResponse)
def partial_detail(request: Request, job_hash: str):
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    job["_clean_description"] = (
        BaseScraper.clean_html(job.get("description") or "")[:8000]
    )
    job["_score_breakdown"] = (
        json.loads(job["score_breakdown"]) if job.get("score_breakdown") else None
    )
    return templates.TemplateResponse(
        request,
        "partials/detail.html",
        {
            "job": job,
            "TIER_MAP": TIER_MAP,
            "SPONSOR_MAP": SPONSOR_MAP,
            "score_color": _score_color,
        },
    )


@app.get("/partials/artifacts/{job_hash}", response_class=HTMLResponse)
def partial_artifacts(request: Request, job_hash: str):
    """Auto-polled by HTMX every 2.5s while a generation is in flight or a
    recent file exists. Renders the resume + cover letter previews + scores."""
    job = db.get_job(job_hash)
    if not job:
        return HTMLResponse("<div></div>")

    resume_path = existing_resume_path(
        job["title"], job["company"], job.get("location") or ""
    )
    cover_path = expected_cover_letter_path(job["title"], job["company"])
    pending_resume = state.pending_started_at(job_hash, "resume")
    pending_cover = state.pending_started_at(job_hash, "cover")

    if resume_path.exists():
        state.clear_pending(job_hash, "resume")
        pending_resume = None
    if cover_path.exists():
        state.clear_pending(job_hash, "cover")
        pending_cover = None

    # Lazy mammoth import — only when actually rendering a preview
    resume_html = None
    scores = None
    if resume_path.exists():
        try:
            import mammoth
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
            import mammoth
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


@app.get("/partials/badges", response_class=HTMLResponse)
def partial_badges(request: Request):
    """Live count badges in the sidebar — polled every 5s."""
    df = db.to_dataframe()
    if df.empty:
        return HTMLResponse(
            '<div id="badges"><small>0 jobs</small></div>'
        )
    strong = int((df["score_total"].fillna(0) >= 80).sum())
    applied = int(df["applied"].sum())
    pending_followup = 0
    # Follow-up count: applied >= 7 days ago, not in interview/offer/rejected stages.
    # For now (binary applied), count applied jobs older than 7 days.
    if "applied_at" in df.columns:
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        pending_followup = int(
            (
                (df["applied"] == 1)
                & (df["applied_at"].fillna("") < cutoff)
                & (df["applied_at"].fillna("") != "")
            ).sum()
        )
    return templates.TemplateResponse(
        request,
        "partials/badges.html",
        {
            "strong": strong,
            "applied": applied,
            "pending_followup": pending_followup,
            "total": len(df),
        },
    )


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

@app.post("/actions/applied/{job_hash}")
def action_applied(job_hash: str, applied: str = Form(...)):
    """Toggle applied. Form value 'on' / 'off'."""
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    new = applied == "on"
    db.set_applied(job_hash, new)
    return Response(status_code=204)


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
    )
    state.mark_pending(job_hash, kind)
    return Response(status_code=204)


@app.post("/actions/regenerate/{kind}/{job_hash}")
def action_regenerate(kind: str, job_hash: str):
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    if kind == "resume":
        path = expected_resume_path(
            job["title"], job["company"], job.get("location") or ""
        )
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
    )
    state.mark_pending(job_hash, kind)
    return Response(status_code=204)


@app.post("/actions/bulk-generate")
def action_bulk_generate():
    n = state.submit_all_missing_strong_fits()
    return JSONResponse({"queued": n})


@app.post("/actions/inject-url")
def action_inject_url(url: str = Form(...)):
    """Paste-a-link injection. Fetches the URL, LLM-extracts the job fields,
    upserts into the DB, and runs prefilter + score inline so the row is
    fully usable the moment it appears in the grid.
    """
    import os as _os
    from src.scrapers.url_inject import inject_from_url
    from src.enrichment.prefilter import prefilter as run_prefilter
    from src.enrichment.llm_scorer import score_job, compute_tier, make_client

    job, status = inject_from_url(url)
    if not job:
        return JSONResponse({"ok": False, "error": status}, status_code=400)

    inserted = db.upsert_job(job)
    if not inserted:
        return JSONResponse(
            {"ok": True, "hash": job.hash, "duplicate": True,
             "title": job.title, "company": job.company}
        )

    ok, reason, sponsorship = run_prefilter(job.title, job.description or "")
    db.update_prefilter(job.hash, ok, reason, sponsorship)

    if ok:
        try:
            client = make_client()
            model = _os.environ.get("SCORING_MODEL", "anthropic/claude-haiku-4.5")
            result = score_job(
                client, model,
                title=job.title, company=job.company, location=job.location,
                description=job.description or "", sponsorship=sponsorship,
            )
            if result:
                total = int(result.get("total", 0))
                tier = result.get("tier") or compute_tier(total)
                breakdown = json.dumps({k: result.get(k) for k in [
                    "title_match", "skills_match", "leadership_scope",
                    "domain_alignment", "location_fit", "comp_confidence",
                ]})
                db.update_score(job.hash, total, breakdown,
                                result.get("rationale", ""), tier)
        except Exception as e:
            log.warning("inline score after inject failed: %s", e)

    return JSONResponse({
        "ok": True, "hash": job.hash, "duplicate": False,
        "title": job.title, "company": job.company,
        "prefilter_passed": ok,
    })


@app.post("/actions/clear-completed-generations")
def action_clear_completed():
    n = state.clear_completed_generations()
    return JSONResponse({"remaining": n})


@app.post("/actions/autoscrape/toggle")
def action_autoscrape_toggle(enabled: str = Form(...)):
    s = state.get_autoscrape_state()
    with state.AUTOSCRAPE_LOCK:
        s["enabled"] = enabled == "on"
    return Response(status_code=204)


@app.post("/actions/autoscrape/config")
def action_autoscrape_config(
    interval_seconds: int = Form(...), score_limit: int = Form(...)
):
    s = state.get_autoscrape_state()
    with state.AUTOSCRAPE_LOCK:
        s["interval_seconds"] = max(60, int(interval_seconds))
        s["score_limit"] = max(1, int(score_limit))
    return Response(status_code=204)


@app.get("/files/resume/{job_hash}")
def download_resume(job_hash: str):
    """Stream the .docx for download."""
    from fastapi.responses import FileResponse
    job = db.get_job(job_hash)
    if not job:
        raise HTTPException(404, "job not found")
    path = existing_resume_path(
        job["title"], job["company"], job.get("location") or ""
    )
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


@app.get("/api/jobs.json")
def api_jobs_json(show_rejects: bool = False):
    """Full job dataset as a flat JSON array — consumed by AG Grid.

    Returns ALL rows (or only prefilter survivors if `show_rejects=False`,
    the default). AG Grid handles pagination/sort/filter on the client. With
    a few thousand rows this is faster than server roundtrips.
    """
    df = db.to_dataframe()
    if df.empty:
        return JSONResponse([])
    if not show_rejects:
        df = df[df["prefilter_passed"] == 1]
    df = df.sort_values(
        by=["score_total", "scraped_at"],
        ascending=[False, False],
        na_position="last",
    )
    out = []
    for r in df.to_dict(orient="records"):
        score = r.get("score_total")
        out.append({
            "hash": r["hash"],
            "score": int(score) if score is not None and score == score else None,
            "tier": r.get("tier") or "",
            "title": r.get("title") or "",
            "company": r.get("company") or "",
            "location": r.get("location") or "",
            "salary_min": r.get("salary_min"),
            "salary_max": r.get("salary_max"),
            "remote": bool(r.get("remote")) if r.get("remote") is not None else None,
            "sponsorship": r.get("sponsorship_status") or "unknown",
            "posted": (str(r.get("posted_at") or ""))[:10],
            "source": r.get("source") or "",
            "applied": bool(r.get("applied")),
            "url": r.get("url") or "",
        })
    return JSONResponse(out)


@app.get("/healthz")
def healthz():
    return {"ok": True}
