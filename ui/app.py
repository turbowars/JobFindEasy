"""Streamlit dashboard for the Job Intelligence Agent.

Run: streamlit run ui/app.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import os

from src import db
from src.generate.resume import (
    generate_resume,
    autogen_resume_if_missing,
    expected_resume_path,
    existing_resume_path,
)
from src.scrapers.base import BaseScraper
from src.generate.cover_letter import expected_cover_letter_path

import mammoth
from src.generate.cover_letter import generate_cover_letter
from src.scrapers.runner import run_all_sync
from src.enrichment.prefilter import prefilter as run_prefilter
from src.enrichment.llm_scorer import score_job, make_client, compute_tier

st.set_page_config(page_title="Job Intelligence Agent", layout="wide")

TIER_COLORS = {
    "strong": "#10b981",
    "possible": "#f59e0b",
    "stretch": "#f97316",
    "skip": "#6b7280",
}
TIER_LABELS = {
    "strong": "STRONG",
    "possible": "POSSIBLE",
    "stretch": "STRETCH",
    "skip": "SKIP",
}
SPONSORSHIP_BADGES = {
    "offered": ("✅", "Sponsorship offered"),
    "denied": ("❌", "No sponsorship"),
    "unknown": ("❓", "Sponsorship unclear"),
}

TIER_EMOJI = {
    "strong": "🟢 strong",
    "possible": "🟡 possible",
    "stretch": "🟠 stretch",
    "skip": "⚫ skip",
}
SPONSORSHIP_EMOJI = {
    "offered": "✅",
    "denied": "❌",
    "unknown": "❓",
}


def score_color(score):
    if score is None or pd.isna(score):
        return "#6b7280"
    s = int(score)
    if s >= 80:
        return TIER_COLORS["strong"]
    if s >= 60:
        return TIER_COLORS["possible"]
    if s >= 40:
        return TIER_COLORS["stretch"]
    return TIER_COLORS["skip"]


def score_label(score):
    if score is None or pd.isna(score):
        return "—"
    return str(int(score))


@st.cache_data(ttl=180, show_spinner=False)
def load_jobs() -> pd.DataFrame:
    """Cached snapshot of the jobs DataFrame.

    TTL is 3 minutes — autoscrape cycles run no faster than every 15 min by
    default, so a 3-minute window of slightly stale UI data is a worthwhile
    trade for near-instant reruns. All write paths (set_applied, set_notes,
    pipeline runs) explicitly call `st.cache_data.clear()` to invalidate.
    """
    return db.to_dataframe()


@st.cache_data(show_spinner=False)
def docx_to_html(path_str: str, mtime: float) -> str:
    """Convert a .docx to HTML for inline preview. mtime is in the cache key
    so the preview updates when the file is regenerated."""
    with open(path_str, "rb") as f:
        result = mammoth.convert_to_html(f)
    return result.value


@st.cache_data(show_spinner=False, max_entries=512)
def clean_jd_text(html_or_text: str) -> str:
    """Strip HTML tags and decode common entities from a job description.

    Most scrapers (Greenhouse, Lever, Coinbase) store the JD body as raw HTML.
    Rendering that with `st.text` shows the literal `<div><p>...</p></div>`
    markup. We reuse `BaseScraper.clean_html` which already handles tag
    stripping + paragraph-aware newline insertion + entity decoding.
    """
    if not html_or_text:
        return ""
    return BaseScraper.clean_html(html_or_text)


def _safe_docx_html(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return docx_to_html(str(path), path.stat().st_mtime)
    except Exception as e:
        return f"<em>Preview failed: {e}</em>"


@st.cache_data(show_spinner=False)
def _scores_sidecar_cached(sidecar_path_str: str, mtime: float) -> dict | None:
    """Memoize the sidecar read keyed on (path, mtime). Avoids re-reading from
    disk on every fragment tick. Cache invalidates automatically when the file
    is regenerated (mtime bump → new cache key)."""
    p = Path(sidecar_path_str)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _load_scores_sidecar(docx_path: Path) -> dict | None:
    sidecar = docx_path.with_suffix(".scores.json")
    if not sidecar.exists():
        return None
    return _scores_sidecar_cached(str(sidecar), sidecar.stat().st_mtime)


# ---------------------------------------------------------------------------
# Process-level pending markers + generations log
#
# These live OUTSIDE st.session_state so they survive browser refreshes and
# are shared across browser tabs. The actual generation work is already
# process-scoped (ThreadPoolExecutor in get_executor); these mirrors are just
# the UI-visible status of those jobs. The Streamlit Python process itself is
# the source of truth — when it restarts, the markers/log reset, but any
# files already written to disk persist and are auto-rediscovered.
# ---------------------------------------------------------------------------
_PENDING_MARKERS: dict[tuple[str, str], float] = {}
_PENDING_LOCK = threading.Lock()


def _mark_pending(job_hash: str, kind: str) -> None:
    """Track in-flight generation for a job so the auto-refreshing fragment
    can render a progress indicator. `kind` is "resume" or "cover"."""
    with _PENDING_LOCK:
        _PENDING_MARKERS[(job_hash, kind)] = time.time()


def _clear_pending(job_hash: str, kind: str) -> None:
    with _PENDING_LOCK:
        _PENDING_MARKERS.pop((job_hash, kind), None)


def _pending_started_at(job_hash: str, kind: str) -> float | None:
    with _PENDING_LOCK:
        return _PENDING_MARKERS.get((job_hash, kind))


def _fmt_file_age(path: Path) -> str:
    if not path.exists():
        return ""
    age = int(time.time() - path.stat().st_mtime)
    if age < 60:
        return f"{age}s ago"
    if age < 3600:
        return f"{age // 60}m ago"
    if age < 86400:
        return f"{age // 3600}h ago"
    return f"{age // 86400}d ago"


def _fmt_size(path: Path) -> str:
    if not path.exists():
        return ""
    n = path.stat().st_size
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _render_inflight_skeleton(label: str, started_at: float):
    """Show a clean 'generating' indicator with elapsed time. Re-renders on
    each fragment tick so the elapsed counter ticks live."""
    elapsed = int(time.time() - started_at)
    st.html(
        f'<div style="border:1px dashed rgba(128,128,128,0.35);border-radius:8px;'
        f'padding:18px 16px;background:rgba(128,128,128,0.04);">'
        f'<div style="font-size:13px;font-weight:600;color:#888;'
        f'letter-spacing:0.4px;">⏳ GENERATING {label.upper()}</div>'
        f'<div style="font-size:11px;color:#888;margin-top:4px;">'
        f'Running in background · {elapsed}s elapsed · '
        f'this view will update automatically when the file is ready.'
        f'</div></div>'
    )


@st.fragment(run_every=2.5)
def _render_artifacts_fragment(
    *,
    job_hash: str,
    job_title: str,
    job_company: str,
    job_location: str,
    job_description: str,
    resume_path_str: str,
    cover_path_str: str,
):
    """Auto-refreshing block that polls every 2.5s for the generated artifacts.

    Three states per artifact:
      1. File exists      → render preview + scores + actions
      2. Generation in flight → show skeleton with live elapsed counter
      3. Neither           → render nothing (the Generate buttons above are the affordance)
    """
    resume_path = Path(resume_path_str)
    cover_path = Path(cover_path_str)

    # Auto-clear stale pending markers if the file has landed.
    if resume_path.exists():
        _clear_pending(job_hash, "resume")
    if cover_path.exists():
        _clear_pending(job_hash, "cover")

    pending_resume = _pending_started_at(job_hash, "resume")
    pending_cover = _pending_started_at(job_hash, "cover")

    has_anything = (
        resume_path.exists() or cover_path.exists()
        or pending_resume is not None or pending_cover is not None
    )
    if not has_anything:
        return

    st.markdown("---")
    st.markdown("#### 📄 Generated artifacts")

    # ---- Resume ----
    if resume_path.exists():
        head = (
            f"📄 Resume preview — `{resume_path.name}`  "
            f"·  {_fmt_size(resume_path)}  ·  generated {_fmt_file_age(resume_path)}"
        )
        with st.expander(head, expanded=True):
            # Toolbar: download + regenerate
            t1, t2, _ = st.columns([0.32, 0.32, 0.36])
            with t1:
                with open(resume_path, "rb") as f:
                    st.download_button(
                        "⬇ Download .docx",
                        f.read(),
                        file_name=resume_path.name,
                        key=f"dl_resume_inline_{job_hash}",
                        use_container_width=True,
                    )
            with t2:
                if st.button(
                    "🔁 Regenerate",
                    key=f"regen_resume_{job_hash}",
                    use_container_width=True,
                    help="Spin a fresh resume with the same JD. The new file replaces the current one.",
                ):
                    try:
                        resume_path.unlink(missing_ok=True)
                        resume_path.with_suffix(".scores.json").unlink(missing_ok=True)
                    except Exception:
                        pass
                    submit_generation("resume", job_title, job_company, job_description)
                    _mark_pending(job_hash, "resume")
                    st.toast(f"📝 Regenerating resume for {job_company}...", icon="📝")
                    st.rerun()

            html = _safe_docx_html(resume_path)
            if html:
                st.html(
                    f'<div style="max-height:500px;overflow:auto;padding:10px;'
                    f'border:1px solid rgba(128,128,128,0.2);border-radius:8px;'
                    f'background:rgba(255,255,255,0.02);">{html}</div>'
                )
            scores = _load_scores_sidecar(resume_path)
            if scores:
                _render_scores_panel(scores)
    elif pending_resume is not None:
        _render_inflight_skeleton("resume", pending_resume)

    # ---- Cover letter ----
    if cover_path.exists():
        head = (
            f"✉️ Cover letter preview — `{cover_path.name}`  "
            f"·  {_fmt_size(cover_path)}  ·  generated {_fmt_file_age(cover_path)}"
        )
        with st.expander(head, expanded=False):
            t1, t2, _ = st.columns([0.32, 0.32, 0.36])
            with t1:
                with open(cover_path, "rb") as f:
                    st.download_button(
                        "⬇ Download .docx",
                        f.read(),
                        file_name=cover_path.name,
                        key=f"dl_cover_inline_{job_hash}",
                        use_container_width=True,
                    )
            with t2:
                if st.button(
                    "🔁 Regenerate",
                    key=f"regen_cover_{job_hash}",
                    use_container_width=True,
                ):
                    try:
                        cover_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    submit_generation("cover", job_title, job_company, job_description)
                    _mark_pending(job_hash, "cover")
                    st.toast(f"✉️ Regenerating cover letter for {job_company}...", icon="✉️")
                    st.rerun()

            html = _safe_docx_html(cover_path)
            if html:
                st.html(
                    f'<div style="max-height:500px;overflow:auto;padding:10px;'
                    f'border:1px solid rgba(128,128,128,0.2);border-radius:8px;'
                    f'background:rgba(255,255,255,0.02);">{html}</div>'
                )
    elif pending_cover is not None:
        _render_inflight_skeleton("cover letter", pending_cover)


def _score_color(pct: int) -> str:
    if pct >= 80:
        return "#16a34a"  # green
    if pct >= 60:
        return "#d97706"  # amber
    return "#dc2626"  # red


def _render_score_circle(label: str, pct: int):
    color = _score_color(int(pct or 0))
    st.html(
        f'<div style="text-align:center;">'
        f'<div style="font-size:11px;color:#888;letter-spacing:0.5px;text-transform:uppercase;">{label}</div>'
        f'<div style="display:inline-block;background:{color};color:white;'
        f'width:72px;height:72px;border-radius:50%;line-height:72px;text-align:center;'
        f'font-weight:700;font-size:24px;margin-top:4px;">{int(pct or 0)}</div>'
        f'</div>'
    )


def _render_scores_panel(scores: dict):
    ats = scores.get("ats_match") or {}
    hr = scores.get("hr") or {}
    if not ats and not hr:
        return

    st.markdown("##### 🎯 Resume scoring")
    col1, col2 = st.columns(2)
    with col1:
        _render_score_circle("ATS keyword match", int(ats.get("match_pct", 0)))
        missing = ats.get("missing") or {}
        flat_missing = []
        for tier in ("required", "preferred", "soft"):
            flat_missing.extend(missing.get(tier) or [])
        if flat_missing:
            st.markdown("**Missing keywords**")
            st.markdown(" ".join(f"`{k}`" for k in flat_missing))
        else:
            st.caption("All extracted keywords are covered.")

    with col2:
        _render_score_circle("HR perspective", int(hr.get("hr_score", 0)))
        if hr.get("rationale"):
            st.caption(hr["rationale"])
        weak = hr.get("weakest_areas") or []
        if weak:
            st.markdown("**Areas to strengthen**")
            for a in weak:
                st.markdown(f"- {a}")

    if scores.get("retried"):
        st.caption("🔁 Auto-retried once due to low initial scores. Final scores shown above.")

    matched = ats.get("matched") or {}
    flat_matched = []
    for tier in ("required", "preferred", "soft"):
        flat_matched.extend(matched.get(tier) or [])
    if flat_matched:
        with st.expander(f"✅ Matched keywords ({len(flat_matched)})", expanded=False):
            for tier in ("required", "preferred", "soft"):
                items = matched.get(tier) or []
                if items:
                    st.markdown(f"_{tier}:_ " + ", ".join(items))


@st.cache_resource
def get_executor() -> ThreadPoolExecutor:
    """Process-wide executor so workers survive Streamlit reruns."""
    return ThreadPoolExecutor(max_workers=3, thread_name_prefix="jia-gen")


_GENERATIONS: list = []
_GENERATIONS_LOCK = threading.Lock()
_GENERATIONS_CAP = 200  # FIFO cap to keep memory bounded over long uptime


def get_generations() -> list:
    """Process-level list of generation records (resume + cover letter).

    Lives outside `st.session_state` so the tray survives browser refreshes,
    cross-tab navigation, and any Streamlit page reruns. Reset only when the
    Streamlit Python process itself restarts. Returns a snapshot list copy so
    callers iterating it don't race with mutations.
    """
    with _GENERATIONS_LOCK:
        return list(_GENERATIONS)


def submit_generation(kind: str, title: str, company: str, jd_text: str, *, location: str = ""):
    fn = generate_resume if kind == "resume" else generate_cover_letter
    if kind == "resume":
        fut = get_executor().submit(fn, title, company, jd_text, location=location)
    else:
        fut = get_executor().submit(fn, title, company, jd_text)
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


def submit_all_missing_strong_fits(min_score: int = 80) -> int:
    """Queue resume generation for every strong-fit job that doesn't already
    have a resume on disk. Returns count submitted.

    Uses the same ThreadPoolExecutor as the per-row Generate button, so
    they appear in the 🛠️ Generations sidebar tray and respect the 3-worker
    parallelism cap. Idempotent — skips files that already exist (matched via
    `existing_resume_path`, which finds both new + legacy filename forms).
    """
    df = load_jobs()
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
        _mark_pending(r["hash"], "resume")
        submitted += 1
    return submitted


@st.fragment(run_every=2)
def render_generations_tray():
    """Auto-refreshing panel showing in-flight + completed generations.

    Caller must wrap with `with st.sidebar:` — fragments cannot call st.sidebar
    directly per Streamlit's API rules.
    """
    gens = get_generations()
    st.markdown("### 🛠️ Generations")
    if not gens:
        st.caption("Nothing running. Open a job → Generate.")
        return

    in_flight = sum(1 for g in gens if not g["future"].done())
    if in_flight:
        st.caption(f"⏳ {in_flight} running · {len(gens) - in_flight} done")
    else:
        st.caption(f"{len(gens)} done")

    for g in reversed(gens[-10:]):
        fut = g["future"]
        with st.container(border=True):
            label = "Resume" if g["kind"] == "resume" else "Cover letter"
            st.markdown(f"**{label}** · {g['company']}")
            st.caption(g["title"][:60])
            if not fut.done():
                elapsed = int(time.time() - g["started_at"])
                st.caption(f"⏳ Generating... ({elapsed}s elapsed)")
            elif fut.exception() is not None:
                st.error(f"Failed: {fut.exception()}", icon="❌")
            else:
                try:
                    result = fut.result()
                    path = result[0] if isinstance(result, tuple) else result
                    with open(path, "rb") as f:
                        data = f.read()
                    st.download_button(
                        "⬇ Download .docx",
                        data,
                        file_name=Path(path).name,
                        key=f"tray_dl_{g['id']}",
                        use_container_width=True,
                    )
                except Exception as e:
                    st.error(f"Read failed: {e}", icon="❌")

    if st.button("Clear completed", key="clear_gens"):
        with _GENERATIONS_LOCK:
            _GENERATIONS[:] = [g for g in _GENERATIONS if not g["future"].done()]
        st.rerun()


def run_pipeline(do_scrape: bool, do_prefilter: bool, do_score: bool, score_limit: int = 200):
    """Run the scrape -> prefilter -> score pipeline with live status."""
    db.init_db()
    summary = {"new": 0, "skipped": 0, "prefilter_pass": 0, "scored": 0, "errors": []}

    with st.status("Running pipeline...", expanded=True) as status:
        if do_scrape:
            st.write("🔎 Scraping configured sources...")
            try:
                jobs = run_all_sync()
                if jobs:
                    new, skipped = db.upsert_many(jobs)
                    summary["new"], summary["skipped"] = new, skipped
                    st.write(f"✅ Scraped {len(jobs)} jobs · {new} new, {skipped} skipped (duplicates)")
                else:
                    st.write("⚠️ No jobs returned")
            except Exception as e:
                summary["errors"].append(f"scrape: {e}")
                st.write(f"❌ Scrape failed: {e}")

        if do_prefilter:
            st.write("🧹 Running rule-based prefilter...")
            pending = db.get_unfiltered()
            passed = 0
            for j in pending:
                ok, reason, sponsorship = run_prefilter(j["title"], j["description"] or "")
                db.update_prefilter(j["hash"], ok, reason, sponsorship)
                if ok:
                    passed += 1
            summary["prefilter_pass"] = passed
            st.write(f"✅ Prefilter: {passed}/{len(pending)} passed")

        if do_score:
            pending = db.get_unscored_passed()[:score_limit]
            if not pending:
                st.write("⚠️ Nothing to score")
            else:
                model = os.environ.get("SCORING_MODEL", "anthropic/claude-haiku-4.5")
                st.write(f"🤖 Scoring {len(pending)} jobs with `{model}`...")
                client = make_client()
                scored = 0
                auto_queued = 0
                progress = st.progress(0.0, text="0 scored")
                for idx, j in enumerate(pending, start=1):
                    result = score_job(
                        client, model,
                        title=j["title"], company=j["company"], location=j["location"],
                        description=j["description"] or "", sponsorship=j["sponsorship_status"],
                    )
                    if not result:
                        db.record_score_failure(j["hash"])
                    if result:
                        total = int(result.get("total", 0))
                        tier = result.get("tier") or compute_tier(total)
                        breakdown = json.dumps({
                            k: result.get(k) for k in
                            ["title_match", "skills_match", "leadership_scope",
                             "domain_alignment", "location_fit", "comp_confidence"]
                        })
                        db.update_score(j["hash"], total, breakdown, result.get("rationale", ""), tier)
                        scored += 1
                        if (
                            tier == "strong" and total >= 80
                            and auto_queued < AUTO_RESUME_CAP_PER_CYCLE
                        ):
                            loc = j.get("location") or ""
                            if not existing_resume_path(j["title"], j["company"], loc).exists():
                                submit_generation(
                                    "resume", j["title"], j["company"], j["description"] or ""
                                )
                                auto_queued += 1
                    progress.progress(idx / len(pending), text=f"{scored} scored / {idx} attempted")
                summary["scored"] = scored
                st.write(f"✅ Scored {scored}/{len(pending)} jobs")
                if auto_queued:
                    st.write(
                        f"📝 Auto-queued {auto_queued} resume(s) for strong fits — "
                        "see the **🛠️ Generations** sidebar tray."
                    )

        status.update(label="Pipeline complete", state="complete", expanded=False)

    return summary


# ---------------------------------------------------------------------------
# Background auto-scrape
# ---------------------------------------------------------------------------

_AUTO_STATE: dict | None = None
_AUTO_THREAD: threading.Thread | None = None
_AUTO_LOCK = threading.Lock()

# Hard ceiling on auto-resume generation per cycle. Keeps Sonnet cost bounded
# even if a single scrape surfaces dozens of strong fits at once. Override
# via AUTO_RESUME_CAP_PER_CYCLE in .env.
AUTO_RESUME_CAP_PER_CYCLE = int(os.environ.get("AUTO_RESUME_CAP_PER_CYCLE", "5"))


def _run_pipeline_headless(score_limit: int) -> dict:
    """Same logic as run_pipeline() but with no Streamlit UI calls.
    Safe to call from a background thread.
    """
    out = {"new": 0, "skipped": 0, "prefilter_pass": 0, "scored": 0, "auto_resumes": 0, "error": None}
    try:
        db.init_db()
        jobs = run_all_sync()
        if jobs:
            new, skipped = db.upsert_many(jobs)
            out["new"], out["skipped"] = new, skipped

        pending = db.get_unfiltered()
        for j in pending:
            ok, reason, sponsorship = run_prefilter(j["title"], j["description"] or "")
            db.update_prefilter(j["hash"], ok, reason, sponsorship)
            if ok:
                out["prefilter_pass"] += 1

        score_pending = db.get_unscored_passed()[:score_limit]
        if score_pending:
            model = os.environ.get("SCORING_MODEL", "anthropic/claude-haiku-4.5")
            client = make_client()
            for j in score_pending:
                result = score_job(
                    client, model,
                    title=j["title"], company=j["company"], location=j["location"],
                    description=j["description"] or "", sponsorship=j["sponsorship_status"],
                )
                if not result:
                    db.record_score_failure(j["hash"])
                if result:
                    total = int(result.get("total", 0))
                    tier = result.get("tier") or compute_tier(total)
                    breakdown = json.dumps({
                        k: result.get(k) for k in
                        ["title_match", "skills_match", "leadership_scope",
                         "domain_alignment", "location_fit", "comp_confidence"]
                    })
                    db.update_score(j["hash"], total, breakdown, result.get("rationale", ""), tier)
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


def _autoscrape_loop(state: dict):
    """Daemon loop: wakes every 5s, runs pipeline when interval has elapsed.

    All reads/writes of `state` are guarded by `_AUTO_LOCK` so concurrent
    Streamlit reruns can't tear values mid-update.
    """
    while True:
        try:
            with _AUTO_LOCK:
                now = time.time()
                should_run = (
                    state["enabled"]
                    and not state["in_progress"]
                    and (state["last_run_at"] is None
                         or now - state["last_run_at"] >= state["interval_seconds"])
                )
                score_limit = state["score_limit"]
                if should_run:
                    state["in_progress"] = True
                    state["last_started_at"] = time.time()
            if should_run:
                # Run the pipeline OUTSIDE the lock — it can take minutes and
                # we don't want to block the UI fragment from reading status.
                summary = _run_pipeline_headless(score_limit)
                with _AUTO_LOCK:
                    state["last_run_summary"] = summary
                    state["last_error"] = summary.get("error")
                    state["last_run_at"] = time.time()
                    state["next_run_at"] = state["last_run_at"] + state["interval_seconds"]
                    state["in_progress"] = False
                    state["run_count"] += 1
        except Exception as e:
            with _AUTO_LOCK:
                state["last_error"] = f"loop: {e}"
                state["in_progress"] = False
        time.sleep(5)


def get_autoscrape_state() -> dict:
    global _AUTO_STATE, _AUTO_THREAD
    with _AUTO_LOCK:
        if _AUTO_STATE is None:
            _AUTO_STATE = {
                "enabled": False,
                "interval_seconds": 21600,  # 6 hours — fresh enough for job listings,
                                            # cheap enough on token spend
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


def _fmt_relative(ts: float | None) -> str:
    if ts is None:
        return "never"
    delta = int(time.time() - ts)
    if delta < 0:
        return f"in {-delta}s"
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h {(delta % 3600) // 60}m ago"


@st.fragment(run_every=5)
def render_autoscrape_controls():
    """Auto-refreshing sidebar panel: toggle, interval, status. Caller wraps with `with st.sidebar`.

    Snapshot the daemon state under the lock before rendering so values are
    consistent for one render pass; write back UI changes under the lock.
    """
    state = get_autoscrape_state()
    with _AUTO_LOCK:
        snap = dict(state)

    st.markdown("### 🔁 Auto-scrape")

    enabled = st.toggle(
        "Enabled",
        value=snap["enabled"],
        key="auto_enabled",
        help="When on, the pipeline runs in the background on the chosen interval.",
    )

    interval_choices = {
        "15 min": 900, "30 min": 1800, "1 hour": 3600,
        "3 hours": 10800, "6 hours": 21600, "12 hours": 43200,
    }
    current_label = next(
        (k for k, v in interval_choices.items() if v == snap["interval_seconds"]),
        "6 hours",
    )
    label = st.selectbox(
        "Interval", list(interval_choices.keys()),
        index=list(interval_choices.keys()).index(current_label),
        key="auto_interval",
    )
    new_interval = interval_choices[label]

    new_score_limit = st.number_input(
        "Score limit per run", min_value=10, max_value=500,
        value=snap["score_limit"], step=10, key="auto_score_limit",
        help="LLM-score at most this many new prefilter survivors per cycle. Caps cost.",
    )

    with _AUTO_LOCK:
        state["enabled"] = enabled
        state["interval_seconds"] = new_interval
        state["score_limit"] = int(new_score_limit)

    if snap["in_progress"]:
        elapsed = int(time.time() - (snap["last_started_at"] or time.time()))
        st.info(f"⏳ Running... ({elapsed}s)")
    elif snap["enabled"]:
        st.caption(
            f"Last run: {_fmt_relative(snap['last_run_at'])} · "
            f"Next: {_fmt_relative(snap['next_run_at'])}"
        )
    else:
        st.caption("Off — flip the toggle to start.")

    last = snap["last_run_summary"]
    if last:
        st.caption(
            f"Last cycle: +{last.get('new', 0)} new · "
            f"{last.get('skipped', 0)} dup · "
            f"{last.get('prefilter_pass', 0)} pf-pass · "
            f"{last.get('scored', 0)} scored · "
            f"📝 {last.get('auto_resumes', 0)} resumes"
        )
    if snap["last_error"]:
        st.error(snap["last_error"], icon="❌")


def render_pipeline_controls(df_empty: bool):
    with st.sidebar.expander("⚡ Crawl & score", expanded=df_empty):
        do_scrape = st.checkbox("Scrape sources", value=True, key="run_scrape")
        do_prefilter = st.checkbox("Prefilter", value=True, key="run_prefilter")
        do_score = st.checkbox("LLM score", value=True, key="run_score")
        score_limit = st.number_input(
            "Score limit", min_value=1, max_value=2000, value=200, step=50, key="run_score_limit"
        )
        if st.button("Run now", type="primary", use_container_width=True, key="run_pipeline_btn"):
            run_pipeline(do_scrape, do_prefilter, do_score, int(score_limit))
            st.cache_data.clear()
            st.rerun()

    with st.sidebar.expander("📝 Bulk generate", expanded=False):
        st.caption(
            "Queues resume generation for every strong-fit job that doesn't "
            "already have a resume on disk. Watch the **🛠️ Generations** panel."
        )
        if st.button(
            "Generate all missing strong-fit resumes",
            type="primary",
            use_container_width=True,
            key="bulk_gen_btn",
        ):
            n = submit_all_missing_strong_fits()
            if n:
                st.toast(
                    f"📝 Queued {n} resume generation{'s' if n != 1 else ''} — see the 🛠️ Generations tray.",
                    icon="📝",
                )
            else:
                st.toast("All strong fits already have resumes ✓", icon="✅")
            st.rerun()


def render_sidebar(df: pd.DataFrame) -> dict:
    with st.sidebar:
        render_autoscrape_controls()
    st.sidebar.markdown("---")
    render_pipeline_controls(df.empty)
    with st.sidebar:
        render_generations_tray()
    st.sidebar.markdown("---")
    st.sidebar.title("Filters")
    if df.empty:
        st.sidebar.info("No jobs yet. Click **Run now** above to scrape.")
        return {}

    show_rejects = st.sidebar.toggle(
        "Show prefilter rejects",
        value=False,
        help="Off (default): only jobs that passed the rule-based prefilter (~282 of ~6.2k). On: include the irrelevant ones too.",
    )

    sources = sorted(df["source"].dropna().unique().tolist())
    selected_sources = st.sidebar.multiselect("Source", sources, default=sources)

    tiers = ["strong", "possible", "stretch", "skip", "(unscored)"]
    selected_tiers = st.sidebar.multiselect("Tier", tiers, default=tiers)

    min_score = st.sidebar.slider("Min score", 0, 100, 0)

    sponsorship_options = ["offered", "unknown", "denied"]
    selected_sponsorship = st.sidebar.multiselect(
        "Sponsorship", sponsorship_options, default=sponsorship_options
    )

    applied_filter = st.sidebar.radio(
        "Application status", ["All", "Not applied", "Applied"], index=0
    )

    company_search = st.sidebar.text_input("Search company / title").strip().lower()

    page_size = st.sidebar.select_slider("Per page", options=[25, 50, 100, 250, 500], value=50)

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Total in DB: {len(df)} jobs")

    return {
        "show_rejects": show_rejects,
        "sources": selected_sources,
        "tiers": selected_tiers,
        "min_score": min_score,
        "sponsorship": selected_sponsorship,
        "applied_filter": applied_filter,
        "search": company_search,
        "page_size": page_size,
    }


def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if not f.get("show_rejects"):
        out = out[out["prefilter_passed"] == 1]
    if f.get("sources"):
        out = out[out["source"].isin(f["sources"])]
    if f.get("tiers"):
        scored_tiers = [t for t in f["tiers"] if t != "(unscored)"]
        include_unscored = "(unscored)" in f["tiers"]
        mask = out["tier"].isin(scored_tiers)
        if include_unscored:
            mask = mask | out["tier"].fillna("").eq("")
        out = out[mask]
    if f.get("min_score") is not None:
        out = out[(out["score_total"].fillna(0) >= f["min_score"])]
    if f.get("sponsorship"):
        out = out[out["sponsorship_status"].isin(f["sponsorship"])]
    if f.get("applied_filter") == "Applied":
        out = out[out["applied"] == 1]
    elif f.get("applied_filter") == "Not applied":
        out = out[out["applied"] == 0]
    if f.get("search"):
        s = f["search"]
        out = out[
            out["company"].str.lower().str.contains(s, na=False)
            | out["title"].str.lower().str.contains(s, na=False)
        ]
    return out


def render_job_details(job: dict):
    score = job.get("score_total")
    tier = (job.get("tier") or "").lower()
    tier_color = TIER_COLORS.get(tier, "#6b7280")
    job_hash = job["hash"]

    head_l, head_r = st.columns([0.78, 0.22])
    with head_l:
        st.markdown(f"### {job['title']}")
        st.markdown(
            f"**{job['company']}**  ·  📍 {job['location']}  ·  🌐 {job['source']}"
        )
    with head_r:
        st.markdown(
            f'<div style="text-align:right;">'
            f'<div style="display:inline-block;background:{score_color(score)};color:white;'
            f'width:64px;height:64px;border-radius:50%;line-height:64px;text-align:center;'
            f'font-weight:700;font-size:22px;">{score_label(score)}</div>'
            f'<div style="margin-top:6px;"><span style="background:{tier_color};color:white;'
            f'padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700;">'
            f'{TIER_LABELS.get(tier, "UNSCORED")}</span></div></div>',
            unsafe_allow_html=True,
        )

    # ------------------------------------------------------------------
    # Top action bar: Applied toggle (prominent) + Open posting link
    # ------------------------------------------------------------------
    bar_l, bar_r = st.columns([0.5, 0.5])
    with bar_l:
        applied_now = st.toggle(
            "✓ Mark as applied",
            value=bool(job.get("applied")),
            key=f"applied_toggle_{job_hash}",
            help="Tracked in the DB. Filterable from the sidebar.",
        )
        if bool(applied_now) != bool(job.get("applied")):
            db.set_applied(job_hash, bool(applied_now))
            st.cache_data.clear()
            st.rerun()
    with bar_r:
        if job.get("url"):
            st.link_button(
                "🔗 OPEN JOB POSTING ↗",
                job["url"],
                use_container_width=True,
                type="primary",
                help="Opens the original job posting in a new browser tab.",
            )

    # ------------------------------------------------------------------
    # Quick copy strip — always visible. `st.code` blocks show a one-click
    # copy icon on hover, which is Streamlit's only native per-cell-style
    # copy affordance. Laid out as a 2-up grid so it stays compact.
    # ------------------------------------------------------------------
    st.caption("📋 Hover any block below → click the copy icon (top-right corner).")
    qc_l, qc_r = st.columns(2)
    with qc_l:
        st.code(job.get("title", ""), language=None)
        st.code(job.get("company", ""), language=None)
    with qc_r:
        if job.get("url"):
            st.code(job["url"], language=None)
        st.code(f"{job.get('title','')} at {job.get('company','')}", language=None)

    pills = []
    sp_key = job.get("sponsorship_status") or "unknown"
    if sp_key in SPONSORSHIP_BADGES:
        emoji, text = SPONSORSHIP_BADGES[sp_key]
        pills.append(f"{emoji} {text}")
    if job.get("salary_min") and job.get("salary_max"):
        try:
            pills.append(f"💰 ${int(job['salary_min']):,} - ${int(job['salary_max']):,}")
        except Exception:
            pass
    if job.get("remote"):
        pills.append("🏠 Remote")
    if job.get("posted_at"):
        pills.append(f"📅 {str(job['posted_at'])[:10]}")
    if pills:
        st.caption("  ·  ".join(pills))

    if job.get("score_rationale"):
        st.info(job["score_rationale"])

    if job.get("score_breakdown"):
        try:
            bd = json.loads(job["score_breakdown"])
            cols = st.columns(6)
            cols[0].metric("Title", f"{bd.get('title_match', '?')}/30")
            cols[1].metric("Skills", f"{bd.get('skills_match', '?')}/25")
            cols[2].metric("Scope", f"{bd.get('leadership_scope', '?')}/15")
            cols[3].metric("Domain", f"{bd.get('domain_alignment', '?')}/10")
            cols[4].metric("Loc/Visa", f"{bd.get('location_fit', '?')}/10")
            cols[5].metric("Comp", f"{bd.get('comp_confidence', '?')}/10")
        except Exception:
            pass

    notes = st.text_area(
        "Notes",
        value=job.get("notes") or "",
        key=f"notes_{job_hash}",
        height=80,
        placeholder="Why this role, prep notes, recruiter contact...",
    )
    if notes != (job.get("notes") or ""):
        db.set_notes(job_hash, notes)
        st.cache_data.clear()

    with st.expander("Job description", expanded=False):
        raw = job.get("description") or ""
        if raw:
            cleaned = clean_jd_text(raw)[:8000]
            # Render as monospace text so paragraph breaks and bullet
            # indentation survive without inviting markdown re-interpretation
            # (JD copy occasionally contains stray '*' / '_' / '#' characters).
            st.text(cleaned)
        else:
            st.caption("No description captured.")

    st.caption("Generation runs in the background — keep browsing while it finishes. Watch the **🛠️ Generations** panel in the sidebar.")
    gen1, gen2 = st.columns(2)
    with gen1:
        if st.button("Generate resume", key=f"resume_{job_hash}", use_container_width=True):
            submit_generation("resume", job["title"], job["company"], job.get("description") or "")
            _mark_pending(job_hash, "resume")
            st.toast(f"📝 Generating resume for {job['company']}...", icon="📝")
            st.rerun()
    with gen2:
        if st.button(
            "Generate cover letter", key=f"cover_{job_hash}", use_container_width=True
        ):
            submit_generation("cover", job["title"], job["company"], job.get("description") or "")
            _mark_pending(job_hash, "cover")
            st.toast(f"✉️ Generating cover letter for {job['company']}...", icon="✉️")
            st.rerun()

    # Auto-refreshing artifacts block — picks up freshly-generated files
    # without requiring a manual page refresh, and shows in-flight progress
    # while a generation is running in the background thread.
    # Use `existing_resume_path` so legacy (no-suffix) filenames are still
    # discovered alongside the new location-aware naming.
    resume_path = existing_resume_path(
        job["title"], job["company"], job.get("location") or ""
    )
    cover_path = expected_cover_letter_path(job["title"], job["company"])
    _render_artifacts_fragment(
        job_hash=job_hash,
        job_title=job["title"],
        job_company=job["company"],
        job_location=job.get("location") or "",
        job_description=job.get("description") or "",
        resume_path_str=str(resume_path),
        cover_path_str=str(cover_path),
    )


def _df_fingerprint(d: pd.DataFrame):
    """Fast content fingerprint for st.cache_data hashing of a DataFrame.

    Hashes only the columns that drive the table view — hash (row identity),
    score_total, tier, applied — so notes/JD edits don't bust the cache. Costs
    a few ms on 6k rows; pays back many-fold by avoiding the full reshape.
    """
    if d is None or d.empty:
        return ()
    cols = [c for c in ("hash", "score_total", "tier", "applied") if c in d.columns]
    if not cols:
        return (len(d),)
    h = pd.util.hash_pandas_object(d[cols].fillna(""), index=False)
    return (len(d), int(h.sum()))


@st.cache_data(show_spinner=False, hash_funcs={pd.DataFrame: _df_fingerprint})
def _build_table_view(df: pd.DataFrame) -> pd.DataFrame:
    """Reshape the raw jobs DataFrame into a compact display DataFrame.
    Cached on a cheap fingerprint of the source df (size + hash sum + score sum)
    so the column-by-column transformation only runs when the underlying data
    actually changes."""
    out = pd.DataFrame()

    # Order: link column first (left), then title — matches the user's preference
    # to land on "open" before scanning down each row's content.
    out["url"] = df["url"]
    out["score"] = df["score_total"].fillna(0).astype(int)
    out["tier"] = df["tier"].fillna("").map(lambda t: TIER_EMOJI.get(t, "—"))
    out["title"] = df["title"]
    out["company"] = df["company"]
    out["location"] = df["location"].fillna("")

    def _salary(row):
        lo, hi = row.get("salary_min"), row.get("salary_max")
        try:
            if lo and hi:
                return f"${int(lo):,}-${int(hi):,}"
        except Exception:
            pass
        return ""

    out["salary"] = df.apply(_salary, axis=1)
    out["sponsor"] = df["sponsorship_status"].fillna("unknown").map(
        lambda s: SPONSORSHIP_EMOJI.get(s, "")
    )
    out["posted"] = df["posted_at"].fillna("").astype(str).str[:10]
    out["source"] = df["source"]
    out["applied"] = df["applied"].astype(bool)
    out["hash"] = df["hash"]  # carried for editor-row → DB writes; hidden in UI
    return out


def render_table(df: pd.DataFrame) -> str | None:
    """Renders the jobs table as an `st.data_editor`. Returns the *hash* of
    the selected row, or None.

    Two interactive columns:
      - **👁 (View)**: checkbox that drives the detail panel below. Single-row
        exclusive — toggling another row's View takes selection from the
        previous one.
      - **Applied**: editable checkbox that writes through to the DB instantly.

    All other columns are read-only.
    """
    view = _build_table_view(df).copy()

    # Inject a session-controlled "view" column so the detail panel selection
    # round-trips through the editor. Default False; True for whichever row's
    # hash matches `selected_job_hash` in session state.
    selected_hash = st.session_state.get("selected_job_hash")
    view.insert(0, "view", view["hash"].eq(selected_hash) if selected_hash else False)

    # Hide the `hash` column visually but keep it in the dataframe so we can
    # match edits back to DB rows by stable identity.
    column_order = [c for c in view.columns if c != "hash"]

    edited = st.data_editor(
        view,
        column_order=column_order,
        column_config={
            "view": st.column_config.CheckboxColumn(
                "👁", width="small",
                help="Click to open this row's details below. Click again to close.",
            ),
            "url": st.column_config.LinkColumn(
                "Open",
                display_text="🔗 Open ↗",
                width="medium",
                help="Open the original posting in a new tab.",
            ),
            "score": st.column_config.ProgressColumn(
                "Score", format="%d", min_value=0, max_value=100, width="small"
            ),
            "tier": st.column_config.TextColumn("Tier", width="small"),
            "title": st.column_config.TextColumn("Title", width="large"),
            "company": st.column_config.TextColumn("Company", width="medium"),
            "location": st.column_config.TextColumn("Location", width="medium"),
            "salary": st.column_config.TextColumn("Salary", width="small"),
            "sponsor": st.column_config.TextColumn("Visa", width="small", help="Sponsorship status"),
            "posted": st.column_config.TextColumn("Posted", width="small"),
            "source": st.column_config.TextColumn("Source", width="small"),
            "applied": st.column_config.CheckboxColumn(
                "Applied", width="small",
                help="Toggle to mark this job as applied. Writes through to the DB.",
            ),
        },
        disabled=[
            "url", "score", "tier", "title", "company", "location",
            "salary", "sponsor", "posted", "source",
        ],
        hide_index=True,
        use_container_width=True,
        height=560,
        key="jobs_editor",
    )

    # Pull the per-row diff from the editor's session state (cheaper than
    # comparing whole DataFrames, and stable across pagination).
    state = st.session_state.get("jobs_editor", {})
    edited_rows: dict = state.get("edited_rows") or {}

    needs_rerun = False
    for row_idx_str, changes in edited_rows.items():
        try:
            row_idx = int(row_idx_str)
        except (TypeError, ValueError):
            continue
        if row_idx < 0 or row_idx >= len(view):
            continue
        row_hash = view["hash"].iloc[row_idx]

        if "applied" in changes:
            new_applied = bool(changes["applied"])
            db.set_applied(row_hash, new_applied)
            st.cache_data.clear()
            needs_rerun = True

        if "view" in changes:
            new_view = bool(changes["view"])
            if new_view:
                st.session_state["selected_job_hash"] = row_hash
            else:
                # Unchecked the currently-selected row → close panel
                if st.session_state.get("selected_job_hash") == row_hash:
                    st.session_state.pop("selected_job_hash", None)
            needs_rerun = True

    if needs_rerun:
        # Fragment-scoped rerun: only the table + detail panel re-renders;
        # the page header, sidebar, and metrics stay in place.
        st.rerun(scope="fragment")

    return st.session_state.get("selected_job_hash")


def main():
    st.title("Job Intelligence Agent")
    df = load_jobs()
    filters = render_sidebar(df)

    if df.empty:
        st.info("No jobs scraped yet. Run `python -m src.cli run` from the terminal.")
        return

    filtered = apply_filters(df, filters)
    filtered = filtered.sort_values(
        by=["score_total", "scraped_at"], ascending=[False, False], na_position="last"
    )

    relevant_count = int((df["prefilter_passed"] == 1).sum())
    m = st.columns(4)
    m[0].metric("Showing", len(filtered))
    m[1].metric("Strong fits (80+)", int((filtered["score_total"].fillna(0) >= 80).sum()))
    m[2].metric("Relevant (post-prefilter)", relevant_count)
    m[3].metric("Total scraped", len(df))

    if filtered.empty:
        st.warning("No jobs match the current filters.")
        return

    page_size = filters.get("page_size", 50)
    total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = st.number_input(
        f"Page (of {total_pages})",
        min_value=1,
        max_value=total_pages,
        value=1,
        step=1,
    )
    start = (page - 1) * page_size
    page_df = filtered.iloc[start : start + page_size].reset_index(drop=True)

    _render_table_and_detail(page_df, filtered)


@st.fragment
def _render_table_and_detail(page_df: pd.DataFrame, filtered: pd.DataFrame):
    """Single fragment so row selection / Applied edits only re-render this
    block — sidebar, header, and metrics stay put. Eliminates the full-page
    flash on every click."""
    selected_hash = render_table(page_df)

    if selected_hash:
        # Look up in the full filtered df so the selection survives pagination
        matches = filtered[filtered["hash"] == selected_hash]
        if not matches.empty:
            st.markdown("---")
            job = matches.iloc[0].to_dict()
            with st.container(border=True):
                render_job_details(job)
        else:
            st.caption("Selected job is no longer in the filtered set. Adjust filters or pick another row.")
    else:
        st.caption("👉 Toggle the **👁** column on any row to open its details.")


if __name__ == "__main__":
    main()
