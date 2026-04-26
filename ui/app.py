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
from src.generate.resume import generate_resume, autogen_resume_if_missing, expected_resume_path
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


@st.cache_data(ttl=30)
def load_jobs() -> pd.DataFrame:
    return db.to_dataframe()


@st.cache_resource
def get_executor() -> ThreadPoolExecutor:
    """Process-wide executor so workers survive Streamlit reruns."""
    return ThreadPoolExecutor(max_workers=3, thread_name_prefix="jia-gen")


def get_generations() -> list:
    if "generations" not in st.session_state:
        st.session_state.generations = []
    return st.session_state.generations


def submit_generation(kind: str, title: str, company: str, jd_text: str):
    fn = generate_resume if kind == "resume" else generate_cover_letter
    fut = get_executor().submit(fn, title, company, jd_text)
    get_generations().append({
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "title": title,
        "company": company,
        "future": fut,
        "started_at": time.time(),
    })


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
        st.session_state.generations = [g for g in gens if not g["future"].done()]
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
                            if not expected_resume_path(j["title"], j["company"], loc).exists():
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
                "interval_seconds": 1800,  # 30 min
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
        "30 min",
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

    job_hash = job["hash"]

    track_col, link_col = st.columns([0.5, 0.5])
    with track_col:
        applied_now = st.radio(
            "Application",
            ["Not applied", "Applied"],
            index=1 if job.get("applied") else 0,
            horizontal=True,
            key=f"applied_{job_hash}",
            label_visibility="collapsed",
        )
        new_state = applied_now == "Applied"
        if new_state != bool(job.get("applied")):
            db.set_applied(job_hash, new_state)
            st.cache_data.clear()
            st.rerun()
    with link_col:
        if job.get("url"):
            st.link_button("Open posting", job["url"])

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
        if job.get("description"):
            st.text(job["description"][:8000])
        else:
            st.caption("No description captured.")

    st.caption("Generation runs in the background — keep browsing while it finishes. Watch the **🛠️ Generations** panel in the sidebar.")
    gen1, gen2 = st.columns(2)
    with gen1:
        if st.button("Generate resume", key=f"resume_{job_hash}", use_container_width=True):
            submit_generation("resume", job["title"], job["company"], job.get("description") or "")
            st.toast(f"📝 Generating resume for {job['company']}...", icon="📝")
            st.rerun()
    with gen2:
        if st.button(
            "Generate cover letter", key=f"cover_{job_hash}", use_container_width=True
        ):
            submit_generation("cover", job["title"], job["company"], job.get("description") or "")
            st.toast(f"✉️ Generating cover letter for {job['company']}...", icon="✉️")
            st.rerun()


def _build_table_view(df: pd.DataFrame) -> pd.DataFrame:
    """Reshape the raw jobs DataFrame into a compact display DataFrame."""
    out = pd.DataFrame()
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
    out["url"] = df["url"]
    return out


def render_table(df: pd.DataFrame) -> int | None:
    """Renders the jobs table. Returns the iloc-index of the selected row, or None."""
    view = _build_table_view(df)
    event = st.dataframe(
        view,
        column_config={
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
            "applied": st.column_config.CheckboxColumn("Applied", width="small", disabled=True),
            "url": st.column_config.LinkColumn("Open", display_text="↗", width="small"),
        },
        hide_index=True,
        use_container_width=True,
        height=560,
        selection_mode="single-row",
        on_select="rerun",
        key="jobs_table",
    )
    rows = event.selection.rows if event.selection else []
    return rows[0] if rows else None


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

    selected_idx = render_table(page_df)

    if selected_idx is not None and 0 <= selected_idx < len(page_df):
        st.markdown("---")
        job = page_df.iloc[selected_idx].to_dict()
        with st.container(border=True):
            render_job_details(job)
    else:
        st.caption("👉 Click a row above to see details and generate resume / cover letter.")


if __name__ == "__main__":
    main()
