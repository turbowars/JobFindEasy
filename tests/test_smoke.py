"""10-test safety net for JobFindEasy.

Every test here corresponds to a bug we shipped to main this week. Running
this suite before merge would have caught each one. Don't add tests for
hypotheticals — only add tests for bugs you've actually seen.

Naming convention: test_<area>_<expected_behavior>.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# 1. URL-based dedup hash
# ---------------------------------------------------------------------------
def test_job_hash_collapses_url_aliases():
    """Two Jobs with the same canonical URL produce the same hash, even when
    title/location strings differ slightly. Bug: pre-rewrite, hashes were
    built from (source, company, title, location) and Coinbase posted the
    same role twice with different location strings, producing dup rows."""
    from src.models import Job

    url = "https://www.coinbase.com/careers/positions/7769397?gh_jid=7769397"
    a = Job(
        source="greenhouse",
        company="Coinbase",
        title="Engineering Manager, Identity Frontend",
        location="Remote - USA",
        url=url,
    )
    b = Job(
        source="greenhouse",
        company="Coinbase",
        title="Engineering Manager, Identity Frontend",
        location="REMOTE United States",  # different string, same role
        url=url,
    )
    c = Job(
        source="greenhouse",
        company="Coinbase",
        title="Engineering Manager, Identity Frontend",
        location="Remote - USA",
        url="https://different.example.com/job/123",  # different URL → different hash
    )
    assert a.hash == b.hash, "same URL should collapse to one hash regardless of location string"
    assert a.hash != c.hash, "different URL should produce a different hash"


# ---------------------------------------------------------------------------
# 2. ATS keyword extract retry on transient API failure
# ---------------------------------------------------------------------------
def test_extract_keywords_retries_on_failure(monkeypatch):
    """When the underlying chat() call raises (e.g., a transient 403),
    extract_keywords retries once before giving up. Bug: a 403 on the first
    keyword extract poisoned the LRU cache with `{}`, leaving every retry
    scoring 0% ATS forever."""
    from src.enrichment import ats_match

    # Clear the LRU cache from any prior test
    ats_match._extract_cached.cache_clear()

    calls = {"n": 0}

    def fake_chat(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient 403")
        return '{"required":["React","TypeScript"],"preferred":["GraphQL"],"soft":["agile"]}'

    monkeypatch.setattr(ats_match, "chat", fake_chat)
    result = ats_match.extract_keywords("Some JD text", "fake_cache_key")
    assert calls["n"] == 2, "should have retried after the first failure"
    assert "React" in result["required"]
    assert "GraphQL" in result["preferred"]


# ---------------------------------------------------------------------------
# 3. JSON serialization tolerates NaN salary fields
# ---------------------------------------------------------------------------
def test_jobs_json_serializes_nan_salary_as_null(monkeypatch):
    """`/api/jobs.json` must convert pandas NaN salary values to JSON null,
    not raise ValueError. Bug: the endpoint returned 500 ('Out of range float
    values are not JSON compliant: nan') as soon as any job had a missing
    salary, which is most of them."""
    from fastapi.testclient import TestClient

    from src import db as db_module
    from web import app as web_app

    # Stub to_dataframe with one row that has NaN salaries
    df = pd.DataFrame(
        [
            {
                "hash": "abc123",
                "source": "greenhouse",
                "company": "Coinbase",
                "title": "EM",
                "location": "Remote",
                "url": "https://example.com",
                "description": "x",
                "posted_at": "2026-04-20",
                "salary_min": float("nan"),
                "salary_max": float("nan"),
                "remote": 1,
                "sponsorship_status": "unknown",
                "prefilter_passed": 1,
                "prefilter_reason": "",
                "score_total": 95.0,
                "score_breakdown": "{}",
                "score_rationale": "",
                "tier": "strong",
                "status": "new",
                "status_at": None,
                "closed_reason": None,
                "applied_at": None,
                "notes": "",
                "scraped_at": "2026-04-20T00:00:00",
            }
        ]
    )
    monkeypatch.setattr(db_module, "to_dataframe", lambda: df)

    client = TestClient(web_app.app)
    r = client.get("/api/jobs.json?show_rejects=false")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:200]}"
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["salary_min"] is None
    assert rows[0]["salary_max"] is None


# ---------------------------------------------------------------------------
# 4. get_model() fallback chain
# ---------------------------------------------------------------------------
def test_get_model_resolution_chain(clean_model_env, monkeypatch):
    """Resolution: per-role var → SCORING_MODEL → built-in default. Bug: every
    callsite used to inline its own `os.environ.get()` with a different
    default, so changing the default required editing 6 files."""
    from src.llm import get_model

    # 1. With nothing set, falls back to built-in default
    assert get_model("job_scoring") == "anthropic/claude-haiku-4.5"
    # 2. SCORING_MODEL takes precedence over default
    monkeypatch.setenv("SCORING_MODEL", "shared/scoring-model")
    assert get_model("job_scoring") == "shared/scoring-model"
    # 3. Per-role var takes precedence over SCORING_MODEL
    monkeypatch.setenv("JOB_SCORING_MODEL", "google/gemini-2.0-flash-001")
    assert get_model("job_scoring") == "google/gemini-2.0-flash-001"
    # 4. Other roles still fall back to SCORING_MODEL
    assert get_model("hr_sim") == "shared/scoring-model"


# ---------------------------------------------------------------------------
# 5. Track detection from JD title
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "title,expected",
    [
        ("Engineering Manager, Identity Frontend", "em"),
        ("Senior Engineering Manager", "em"),
        ("Director of Engineering", "em"),
        ("VP of Engineering", "em"),
        ("Head of Engineering, Platform", "em"),
        ("Staff Frontend Engineer", "ic"),
        ("Principal Software Engineer", "ic"),
        ("Tech Lead, Frontend", "ic"),
        ("EM", "em"),
        ("Senior Backend Engineer", "ic"),
        # Bug pinned: titles that pair "Manager" with "Engineering" in a
        # non-contiguous shape ("Senior Manager, Platform Engineering")
        # are still EM roles — Twilio, Stripe, and others phrase EM titles
        # this way. The primary regex misses them because "engineering"
        # lives in the qualifier after the comma.
        ("Senior Manager, Platform Engineering - Secure Supply Chain", "em"),
        ("Senior Manager, Engineering Tools", "em"),
        ("Manager, Platform Engineering", "em"),
        # Excluded: non-EM "Manager" roles whose JD body mentions engineering
        ("Project Manager, Engineering Programs", "ic"),
        ("Product Manager, Engineering Platform", "ic"),
        ("Program Manager, Engineering Operations", "ic"),
    ],
)
def test_track_detection(title, expected):
    """Track determines section order, highlights presence, and Equifax title
    parenthetical. Misclassification would render the wrong layout."""
    from src.resume.pipeline import detect_track

    assert detect_track(title) == expected, f"{title!r} should be {expected}"


# ---------------------------------------------------------------------------
# 6. scrub_dashes invariant
# ---------------------------------------------------------------------------
def test_scrub_dashes_replaces_em_and_en_dashes():
    """All resume fields run through scrub_dashes. The .docx must never
    contain em or en dashes, regardless of which way the LLM emits them."""
    from src.utils import scrub_dashes

    assert scrub_dashes("foo — bar") == "foo - bar"
    assert scrub_dashes("foo–bar") == "foo - bar"
    assert scrub_dashes("a — b — c") == "a - b - c"
    assert scrub_dashes("") == ""
    assert scrub_dashes("no dashes here") == "no dashes here"
    # Idempotent
    once = scrub_dashes("foo — bar")
    assert scrub_dashes(once) == once


# ---------------------------------------------------------------------------
# 7. Skills validator rejects non-master items in primary categories
# ---------------------------------------------------------------------------
def test_skills_validator_filters_to_master_tree():
    """Items in PRIMARY skill categories must come from the master tree.
    Adjacency tail ('Additional Skills and Technologies') is the one
    sanctioned escape hatch and is capped at 8 items."""
    from src.resume.pipeline import _skills_from_llm

    llm_output = [
        {
            "label": "Languages",
            "items": [
                "TypeScript",  # in master tree → keep
                "FabricatedLanguage9",  # not in tree → drop
                "Python",  # in master → keep
            ],
        },
        {
            "label": "Additional Skills and Technologies",
            "items": ["WebAuthn", "Passkeys", "FIDO2"],  # adjacency tail — pass-through
        },
    ]
    out = _skills_from_llm(llm_output)
    primary = next(c for c in out if c.label == "Languages")
    assert "TypeScript" in primary.items
    assert "Python" in primary.items
    assert "FabricatedLanguage9" not in primary.items
    tail = next(c for c in out if c.label == "Additional Skills and Technologies")
    assert tail.items == ["WebAuthn", "Passkeys", "FIDO2"]


# ---------------------------------------------------------------------------
# 8. Equifax title override per track + base-title strip for team qualifiers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "jd_title,track,expected",
    [
        # Bug pinned: user reported "Engineering Manager, Autonomous Freight
        # Systems" was rendering on the Equifax line verbatim. The
        # comma-suffix is Flexport's team name and must be stripped so the
        # mirror reads as a plausible Equifax role rather than a copy of
        # the target company's specific team.
        (
            "Engineering Manager, Autonomous Freight Systems",
            "em",
            "Engineering Manager (Engineering Lead)",
        ),
        (
            "Engineering Manager, Identity Frontend",
            "em",
            "Engineering Manager (Engineering Lead)",
        ),
        (
            "Director of Engineering, Developer Ecosystem",
            "em",
            "Director of Engineering (Engineering Lead)",
        ),
        # Other separators JDs use for the same kind of qualifier
        ("Engineering Manager - Platform", "em", "Engineering Manager (Engineering Lead)"),
        ("Engineering Manager | Growth", "em", "Engineering Manager (Engineering Lead)"),
        ("Engineering Manager: Identity", "em", "Engineering Manager (Engineering Lead)"),
        # Adjective prefixes (no separator) are part of the base title
        ("Senior Engineering Manager", "em", "Senior Engineering Manager (Engineering Lead)"),
        ("VP of Engineering", "em", "VP of Engineering (Engineering Lead)"),
        # Bug pinned: user reported "Senior Manager, Platform Engineering -
        # Secure Supply Chain" was rendering as "Senior Manager, Platform
        # Engineering - Secure Supply Chain (Tech Lead)". Two errors stacked:
        # (a) detect_track missed this title (it's EM, not IC), and
        # (b) even after the comma strip, the base "Senior Manager" doesn't
        # itself say engineering, so the Equifax mirror should normalize to
        # the canonical "Engineering Manager".
        (
            "Senior Manager, Platform Engineering - Secure Supply Chain",
            "em",
            "Engineering Manager (Engineering Lead)",
        ),
        # Same normalization for any base that doesn't include engineering /
        # director / VP / head phrasing
        ("Senior Manager", "em", "Engineering Manager (Engineering Lead)"),
        ("Lead, Platform", "em", "Engineering Manager (Engineering Lead)"),
        # IC-track parenthetical — base title preserved verbatim
        ("Staff Frontend Engineer", "ic", "Staff Frontend Engineer (Tech Lead)"),
        ("Staff Software Engineer, Platform", "ic", "Staff Software Engineer (Tech Lead)"),
    ],
)
def test_equifax_title_override_strips_team_qualifier(jd_title, track, expected):
    """Equifax is the only role with a JD-flexed title. The qualifier after
    the first separator (',' ':' '|' or ' - ') is stripped because it
    refers to the target company's specific team / product, which Dheeraj
    didn't have at Equifax."""
    from src.resume.pipeline import _equifax_title_override

    assert _equifax_title_override(jd_title, track) == expected


# ---------------------------------------------------------------------------
# 9. Resume filename suffix collapses long locations + adds collision hash
# ---------------------------------------------------------------------------
def test_safe_loc_suffix_truncates_long_locations_with_hash():
    """Long location strings are truncated to ≤30 chars but get a 6-char
    content hash so two distinct long locations don't collide on the same
    filename. Bug: pre-fix, two Coinbase postings whose locations both
    started with 'San Francisco Bay Area or Remote United...' collided."""
    from src.utils import safe_loc_suffix

    short = safe_loc_suffix("Austin, TX")
    assert short == "_Austin_TX"
    assert safe_loc_suffix("") == ""

    a = safe_loc_suffix("San Francisco Bay Area or Remote United States")
    b = safe_loc_suffix("San Francisco Bay Area or Remote United Kingdom")
    assert a != b, "two distinct long locations must produce different suffixes"
    assert len(a) <= 31  # leading underscore + 30 chars
    assert len(b) <= 31


# ---------------------------------------------------------------------------
# 10. Adjacency-tail label is enforced server-side
# ---------------------------------------------------------------------------
def test_adjacency_tail_cap_is_enforced():
    """The 'Additional Skills and Technologies' tail is capped at 8 items
    so the LLM can't pad indefinitely. This is the one place where non-
    master items are allowed; without the cap the LLM could fabricate
    a long tail of JD keywords."""
    from src.resume.pipeline import _ADJACENCY_TAIL_CAP, _skills_from_llm

    llm_output = [
        {
            "label": "Additional Skills and Technologies",
            # Send 20 items; cap should clip to 8.
            "items": [f"Tool{i}" for i in range(20)],
        },
    ]
    out = _skills_from_llm(llm_output)
    tail = next(c for c in out if c.label == "Additional Skills and Technologies")
    assert len(tail.items) == _ADJACENCY_TAIL_CAP
    assert tail.items[0] == "Tool0"
    assert tail.items[-1] == f"Tool{_ADJACENCY_TAIL_CAP - 1}"


# ---------------------------------------------------------------------------
# 11. /actions/apply preserves progressed pipeline states
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "starting_status,should_transition",
    [
        ("new", True),
        ("shortlisted", True),
        ("applying", False),
        ("applied", False),  # CRITICAL: don't demote
        ("interviewing", False),  # CRITICAL: don't demote
        ("offer", False),  # CRITICAL: don't demote
        ("closed", False),
        ("not_interested", False),  # terminal — Apply must not resurrect
        ("no_sponsorship", False),  # terminal — Apply must not resurrect
    ],
)
def test_apply_action_preserves_progressed_states(monkeypatch, starting_status, should_transition):
    """Clicking the grid URL link OR the 'Apply with Claude' button on a job
    already past the early stages must NOT demote it back to 'applying'.
    Bug we just shipped: /actions/apply unconditionally flipped status to
    'applying', so a user re-opening a JD link on an interview-stage job
    silently lost their pipeline state."""
    from fastapi.testclient import TestClient

    from src import db as db_module
    from web import app as web_app

    set_status_calls = []
    monkeypatch.setattr(
        db_module,
        "get_job",
        lambda h: {"hash": h, "status": starting_status, "url": "https://x.example/y"},
    )
    monkeypatch.setattr(
        db_module,
        "set_status",
        lambda h, s, *a, **kw: set_status_calls.append((h, s)),
    )

    client = TestClient(web_app.app)
    r = client.post("/actions/apply/abc123")
    assert r.status_code == 200
    payload = r.json()
    assert payload["transitioned"] is should_transition
    if should_transition:
        assert set_status_calls == [("abc123", "applying")]
        assert payload["status"] == "applying"
    else:
        assert set_status_calls == [], (
            f"must NOT have called set_status for status={starting_status}"
        )
        assert payload["status"] == starting_status


# ---------------------------------------------------------------------------
# 12. Status transition via grid chip dropdown (incl. closed:<reason>)
# ---------------------------------------------------------------------------
def test_action_status_accepts_closed_with_reason(monkeypatch):
    """The grid's status chip dropdown POSTs `status=closed&closed_reason=...`
    in one shot. /actions/status must accept the form and forward both
    fields to db.set_status. Bug-shaped: forgetting to pass closed_reason
    silently ghosts a job without the reason, polluting the pipeline view."""
    from fastapi.testclient import TestClient

    from src import db as db_module
    from web import app as web_app

    set_status_calls = []
    monkeypatch.setattr(db_module, "get_job", lambda h: {"hash": h, "status": "applied"})
    monkeypatch.setattr(
        db_module,
        "set_status",
        lambda h, s, *a, **kw: set_status_calls.append((h, s, a, kw)),
    )

    # Stub to_dataframe so the OOB badges fragment can render without hitting
    # a real DB. An empty dataframe takes the "0 jobs" placeholder branch.
    monkeypatch.setattr(db_module, "to_dataframe", lambda: pd.DataFrame())

    client = TestClient(web_app.app)
    r = client.post(
        "/actions/status/abc123",
        data={"status": "closed", "closed_reason": "ghosted"},
    )
    assert r.status_code == 200
    assert len(set_status_calls) == 1
    h, status, args, _ = set_status_calls[0]
    assert h == "abc123"
    assert status == "closed"
    # `closed_reason` is the second positional arg to db.set_status
    assert args == ("ghosted",)


# ---------------------------------------------------------------------------
# 27. /actions/status returns an HTMX OOB badges fragment so the sidebar
#     refreshes instantly without polling.
# ---------------------------------------------------------------------------
def test_action_status_returns_oob_badges_fragment(monkeypatch):
    """The 5s polling loop on the badges container was wasteful — most
    ticks find nothing changed. The fix: every mutation that affects
    badge counts appends an HTMX out-of-band swap fragment to its
    response so the sidebar updates in the same round trip.

    This test pins the contract: hitting /actions/status returns HTML
    containing both ``id="badges"`` and ``hx-swap-oob="true"`` so HTMX
    swaps the sidebar element automatically.
    """
    from fastapi.testclient import TestClient

    from src import db as db_module
    from web import app as web_app

    monkeypatch.setattr(db_module, "get_job", lambda h: {"hash": h, "status": "applied"})
    monkeypatch.setattr(db_module, "set_status", lambda *a, **kw: None)
    monkeypatch.setattr(db_module, "to_dataframe", lambda: pd.DataFrame())

    client = TestClient(web_app.app)
    r = client.post("/actions/status/abc123", data={"status": "shortlisted"})
    assert r.status_code == 200
    body = r.text
    assert 'id="badges"' in body, "OOB fragment must target the #badges element"
    assert 'hx-swap-oob="true"' in body, "OOB fragment must declare hx-swap-oob"


# ---------------------------------------------------------------------------
# 13. Retry-feedback handles HR-low even when ATS is fine
# ---------------------------------------------------------------------------
def test_retry_feedback_fires_for_low_hr_alone():
    """Bug we just shipped: retry only triggered when ATS was low. A resume
    with great keyword match but weak content (low HR score, e.g. bullets
    without metrics) silently shipped without a second attempt. Now retry
    fires when EITHER ATS or HR is below threshold."""
    from src.resume.pipeline import (
        ATS_RETRY_THRESHOLD,
        HR_RETRY_THRESHOLD,
        _build_retry_feedback,
    )

    # ATS is fine (above threshold), HR is low — feedback should focus on HR.
    fb = _build_retry_feedback(
        prev_ats=ATS_RETRY_THRESHOLD + 5,  # 85 — above threshold
        prev_hr=HR_RETRY_THRESHOLD - 10,  # 70 — below threshold
        missing={},
        weakest_areas=["bullet specificity", "JD-priority alignment"],
    )
    assert "HR perspective score was 70" in fb
    assert "weak areas: bullet specificity" in fb
    # ATS section must NOT appear since ATS was fine
    assert "ATS keyword match was" not in fb


def test_retry_feedback_combines_ats_and_hr_when_both_low():
    """When both signals fail, retry feedback covers both dimensions."""
    from src.resume.pipeline import _build_retry_feedback

    fb = _build_retry_feedback(
        prev_ats=55,
        prev_hr=68,
        missing={"required": ["WebAuthn", "Passkeys"], "preferred": [], "soft": []},
        weakest_areas=["readability"],
    )
    assert "ATS keyword match was 55%" in fb
    assert "HR perspective score was 68" in fb
    assert "WebAuthn" in fb
    assert "readability" in fb


# ---------------------------------------------------------------------------
# 14. Combined-score keep logic — retry doesn't trade HR for ATS
# ---------------------------------------------------------------------------
def test_combined_score_picks_balanced_winner():
    """Bug shape: previous logic kept the retry whenever ATS improved, even
    if HR collapsed. e.g., attempt 1 = (ATS 70, HR 90) vs attempt 2 =
    (ATS 85, HR 60) — the old keep_retry returned True (ATS improved).
    The combined metric correctly keeps attempt 1 (sum 160 > 145)."""
    from src.resume.pipeline import _combined_score

    a1 = {"match_pct": 70}
    h1 = {"hr_score": 90}
    a2 = {"match_pct": 85}
    h2 = {"hr_score": 60}
    # attempt 1: 70 + 90 = 160. attempt 2: 85 + 60 = 145. Keep attempt 1.
    assert _combined_score(a1, h1) > _combined_score(a2, h2)

    # Symmetric case: attempt 2 strictly dominates → it wins.
    assert _combined_score({"match_pct": 95}, {"hr_score": 92}) > _combined_score(
        {"match_pct": 85}, {"hr_score": 88}
    )


# ---------------------------------------------------------------------------
# 15. Domain-qualifier flexing rule is in the prompt
# ---------------------------------------------------------------------------
def test_prompt_allows_domain_qualifier_flexing():
    """The bullet pool ships frontend-specific phrasing for Equifax (e.g.
    'Led front-end architecture and program strategy'). For a generic EM
    JD that's NOT specifically frontend, the prompt must tell the LLM to
    substitute the domain qualifier (e.g., 'Led engineering architecture
    and program strategy'). Without this rule, EM resumes for non-frontend
    roles read as if Dheeraj only does frontend work."""
    from src.resume.prompts import SYSTEM_PROMPT

    assert "Domain qualifiers ARE flexible" in SYSTEM_PROMPT
    # The rule must explicitly call out the front-end -> engineering swap
    # for generic EM JDs (the exact case the user surfaced).
    assert "front-end" in SYSTEM_PROMPT.lower() or "frontend" in SYSTEM_PROMPT.lower()
    assert "Numbers, named tools" in SYSTEM_PROMPT
    assert "are NEVER substituted" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 16. Cover letter end-to-end render — IC track (locked IC background +
#     IC-framed bullets, no people-leadership phrasings)
# ---------------------------------------------------------------------------
def test_cover_letter_ic_track_renders_ic_template(monkeypatch, tmp_path):
    """IC titles (Staff Frontend Engineer, Tech Lead, etc.) used to raise
    ValueError. The IC pipeline must now:
      - skip frame_check (IC titles are always IC-framed)
      - render the locked IC background paragraph verbatim
      - render IC-framed bullets ("I designed and shipped" not "my team")
      - report track="ic" + frame="ic" so the caller can introspect
    """
    import json as _json

    import pytest

    from src.cover_letter import pipeline as cl_pipeline
    from src.cover_letter.pipeline import generate_cover_letter
    from src.resume import profile as resume_profile

    fake_payload = {
        "hiring_manager_name": "",
        "opening_hook": "I'm applying for the Staff Frontend Engineer role you posted on Greenhouse.",
        "company_hook": "",
        "bullets": [
            {"signal": "platform_devex"},
            {"signal": "end_to_end"},
            {"signal": "performance"},
        ],
        "company_fit_line": "",
    }
    monkeypatch.setattr(cl_pipeline, "chat", lambda *a, **k: _json.dumps(fake_payload))
    monkeypatch.setattr(cl_pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cl_pipeline, "mirror_to_public", lambda p: None)

    # JD body deliberately IC-heavy — would have hit the ic_dominated guard
    # on EM track. IC track must NOT run frame_check.
    ic_jd = (
        "Staff Frontend Engineer — hands-on individual contributor. Deep "
        "technical depth on system design, architecture, code review, "
        "on-call rotations, RFCs, and load-bearing production code."
    )

    path, report = generate_cover_letter(
        jd_title="Staff Frontend Engineer",
        jd_company="Vercel",
        jd_text=ic_jd,
    )

    # Did NOT raise the IC-dominated ValueError that fires on EM track.
    assert path.exists() and path.suffix == ".docx"
    assert report["track"] == "ic"
    assert report["frame"] == "ic"

    text = report["plain_text"]
    # The locked IC background paragraph must render VERBATIM.
    assert resume_profile.COVER_LETTER_BACKGROUND_IC in text
    # And the EM backgrounds must NOT appear.
    assert resume_profile.COVER_LETTER_BACKGROUND_STANDARD not in text
    assert resume_profile.COVER_LETTER_BACKGROUND_HYBRID not in text

    # Bullets must come from the IC table, not the EM table — verify by
    # asserting the IC bullet text shows up for every signal the LLM picked.
    ic_table = resume_profile.COVER_LETTER_BULLETS_BY_SIGNAL_IC
    em_table = resume_profile.COVER_LETTER_BULLETS_BY_SIGNAL
    for key in ("platform_devex", "end_to_end", "performance"):
        assert ic_table[key]["bullet"] in text, f"IC bullet for {key} must render"
        # IC bullets are written differently from EM bullets — confirm we
        # didn't accidentally render the EM variant.
        if ic_table[key]["bullet"] != em_table[key]["bullet"]:
            assert em_table[key]["bullet"] not in text

    # IC framing rule: no people-leadership phrasings should appear in the
    # IC background. (The bullets were authored to honor this too.)
    background = resume_profile.COVER_LETTER_BACKGROUND_IC
    for forbidden in ("my team", "engineers I led", "team I managed"):
        assert forbidden.lower() not in background.lower(), (
            f"IC background must not contain people-leadership phrasing: {forbidden!r}"
        )

    # Sanity: greeting + signoff + closing all still present.
    assert text.lstrip().startswith("Dear Hiring Manager,")
    assert resume_profile.COVER_LETTER_SIGNOFF in text
    assert "Dheeraj Sampath" in text

    # The old EM-only refusal must NOT raise anymore.
    # (Re-asserting via a second call with a different IC title for clarity.)
    monkeypatch.setattr(cl_pipeline, "chat", lambda *a, **k: _json.dumps(fake_payload))
    try:
        generate_cover_letter(
            jd_title="Senior Frontend Engineer",
            jd_company="Linear",
            jd_text=ic_jd,
        )
    except ValueError as e:
        if "EM-track" in str(e):
            pytest.fail("IC track must no longer raise the EM-only ValueError")


# ---------------------------------------------------------------------------
# 17. Cover letter end-to-end render — standard frame, locked text, bullet table
# ---------------------------------------------------------------------------
def test_cover_letter_render_assembles_locked_template(monkeypatch, tmp_path):
    """Stubbed Sonnet response, end-to-end render. Verifies:
    - frame check returns 'standard' for a people-leadership-dominant JD
    - the locked STANDARD background paragraph renders (not hybrid)
    - the three bullets the LLM selected (by signal key) render in order
    - greeting falls back to 'Dear Hiring Manager,' when no name given
    - `company_hook` and `company_fit_line` are omitted when empty
    - subject line includes target title and company
    - report contains frame + bullet_picks for caller introspection
    """
    import json as _json

    from src.cover_letter import pipeline as cl_pipeline
    from src.cover_letter.pipeline import generate_cover_letter
    from src.resume import profile as resume_profile

    fake_payload = {
        "hiring_manager_name": "",
        "opening_hook": "I'm applying for the Engineering Manager, Identity Frontend role you posted on Greenhouse.",
        "company_hook": "",
        "bullets": [
            {"signal": "platform_devex"},
            {"signal": "team_scaling"},
            {"signal": "mentoring"},
        ],
        "company_fit_line": "",
    }

    def fake_chat(*args, **kwargs):
        return _json.dumps(fake_payload)

    monkeypatch.setattr(cl_pipeline, "chat", fake_chat)
    monkeypatch.setattr(cl_pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cl_pipeline, "mirror_to_public", lambda p: None)

    # JD body laden with people-leadership signals so frame_check returns "standard"
    people_heavy_jd = (
        "We're hiring an Engineering Manager who will lead a growing team of "
        "frontend engineers. You'll own hiring, performance management, "
        "1:1s, growth plans, headcount, and roadmap ownership. You'll partner "
        "with PM and design. Stakeholder communication and mentoring are core."
    )

    path, report = generate_cover_letter(
        jd_title="Engineering Manager, Identity Frontend",
        jd_company="Coinbase",
        jd_text=people_heavy_jd,
    )

    assert path.exists() and path.suffix == ".docx"
    assert "Engineering_Manager_Identity_Frontend_Coinbase" in path.name

    # Report shape
    assert report["frame"] == "standard"
    assert [p["signal"] for p in report["bullet_picks"]] == [
        "platform_devex",
        "team_scaling",
        "mentoring",
    ]
    assert report["company_hook_used"] is False
    assert report["company_fit_line_used"] is False

    text = report["plain_text"]
    # Bug pinned: an early version added a "Re: <title> at <company>" subject
    # line that wasn't in the master template. The greeting must be the
    # first non-header content.
    assert "Re:" not in text
    assert text.lstrip().startswith("Dear Hiring Manager,")
    # Standard background, NOT hybrid
    assert resume_profile.COVER_LETTER_BACKGROUND_STANDARD in text
    assert resume_profile.COVER_LETTER_BACKGROUND_HYBRID not in text
    assert resume_profile.COVER_LETTER_BULLETS_LEAD in text
    assert resume_profile.COVER_LETTER_SIGNOFF in text
    assert "Dheeraj Sampath" in text
    # All three picked-signal bullets must surface in order
    for key in ("platform_devex", "team_scaling", "mentoring"):
        bullet_text = resume_profile.COVER_LETTER_BULLETS_BY_SIGNAL[key]["bullet"]
        assert bullet_text in text
    # Background appears AFTER opening hook in the rendered order
    assert text.index("I'm applying for the Engineering Manager") < text.index(
        resume_profile.COVER_LETTER_BACKGROUND_STANDARD
    )


# ---------------------------------------------------------------------------
# 18. Frame check — people / hybrid / ic_dominated bucketing
# ---------------------------------------------------------------------------
def test_frame_check_classifies_people_hybrid_ic():
    """The frame_check function counts people-leadership phrases vs IC
    technical-depth phrases per the skill rule:
      people >= 2*ic   → standard
      ic >= 2*people   → ic_dominated
      otherwise        → hybrid
    Bug shape: misclassifying a balanced JD as 'standard' would render the
    standard people-manager opening for a player-coach role and undersell
    the IC depth — the user surfaced this exact pitfall."""
    from src.cover_letter.pipeline import frame_check

    # People-leadership-dominant — should be standard
    people_heavy = (
        "Lead a team of engineers. Own hiring, performance management, "
        "1:1s, headcount planning, growth plans, mentoring, and "
        "stakeholder communication."
    )
    assert frame_check(people_heavy) == "standard"

    # IC-dominant — should refuse
    ic_heavy = (
        "Hands-on individual contributor. Deep technical work on system "
        "design, architecture reviews, code review, on-call rotations, "
        "RFCs, and load-bearing production code. Specific framework "
        "expertise required."
    )
    assert frame_check(ic_heavy) == "ic_dominated"

    # Balanced — should be hybrid
    balanced = (
        "Hands-on engineering manager. You'll do code review, lead "
        "architecture decisions, mentor engineers, and own the team "
        "roadmap. Stakeholder partnership and on-call rotations both "
        "expected."
    )
    assert frame_check(balanced) == "hybrid"


# ---------------------------------------------------------------------------
# 19. Cover letter refuses IC-dominated JDs even on EM-track titles
# ---------------------------------------------------------------------------
def test_cover_letter_refuses_ic_dominated_jd_body():
    """An EM-titled JD whose body is IC-dominated should be flagged back per
    the skill rule. Bug shape: the title check alone passed JDs through,
    leading to EM-framed letters for what were really IC roles in disguise."""
    import pytest

    from src.cover_letter import generate_cover_letter

    ic_dominated_jd = (
        "Senior Engineering Manager - hands-on. Deep technical depth in "
        "system design, architecture, code review, on-call. Load-bearing "
        "production code. Individual contributor work on RFCs and "
        "specific framework expertise."
    )
    with pytest.raises(ValueError, match="IC-dominated"):
        generate_cover_letter(
            jd_title="Senior Engineering Manager",
            jd_company="SomeCo",
            jd_text=ic_dominated_jd,
        )


# ---------------------------------------------------------------------------
# 20. Hybrid frame swaps the background paragraph
# ---------------------------------------------------------------------------
def test_cover_letter_hybrid_frame_uses_hybrid_background(monkeypatch, tmp_path):
    """Balanced JDs get the 40%-code/60%-leading opening. The renderer must
    swap STANDARD for HYBRID when frame == 'hybrid'."""
    import json as _json

    from src.cover_letter import pipeline as cl_pipeline
    from src.cover_letter.pipeline import generate_cover_letter
    from src.resume import profile as resume_profile

    fake_payload = {
        "hiring_manager_name": "",
        "opening_hook": "I'm applying for the Staff Engineering Manager role.",
        "company_hook": "",
        "bullets": [
            {"signal": "end_to_end"},
            {"signal": "platform_devex"},
            {"signal": "mentoring"},
        ],
        "company_fit_line": "",
    }
    monkeypatch.setattr(cl_pipeline, "chat", lambda *a, **k: _json.dumps(fake_payload))
    monkeypatch.setattr(cl_pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cl_pipeline, "mirror_to_public", lambda p: None)

    balanced_jd = (
        "Hands-on engineering manager. You'll do code review, lead "
        "architecture decisions, mentor engineers, and own the team "
        "roadmap. On-call rotations and stakeholder partnership both "
        "expected."
    )
    _, report = generate_cover_letter(
        jd_title="Staff Engineering Manager",
        jd_company="SomeCo",
        jd_text=balanced_jd,
    )
    assert report["frame"] == "hybrid"
    text = report["plain_text"]
    assert resume_profile.COVER_LETTER_BACKGROUND_HYBRID in text
    assert resume_profile.COVER_LETTER_BACKGROUND_STANDARD not in text


# ---------------------------------------------------------------------------
# 21. Bullet validator drops unknown signals + dedupes + backfills to 3
# ---------------------------------------------------------------------------
def test_cover_letter_bullets_validator_anchors_against_fabrication():
    """The skill says 'do not invent bullets — pick from the table'. The
    pipeline's signal-key validator must:
      - drop unknown keys
      - drop duplicates
      - backfill to exactly 3 bullets so the renderer always has content
    """
    from src.cover_letter.pipeline import _bullets_from_llm

    # LLM drift: one valid, one duplicate, one unknown — should still get 3
    picks = _bullets_from_llm(
        [
            {"signal": "platform_devex"},
            {"signal": "platform_devex"},  # duplicate — drop
            {"signal": "made_this_up"},  # unknown — drop
        ]
    )
    assert len(picks) == 3
    signals = [p.signal for p in picks]
    assert "platform_devex" in signals
    assert "made_this_up" not in signals
    assert len(set(signals)) == 3, "all three picks must be different signals"


# ---------------------------------------------------------------------------
# 22. refine_resume keeps prior when the new attempt scores worse
# ---------------------------------------------------------------------------
def test_refine_resume_keeps_prior_when_new_attempt_is_worse(monkeypatch, tmp_path):
    """Bug shape: the in-pipeline retry sometimes regresses (HR fix drops
    ATS keywords). The user's manual 'Refine' button must use the same
    combined-score gate, so clicking it can NEVER make a working resume
    worse — it either improves or it's a no-op."""
    import json as _json

    from src.resume import pipeline as r_pipeline
    from src.resume.pipeline import expected_resume_path, refine_resume

    # Set up a prior file pair: docx + sidecar with strong scores
    monkeypatch.setattr(r_pipeline, "OUTPUT_DIR", tmp_path)
    docx_path = expected_resume_path("EM Test", "AcmeCo", "Remote")
    docx_path.write_bytes(b"placeholder docx bytes")  # docx_builder won't be called for prior
    prior_scores = {
        "ats_match": {
            "match_pct": 85,
            "missing": {"required": ["Kafka"], "preferred": [], "soft": []},
            "matched": {},
        },
        "hr": {"hr_score": 88, "rationale": "ok", "weakest_areas": ["something"]},
        "keywords": {"required": ["React"], "preferred": [], "soft": []},
        "track": "em",
        "attempts": [{"ats_pct": 85, "hr_score": 88, "kept": True}],
    }
    docx_path.with_suffix(".scores.json").write_text(_json.dumps(prior_scores))

    # Stub LLM to return a payload that scores WORSE than prior
    fake_payload = {
        "summary": "Engineering Manager with 15+ years.",
        "highlights": [],
        "experience": [],
        "skills": [],
        "conditional_cert": "cua",
        "tailoring_report": {},
    }
    monkeypatch.setattr(r_pipeline, "chat", lambda *a, **k: _json.dumps(fake_payload))
    monkeypatch.setattr(
        r_pipeline,
        "match_keywords",
        lambda *a, **k: {
            "match_pct": 60,
            "matched": {},
            "missing": {"required": [], "preferred": [], "soft": []},
        },
    )
    monkeypatch.setattr(
        r_pipeline,
        "hr_simulate",
        lambda *a, **k: {"hr_score": 70, "rationale": "weaker", "weakest_areas": []},
    )
    monkeypatch.setattr(r_pipeline, "build_docx", lambda resume, path: None)
    monkeypatch.setattr(r_pipeline, "mirror_to_public", lambda p: None)
    monkeypatch.setattr(r_pipeline, "extract_keywords", lambda *a, **k: prior_scores["keywords"])

    path, report = refine_resume(
        jd_title="EM Test",
        jd_company="AcmeCo",
        jd_text="some JD",
        location="Remote",
    )
    # Prior combined = 85+88=173; new combined = 60+70=130. Refinement loses.
    assert report["refinement_kept"] is False, "should NOT keep a worse attempt"

    # Sidecar was rewritten with the refinement record but kept-flag stays on prior
    sidecar = _json.loads(docx_path.with_suffix(".scores.json").read_text())
    refinement_records = [a for a in sidecar["attempts"] if a.get("refinement")]
    assert len(refinement_records) == 1
    assert refinement_records[0]["kept"] is False
    # The original prior attempt should still be kept=True
    non_refinement = [a for a in sidecar["attempts"] if not a.get("refinement")]
    assert any(a["kept"] for a in non_refinement)


def test_refine_resume_overwrites_when_new_attempt_is_better(monkeypatch, tmp_path):
    """Mirror of the previous test — refinement DOES overwrite when combined
    score improves. Verifies the .docx gets rewritten."""
    import json as _json

    from src.resume import pipeline as r_pipeline
    from src.resume.pipeline import expected_resume_path, refine_resume

    monkeypatch.setattr(r_pipeline, "OUTPUT_DIR", tmp_path)
    docx_path = expected_resume_path("EM Test", "AcmeCo", "Remote")
    docx_path.write_bytes(b"placeholder old bytes")
    prior_scores = {
        "ats_match": {
            "match_pct": 60,
            "missing": {"required": ["Kafka"], "preferred": [], "soft": []},
            "matched": {},
        },
        "hr": {"hr_score": 65, "rationale": "weak", "weakest_areas": ["specificity"]},
        "keywords": {"required": ["React"], "preferred": [], "soft": []},
        "track": "em",
        "attempts": [{"ats_pct": 60, "hr_score": 65, "kept": True}],
    }
    docx_path.with_suffix(".scores.json").write_text(_json.dumps(prior_scores))

    fake_payload = {
        "summary": "...",
        "highlights": [],
        "experience": [],
        "skills": [],
        "conditional_cert": "cua",
        "tailoring_report": {},
    }
    monkeypatch.setattr(r_pipeline, "chat", lambda *a, **k: _json.dumps(fake_payload))
    monkeypatch.setattr(
        r_pipeline,
        "match_keywords",
        lambda *a, **k: {
            "match_pct": 90,
            "matched": {},
            "missing": {"required": [], "preferred": [], "soft": []},
        },
    )
    monkeypatch.setattr(
        r_pipeline,
        "hr_simulate",
        lambda *a, **k: {"hr_score": 88, "rationale": "stronger", "weakest_areas": []},
    )
    docx_writes: list[bytes] = []
    monkeypatch.setattr(
        r_pipeline,
        "build_docx",
        lambda resume, path: docx_writes.append(b"new bytes"),
    )
    monkeypatch.setattr(r_pipeline, "mirror_to_public", lambda p: None)
    monkeypatch.setattr(r_pipeline, "extract_keywords", lambda *a, **k: prior_scores["keywords"])

    path, report = refine_resume(
        jd_title="EM Test",
        jd_company="AcmeCo",
        jd_text="some JD",
        location="Remote",
    )
    # Prior combined 60+65=125; new 90+88=178. Refinement wins.
    assert report["refinement_kept"] is True
    assert len(docx_writes) == 1, "build_docx should have been called once"

    sidecar = _json.loads(docx_path.with_suffix(".scores.json").read_text())
    refinement = [a for a in sidecar["attempts"] if a.get("refinement")][0]
    assert refinement["kept"] is True
    assert refinement["ats_pct"] == 90
    assert refinement["hr_score"] == 88


# ---------------------------------------------------------------------------
# 24. /actions/refine-resume endpoint refuses when nothing to refine
# ---------------------------------------------------------------------------
def test_refine_resume_endpoint_refuses_when_no_prior_resume(monkeypatch):
    """The 'Refine with feedback' button is only meaningful when a resume
    + sidecar already exist on disk. The endpoint must return 409 (not 500
    or silent submission) when the user's hash has no prior resume."""
    from fastapi.testclient import TestClient

    from src import db as db_module
    from web import app as web_app

    monkeypatch.setattr(
        db_module,
        "get_job",
        lambda h: {
            "hash": h,
            "title": "EM Z",
            "company": "Nowhere",
            "location": "",
            "description": "x",
        },
    )
    # Patch the resume-path helper so it returns a path that DOESN'T exist
    nonexistent = web_app.expected_resume_path("EM Z", "Nowhere", "")
    assert not nonexistent.exists(), "test setup expects no prior resume"

    client = TestClient(web_app.app)
    r = client.post("/actions/refine-resume/abc123")
    assert r.status_code == 409
    assert "no existing resume" in r.text.lower()


# ---------------------------------------------------------------------------
# 25. Generations queue records job_hash so sidebar rows can deep-link
# ---------------------------------------------------------------------------
def test_submit_generation_records_job_hash(monkeypatch):
    """The sidebar generations tray needs each row to carry the originating
    job_hash so clicking a row jumps to that job's detail panel. Bug shape:
    queue rows had only title+company, requiring the user to manually find
    the row in the grid to open the detail pane."""
    from src import state

    # Stub the executor + the underlying generators so no real work runs
    captured = {}

    class _FakeFuture:
        def done(self):
            return True

        def exception(self):
            return None

        def add_done_callback(self, cb):
            cb(self)  # done synchronously — fire the cleanup callback

    def _fake_submit(fn, *args, **kwargs):
        captured["called_with"] = (fn.__name__, args, kwargs)
        return _FakeFuture()

    class _FakeExec:
        def submit(self, fn, *args, **kwargs):
            return _fake_submit(fn, *args, **kwargs)

    monkeypatch.setattr(state, "get_executor", lambda: _FakeExec())

    # Reset the in-memory log so this test sees only its own entry
    with state._GENERATIONS_LOCK:
        state._GENERATIONS.clear()

    state.submit_generation(
        "resume",
        "Engineering Manager",
        "Coinbase",
        "JD body",
        location="Remote - USA",
        job_hash="abc123def456",
    )

    snapshot = state.get_generations()
    assert len(snapshot) == 1
    record = snapshot[0]
    assert record["kind"] == "resume"
    assert record["job_hash"] == "abc123def456", (
        "submit_generation must persist job_hash on the record so the sidebar "
        "tray can open the detail panel for that job on click"
    )
    assert record["title"] == "Engineering Manager"
    assert record["company"] == "Coinbase"


# ---------------------------------------------------------------------------
# 26. Cover letter autogen now generates for IC-track titles too
# ---------------------------------------------------------------------------
def test_autogen_cover_letter_generates_for_ic_track(monkeypatch, tmp_path):
    """Used to early-return None for IC titles. With the IC template wired
    up, the autogen helper must run the IC pipeline and produce a .docx
    so Staff Frontend Engineer / Tech Lead postings get a cover letter
    paired with their resume in the auto-flow."""
    import json as _json

    from src.cover_letter import pipeline as cl_pipeline
    from src.cover_letter.pipeline import autogen_cover_letter_if_missing

    fake_payload = {
        "hiring_manager_name": "",
        "opening_hook": "I'm applying for the Staff Frontend Engineer role.",
        "company_hook": "",
        "bullets": [
            {"signal": "platform_devex"},
            {"signal": "end_to_end"},
            {"signal": "performance"},
        ],
        "company_fit_line": "",
    }
    monkeypatch.setattr(cl_pipeline, "chat", lambda *a, **k: _json.dumps(fake_payload))
    monkeypatch.setattr(cl_pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cl_pipeline, "mirror_to_public", lambda p: None)

    result = autogen_cover_letter_if_missing(
        jd_title="Staff Frontend Engineer",
        jd_company="Vercel",
        jd_text="hands-on staff IC role, deep system design, on-call",
    )
    assert result is not None, "IC-track jobs must now produce a cover letter"
    assert result.exists() and result.suffix == ".docx"


# ---------------------------------------------------------------------------
# 27. Bundle download zips both artifacts and 404s when one is missing
# ---------------------------------------------------------------------------
def test_bundle_download_streams_zip_with_both_artifacts(monkeypatch, tmp_path):
    """The 'Download both' button hits /files/bundle/{hash}. Server must
    return a ZIP containing both files when they exist, and 404 with a
    clear message when one is missing — so the user knows to generate the
    missing one rather than getting an empty / corrupt zip."""
    import io
    import zipfile

    from fastapi.testclient import TestClient

    from src import db as db_module
    from web import app as web_app

    job = {
        "hash": "abc123",
        "title": "Engineering Manager",
        "company": "Coinbase",
        "location": "Remote",
        "description": "x",
        "status": "applying",
    }
    monkeypatch.setattr(db_module, "get_job", lambda h: job)

    # Create real placeholder files at the canonical paths
    resume_path = web_app.expected_resume_path(job["title"], job["company"], job["location"])
    cover_path = web_app.expected_cover_letter_path(job["title"], job["company"])
    resume_path.parent.mkdir(parents=True, exist_ok=True)
    resume_path.write_bytes(b"resume bytes")
    cover_path.write_bytes(b"cover bytes")

    try:
        client = TestClient(web_app.app)
        r = client.get("/files/bundle/abc123")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert "_bundle.zip" in r.headers["content-disposition"]
        # Verify the zip really contains both files
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = set(zf.namelist())
        assert resume_path.name in names
        assert cover_path.name in names

        # Now delete the cover letter and verify 404 with named missing file
        cover_path.unlink()
        r = client.get("/files/bundle/abc123")
        assert r.status_code == 404
        assert "cover letter" in r.text.lower()
    finally:
        resume_path.unlink(missing_ok=True)
        cover_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 28. Failed generations clear the pending marker (no infinite "Generating…")
# ---------------------------------------------------------------------------
def test_failed_generation_clears_pending_marker(monkeypatch):
    """Bug user surfaced: dashboard clicked Generate, executor caught an
    exception, but the artifacts panel polled "Generating resume…" forever
    because the only marker-cleanup path was 'file appears on disk'. Add a
    done-callback so the marker resolves regardless of outcome and the
    user-visible polling can move on (and the failure logs loud)."""
    import time

    from src import state

    # Stub the executor so we control the future
    captured_callback = {}

    class _FakeFuture:
        def __init__(self, err):
            self._err = err
            self._cbs = []

        def done(self):
            return True

        def exception(self):
            return self._err

        def add_done_callback(self, cb):
            captured_callback["cb"] = cb
            self._cbs.append(cb)
            cb(self)  # synchronous fire — already done

    err = RuntimeError("simulated openrouter blip")

    class _FakeExec:
        def submit(self, fn, *args, **kwargs):
            return _FakeFuture(err)

    monkeypatch.setattr(state, "get_executor", lambda: _FakeExec())

    state.mark_pending("abc123", "resume")
    assert state.pending_started_at("abc123", "resume") is not None

    state.submit_generation(
        "resume",
        "EM",
        "TestCo",
        "jd",
        location="Remote",
        job_hash="abc123",
    )

    # The done callback should have fired synchronously and cleared the marker
    assert state.pending_started_at("abc123", "resume") is None, (
        "pending marker must clear when the future resolves, even on failure"
    )

    # And refine's marker uses the resume key (refine writes the resume),
    # so a failed refine should clean up the resume pending marker too.
    state.mark_pending("xyz789", "resume")
    state.submit_generation(
        "refine",
        "EM",
        "TestCo",
        "jd",
        location="Remote",
        job_hash="xyz789",
    )
    assert state.pending_started_at("xyz789", "resume") is None


# ────────────────────────────────────────────────────────────────────────────
# Prefilter title matching — pins the EM + IC + Architect track scope.
#
# Bug surfaced 2026-04-30: the original ruleset was EM-only, so the user's
# entire IC track ("Staff Software Engineer, Frontend Engineering" etc.)
# was getting prefilter-rejected before reaching the scorer. 8,678 jobs
# silently fell through "no title match and weak signals". This pins the
# expanded scope so it can't regress.
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "title",
    [
        # EM track
        "Engineering Manager",
        "Engineering Manager, Frontend Platform",
        "Senior Engineering Manager",
        "Director of Engineering",
        "Director, Engineering",
        "Head of Engineering",
        "VP of Engineering",
        "Senior Manager, Software Engineering",
        "Frontend Engineering Lead",
        "Tech Lead Manager",
        # IC: Staff / Principal
        "Staff Software Engineer, Frontend Engineering",
        "Staff Frontend Engineer",
        "Staff Product Engineer",
        "Staff Full-Stack Engineer",
        "Senior Staff Frontend Engineer",
        "Principal Frontend Engineer",
        "Principal Software Engineer",
        "Principal Web Platform Engineer",
        # IC: Senior
        "Senior Frontend Engineer",
        "Senior Front-end Engineer",
        "Senior Full-Stack Engineer",
        "Senior Product Engineer",
        # Plain Full-Stack (any seniority — user explicitly allowed)
        "Full Stack Engineer",
        "Full-Stack Engineer",
        # AI / Forward Deployed (user explicitly allowed)
        "AI Engineer",
        "Senior AI Engineer",
        "Forward Deployed Engineer",
        "Staff Forward-Deployed Engineer",
        # Architect track (user explicitly allowed)
        "Tech Architect",
        "Technical Architect",
        "Web Architect",
        "React Architect",
        "Frontend Architect",
        "Front-End Architect",
        "Senior Frontend Architect",
        "Lead Web Architect",
        # Tech Lead variants
        "Frontend Tech Lead",
        "Tech Lead, Frontend",
        "Software Engineer Tech Lead",
        "Web Platform Engineer",
    ],
)
def test_prefilter_title_good_accepts_em_ic_and_architect_titles(title):
    from src.enrichment.prefilter import TITLE_GOOD

    assert TITLE_GOOD.search(title), f"TITLE_GOOD should match {title!r}"


@pytest.mark.parametrize(
    "title",
    [
        # Out-of-scope IC junior/mid
        "Junior Frontend Developer",
        "Software Engineer Intern",
        # Adjacent-but-different roles
        "Recruiter",
        "Sales Engineer",
        "Solutions Engineer",
        "Customer Engineer",
        "Product Designer",
        "UX Designer",
        "Data Scientist",
        "ML Engineer",
        "Machine Learning Engineer",
        "QA Engineer",
        "Test Engineer",
        "Site Reliability Engineer",
        "DevOps Engineer",
        "Security Engineer",
        "Embedded Engineer",
        "Firmware Engineer",
        "Technical Writer",
        "Product Manager",
        "Program Manager",
        "Project Manager",
        "TPM",
    ],
)
def test_prefilter_title_bad_rejects_out_of_scope_titles(title):
    from src.enrichment.prefilter import TITLE_BAD, prefilter

    # Either TITLE_BAD catches it, or end-to-end prefilter drops it.
    if TITLE_BAD.search(title):
        return
    passed, _reason, _sp = prefilter(title, description="")
    assert not passed, f"prefilter should drop {title!r}"


# ────────────────────────────────────────────────────────────────────────────
# Claude-for-Chrome prompt — pins queue ordering + structural sections.
#
# Bug class this prevents: the prompt is the contract between the dashboard
# and the Claude-for-Chrome session. If the queue order silently changes
# (e.g., `closed` jobs leak in, `applying` jobs sort before `shortlisted`,
# tier priority gets reversed), Dheeraj wastes time on stale or wrong-track
# applications. The prompt rewrite (2026-04-30) baked in: queue scope =
# shortlisted ∪ new ∪ applying; sort = tier → status → score; batch of 5;
# sponsorship-denied → mark `no_sponsorship`. These tests pin those.
# ────────────────────────────────────────────────────────────────────────────
def test_claude_queue_orders_by_tier_then_status_then_score():
    from web.app import _build_claude_queue

    df = pd.DataFrame(
        [
            # (title, status, tier, score) — names the row's expected position
            {
                "title": "lower-tier-shortlisted",
                "company": "C1",
                "status": "shortlisted",
                "tier": "possible",
                "score_total": 95.0,
                "scraped_at": "2026-04-29",
                "hash": "h1",
                "url": "u",
                "location": "",
            },
            {
                "title": "strong-applying-low",
                "company": "C2",
                "status": "applying",
                "tier": "strong",
                "score_total": 70.0,
                "scraped_at": "2026-04-29",
                "hash": "h2",
                "url": "u",
                "location": "",
            },
            {
                "title": "strong-shortlisted-mid",
                "company": "C3",
                "status": "shortlisted",
                "tier": "strong",
                "score_total": 80.0,
                "scraped_at": "2026-04-29",
                "hash": "h3",
                "url": "u",
                "location": "",
            },
            {
                "title": "strong-new-high",
                "company": "C4",
                "status": "new",
                "tier": "strong",
                "score_total": 92.0,
                "scraped_at": "2026-04-29",
                "hash": "h4",
                "url": "u",
                "location": "",
            },
            {
                "title": "strong-shortlisted-high",
                "company": "C5",
                "status": "shortlisted",
                "tier": "strong",
                "score_total": 90.0,
                "scraped_at": "2026-04-29",
                "hash": "h5",
                "url": "u",
                "location": "",
            },
            # Terminal — must NOT appear in the queue
            {
                "title": "closed-high",
                "company": "C6",
                "status": "closed",
                "tier": "strong",
                "score_total": 99.0,
                "scraped_at": "2026-04-29",
                "hash": "h6",
                "url": "u",
                "location": "",
            },
            {
                "title": "not-interested-high",
                "company": "C7",
                "status": "not_interested",
                "tier": "strong",
                "score_total": 99.0,
                "scraped_at": "2026-04-29",
                "hash": "h7",
                "url": "u",
                "location": "",
            },
            {
                "title": "no-sponsorship-high",
                "company": "C8",
                "status": "no_sponsorship",
                "tier": "strong",
                "score_total": 99.0,
                "scraped_at": "2026-04-29",
                "hash": "h8",
                "url": "u",
                "location": "",
            },
        ]
    )
    # _build_claude_queue gates on prefilter_passed (matches dashboard grid);
    # every row in this fixture is a curated-state job we want surfaced.
    df["prefilter_passed"] = 1

    queue = _build_claude_queue(df)
    titles = [j["title"] for j in queue]

    # Terminal states excluded
    assert "closed-high" not in titles
    assert "not-interested-high" not in titles
    assert "no-sponsorship-high" not in titles

    # Order: tier=strong first, within that status=shortlisted before new
    # before applying, score desc within bucket; then tier=possible.
    assert titles == [
        "strong-shortlisted-high",  # strong + shortlisted, score 90
        "strong-shortlisted-mid",  # strong + shortlisted, score 80
        "strong-new-high",  # strong + new, score 92
        "strong-applying-low",  # strong + applying, score 70
        "lower-tier-shortlisted",  # possible + shortlisted (last)
    ], f"unexpected queue order: {titles}"


def test_claude_prompt_references_new_apis(monkeypatch):
    """The prompt is the user-facing contract for Claude-for-Chrome behavior.
    Post the API-first rewrite, the prompt teaches Claude to call JSON
    endpoints (not scrape the dashboard UI). Pin: required section headers,
    every API endpoint Claude is told about, batch size, sponsorship rule.
    Personal facts live in /api/job/<hash>.json `application_defaults` —
    NOT restated in the prompt body."""
    from fastapi.testclient import TestClient

    from src import db as db_module
    from web import app as web_app

    df = pd.DataFrame(
        [
            {
                "hash": "abc123",
                "source": "greenhouse",
                "company": "Acme",
                "title": "Engineering Manager, Frontend Platform",
                "location": "Remote - USA",
                "url": "https://acme.example/jobs/em-fp",
                "description": "x",
                "posted_at": "2026-04-29",
                "salary_min": None,
                "salary_max": None,
                "remote": 1,
                "sponsorship_status": "unknown",
                "prefilter_passed": 1,
                "prefilter_reason": "",
                "score_total": 92.0,
                "score_breakdown": "{}",
                "score_rationale": "",
                "tier": "strong",
                "status": "shortlisted",
                "status_at": None,
                "closed_reason": None,
                "applied_at": None,
                "notes": "",
                "scraped_at": "2026-04-29T00:00:00",
            }
        ]
    )
    monkeypatch.setattr(db_module, "to_dataframe", lambda: df)

    client = TestClient(web_app.app)
    r = client.get("/api/claude-prompt.txt")
    assert r.status_code == 200
    body = r.text

    # Section landmarks for the API-first prompt.
    for header in (
        "## API contract",
        "## Sponsorship rule",
        "## Tab discipline",
        "## Workflow",
        "## End-of-queue summary",
        "## Hard rules",
        "## Start now",
    ):
        assert header in body, f"missing section: {header}"

    # Batch size baked into the workflow narrative
    assert "BATCHES OF 5" in body.upper()

    # The four API endpoints Claude must know to call.
    assert "/api/queue.json" in body
    assert "/api/job/<hash>.json" in body
    assert "/api/applied/<hash>" in body
    assert "/api/no-sponsorship/<hash>" in body

    # The prompt points at application_defaults as the single source of
    # truth for personal facts — must NOT restate them in the prompt body
    # itself (drift risk; the JSON is the contract).
    assert "application_defaults" in body
    assert "cover_letter_content.why_this_company" in body

    # Sensitive personal facts must NOT leak into the prompt body — they
    # ride per-job in the JSON. The Gmail check is a regression guard:
    # turbowars@gmail.com is the user's personal address; only the
    # proton address goes on application forms (and even that's now
    # surfaced via JSON only).
    assert "turbowars@gmail.com" not in body

    # The prompt must instruct Claude to mark sponsorship-denied jobs as
    # No Sponsorship via the API — NOT silently skip them.
    assert "no-sponsorship" in body or "No Sponsorship" in body
    assert "REQUIRES sponsorship" in body  # the work_authorization stance is still in-prompt

    # Old UI-driven steps must NOT appear (the rewrite replaced them).
    assert "Click the top unprocessed row" not in body
    assert "filtered grid IS the queue" not in body


# ────────────────────────────────────────────────────────────────────────────
# New API-first endpoints + cover-letter sidecar.
#
# These pin the contract Claude-in-Chrome reads. The shape change cost was
# real: rewriting the prompt to hit JSON instead of scraping the dashboard
# is only safe if the JSON keeps its shape. Each test below corresponds to
# a regression we'd ship without it.
# ────────────────────────────────────────────────────────────────────────────
def _stub_one_job_df(monkeypatch, **overrides):
    """Helper: install a single-row DataFrame as db.to_dataframe()'s output.
    Returns the row dict so tests can check what they stubbed."""
    from src import db as db_module

    row = {
        "hash": "abc123",
        "source": "greenhouse",
        "company": "Acme",
        "title": "Engineering Manager, Frontend Platform",
        "location": "Remote - USA",
        "url": "https://acme.example/jobs/em-fp",
        "description": "We are hiring an EM. No mention of sponsorship.",
        "posted_at": "2026-04-29",
        "salary_min": None,
        "salary_max": None,
        "remote": 1,
        "sponsorship_status": "unknown",
        "prefilter_passed": 1,
        "prefilter_reason": "",
        "score_total": 92.0,
        "score_breakdown": "{}",
        "score_rationale": "",
        "tier": "strong",
        "status": "shortlisted",
        "status_at": None,
        "closed_reason": None,
        "applied_at": None,
        "notes": "",
        "scraped_at": "2026-04-29T00:00:00",
    }
    row.update(overrides)
    df = pd.DataFrame([row])
    monkeypatch.setattr(db_module, "to_dataframe", lambda: df)
    monkeypatch.setattr(db_module, "get_job", lambda h: row if h == row["hash"] else None)
    return row


def test_api_queue_json_orders_and_trims(monkeypatch):
    """The actionable queue endpoint reuses _build_claude_queue and trims
    to the JSON shape Claude needs. Pins the field set + the cap."""
    from fastapi.testclient import TestClient

    from src import db as db_module
    from web import app as web_app

    df = pd.DataFrame(
        [
            {
                "hash": "h1",
                "title": "t1",
                "company": "c1",
                "status": "shortlisted",
                "tier": "strong",
                "score_total": 90.0,
                "scraped_at": "2026-04-29",
                "url": "u1",
                "location": "",
            },
            {
                "hash": "h2",
                "title": "t2",
                "company": "c2",
                "status": "new",
                "tier": "strong",
                "score_total": 85.0,
                "scraped_at": "2026-04-29",
                "url": "u2",
                "location": "",
            },
            # Terminal — must NOT appear
            {
                "hash": "h3",
                "title": "t3",
                "company": "c3",
                "status": "closed",
                "tier": "strong",
                "score_total": 99.0,
                "scraped_at": "2026-04-29",
                "url": "u3",
                "location": "",
            },
        ]
    )
    df["prefilter_passed"] = 1  # gate matches dashboard grid
    monkeypatch.setattr(db_module, "to_dataframe", lambda: df)

    client = TestClient(web_app.app)
    r = client.get("/api/queue.json?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert {q["hash"] for q in body["queue"]} == {"h1", "h2"}, "terminal states leaked"
    assert body["queue"][0]["hash"] == "h1", "shortlisted should sort before new"
    # Pin the field set so a refactor that drops fields breaks this.
    first = body["queue"][0]
    assert set(first.keys()) == {"hash", "title", "company", "score", "tier", "status", "apply_url"}
    assert body["total"] == 2
    assert body["returned"] == 2

    # Limit clamping
    r = client.get("/api/queue.json?limit=1")
    assert len(r.json()["queue"]) == 1
    assert r.json()["total"] == 2  # full count stays accurate


def test_api_job_returns_full_bundle(monkeypatch, tmp_path):
    """The per-job endpoint bundles JD + artifacts + application_defaults
    + cover_letter_content. Pin every required key so a missed field
    breaks the test, not Claude-in-Chrome at runtime."""
    from fastapi.testclient import TestClient

    from src import utils as utils_module
    from src.cover_letter import expected_cover_letter_path, expected_cover_sidecar_path
    from src.resume import existing_resume_path
    from web import app as web_app

    # Install fake exports dir + write artifacts so existing_resume_path /
    # expected_cover_letter_path point at real files.
    monkeypatch.setattr(utils_module, "OUTPUT_DIR", tmp_path)
    # Re-import the resume + cover_letter modules' OUTPUT_DIR references
    # by patching the symbols they imported.
    from src.cover_letter import pipeline as cover_pipeline
    from src.resume import pipeline as resume_pipeline

    monkeypatch.setattr(resume_pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cover_pipeline, "OUTPUT_DIR", tmp_path)

    row = _stub_one_job_df(monkeypatch)

    rp = existing_resume_path(row["title"], row["company"], row["location"])
    rp.write_bytes(b"fake docx")
    rp.with_suffix(".scores.json").write_text(
        '{"ats_match": {"match_pct": 87}, "hr": {"hr_score": 84}}'
    )
    cp = expected_cover_letter_path(row["title"], row["company"])
    cp.write_bytes(b"fake docx")
    expected_cover_sidecar_path(row["title"], row["company"]).write_text(
        '{"track": "em", "frame": "standard", "company_hook": "We love Acme",'
        ' "company_fit_line": "Their platform work matters.",'
        ' "why_this_company": "We love Acme Their platform work matters.",'
        ' "bullets": [{"signal": "platform_devex", "text": "..."}]}'
    )

    client = TestClient(web_app.app)
    r = client.get(f"/api/job/{row['hash']}.json")
    assert r.status_code == 200
    body = r.json()

    # Top-level keys
    for key in (
        "hash",
        "title",
        "company",
        "location",
        "url",
        "description",
        "sponsorship_status",
        "score_total",
        "tier",
        "status",
        "track",
        "artifacts",
        "cover_letter_content",
        "application_defaults",
        "actions",
    ):
        assert key in body, f"missing key: {key}"

    # Artifact paths are absolute and match what we wrote
    assert body["artifacts"]["resume_path"] == str(rp.absolute())
    assert body["artifacts"]["resume_filename"] == rp.name
    assert body["artifacts"]["cover_letter_path"] == str(cp.absolute())
    assert body["artifacts"]["scores"] == {"ats_match_pct": 87, "hr_score": 84}

    # Cover letter content composed from sidecar
    assert body["cover_letter_content"]["why_this_company"].startswith("We love Acme")
    assert body["cover_letter_content"]["track"] == "em"

    # Action URLs Claude POSTs to
    assert body["actions"]["mark_applied"] == f"/api/applied/{row['hash']}"
    assert body["actions"]["mark_no_sponsorship"] == f"/api/no-sponsorship/{row['hash']}"

    # Single-source-of-truth: all of APPLICATION_DEFAULTS makes it through
    from src.resume import profile as resume_profile

    assert set(body["application_defaults"].keys()) == set(
        resume_profile.APPLICATION_DEFAULTS.keys()
    )


def test_api_job_handles_missing_artifacts(monkeypatch, tmp_path):
    """When neither the resume nor the cover letter is on disk, the JSON
    surfaces nulls (not crashes). cover_letter_content is null too —
    Claude is instructed (in the prompt) to flag the job."""
    from fastapi.testclient import TestClient

    from src import utils as utils_module
    from src.cover_letter import pipeline as cover_pipeline
    from src.resume import pipeline as resume_pipeline
    from web import app as web_app

    monkeypatch.setattr(utils_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(resume_pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cover_pipeline, "OUTPUT_DIR", tmp_path)
    row = _stub_one_job_df(monkeypatch)

    client = TestClient(web_app.app)
    r = client.get(f"/api/job/{row['hash']}.json")
    assert r.status_code == 200
    body = r.json()

    assert body["artifacts"]["resume_path"] is None
    assert body["artifacts"]["cover_letter_path"] is None
    assert body["artifacts"]["scores"] is None
    assert body["cover_letter_content"] is None


def test_api_job_handles_pathological_title(monkeypatch, tmp_path):
    """A 1000-char title (a scraper bug we've actually seen) produces a
    filename longer than the filesystem max. existing_resume_path's
    .exists() probe raises OSError ENAMETOOLONG. The endpoint must
    treat that as "no artifact" rather than 500."""
    from fastapi.testclient import TestClient

    from src import utils as utils_module
    from src.cover_letter import pipeline as cover_pipeline
    from src.resume import pipeline as resume_pipeline
    from web import app as web_app

    monkeypatch.setattr(utils_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(resume_pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cover_pipeline, "OUTPUT_DIR", tmp_path)

    row = _stub_one_job_df(monkeypatch, title="A" * 1000)

    client = TestClient(web_app.app)
    r = client.get(f"/api/job/{row['hash']}.json")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body["artifacts"]["resume_path"] is None
    assert body["artifacts"]["cover_letter_path"] is None


def test_api_applied_alias_flips_status(monkeypatch):
    """POST /api/applied/<hash> ≡ POST /actions/status/<hash>?status=applied
    in DB outcome. Tested separately because the alias is what Claude
    POSTs from JSON; the underlying /actions/status is a form-encoded
    HTML endpoint and its contract isn't part of the JSON API."""
    from fastapi.testclient import TestClient

    from src import db as db_module
    from web import app as web_app

    set_status_calls = []

    def fake_set_status(job_hash, status, closed_reason):
        set_status_calls.append((job_hash, status, closed_reason))

    monkeypatch.setattr(db_module, "get_job", lambda h: {"hash": h} if h == "abc123" else None)
    monkeypatch.setattr(db_module, "set_status", fake_set_status)

    client = TestClient(web_app.app)

    r = client.post("/api/applied/abc123")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "status": "applied"}
    assert set_status_calls == [("abc123", "applied", None)]

    r = client.post("/api/no-sponsorship/abc123")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "status": "no_sponsorship"}
    assert set_status_calls[-1] == ("abc123", "no_sponsorship", None)

    # 404 for unknown hash
    r = client.post("/api/applied/zzz")
    assert r.status_code == 404


def test_cover_letter_sidecar_written_on_generate(tmp_path, monkeypatch):
    """The cover-letter pipeline now writes a .json sidecar next to the
    .docx with structured content (track, why_this_company, bullets).
    /api/job/<hash>.json reads it; if it stops being written, the
    "Why this company?" answer becomes invisible to Claude."""
    from src.cover_letter import pipeline as cover_pipeline
    from src.cover_letter.template import BulletPick, CoverLetter

    letter = CoverLetter(
        hiring_manager_name="",
        opening_hook="Excited to apply for the Frontend Platform EM role.",
        company_hook="Your investment in design systems is exactly the work I want to do.",
        bullets=[
            BulletPick(signal="platform_devex", bullet="bullet 1"),
            BulletPick(signal="team_scaling", bullet="bullet 2"),
            BulletPick(signal="delivery_at_scale", bullet="bullet 3"),
        ],
        company_fit_line="Acme's frontend platform work compounds across the org.",
        jd_title="EM, FE Platform",
        jd_company="Acme",
        frame="standard",
    )

    docx_path = tmp_path / "cover.docx"
    cover_pipeline._write_sidecar(letter, docx_path)

    sidecar_path = tmp_path / "cover.json"
    assert sidecar_path.exists()
    payload = json.loads(sidecar_path.read_text())
    assert payload["track"] == "em"
    assert payload["frame"] == "standard"
    assert payload["company_hook"].startswith("Your investment")
    assert payload["company_fit_line"].startswith("Acme's frontend")
    # why_this_company is the composition: hook + " " + fit_line
    assert payload["why_this_company"] == letter.company_hook + " " + letter.company_fit_line
    assert len(payload["bullets"]) == 3
    assert payload["bullets"][0] == {"signal": "platform_devex", "text": "bullet 1"}


@pytest.mark.parametrize(
    "hook,fit,expected",
    [
        ("h", "f", "h f"),  # both
        ("only the hook", "", "only the hook"),  # hook only
        ("", "only the fit", "only the fit"),  # fit only
        ("", "", None),  # neither — null
        ("   ", "  ", None),  # whitespace-only both — null
    ],
)
def test_cover_letter_sidecar_why_composition(tmp_path, hook, fit, expected):
    """why_this_company composition rule: join non-empty parts with one
    space, return None when nothing's filled. Pinned because Claude is
    told to use this answer VERBATIM — silent regression to "" instead
    of None would put empty strings in form fields."""
    from src.cover_letter import pipeline as cover_pipeline
    from src.cover_letter.template import BulletPick, CoverLetter

    letter = CoverLetter(
        hiring_manager_name="",
        opening_hook="x",
        company_hook=hook,
        bullets=[BulletPick(signal="platform_devex", bullet="b")],
        company_fit_line=fit,
        frame="standard",
    )
    cover_pipeline._write_sidecar(letter, tmp_path / "x.docx")
    payload = json.loads((tmp_path / "x.json").read_text())
    assert payload["why_this_company"] == expected


# ────────────────────────────────────────────────────────────────────────────
# /api/jobs.json — has_resume / has_cover_letter booleans drive the grid's
# per-row Generate / Regenerate buttons. If these stop being computed, every
# row shows "+ Gen" and the user can't tell what's already done.
# ────────────────────────────────────────────────────────────────────────────
def test_jobs_json_includes_resume_and_cover_flags(monkeypatch, tmp_path):
    """Each row in /api/jobs.json carries has_resume / has_cover_letter
    booleans computed by membership-checking expected filenames against
    the contents of OUTPUT_DIR. Pin: both fields exist; one row with the
    files on disk reads True, one row without reads False."""
    from fastapi.testclient import TestClient

    from src import db as db_module
    from src import utils as utils_module
    from src.cover_letter import expected_cover_letter_path
    from src.cover_letter import pipeline as cover_pipeline
    from src.resume import expected_resume_path
    from src.resume import pipeline as resume_pipeline
    from web import app as web_app

    # Redirect OUTPUT_DIR to a temp dir we control. The endpoint imports
    # OUTPUT_DIR lazily inside the function so monkeypatching utils
    # alone is enough — but we also patch the resume + cover modules
    # because their helpers use the symbol they imported.
    monkeypatch.setattr(utils_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(resume_pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cover_pipeline, "OUTPUT_DIR", tmp_path)

    df = pd.DataFrame(
        [
            {
                "hash": "with-files",
                "source": "greenhouse",
                "company": "Acme",
                "title": "Engineering Manager",
                "location": "Remote",
                "url": "u1",
                "description": "x",
                "posted_at": "2026-04-20",
                "salary_min": None,
                "salary_max": None,
                "remote": 1,
                "sponsorship_status": "unknown",
                "prefilter_passed": 1,
                "prefilter_reason": "",
                "score_total": 95.0,
                "score_breakdown": "{}",
                "score_rationale": "",
                "tier": "strong",
                "status": "shortlisted",
                "status_at": None,
                "closed_reason": None,
                "applied_at": None,
                "notes": "",
                "scraped_at": "2026-04-20T00:00:00",
            },
            {
                "hash": "no-files",
                "source": "greenhouse",
                "company": "Beacon",
                "title": "Staff Frontend Engineer",
                "location": "",
                "url": "u2",
                "description": "x",
                "posted_at": "2026-04-20",
                "salary_min": None,
                "salary_max": None,
                "remote": 1,
                "sponsorship_status": "unknown",
                "prefilter_passed": 1,
                "prefilter_reason": "",
                "score_total": 80.0,
                "score_breakdown": "{}",
                "score_rationale": "",
                "tier": "strong",
                "status": "new",
                "status_at": None,
                "closed_reason": None,
                "applied_at": None,
                "notes": "",
                "scraped_at": "2026-04-20T00:00:00",
            },
        ]
    )
    monkeypatch.setattr(db_module, "to_dataframe", lambda: df)

    # Write the expected files for the first row only.
    expected_resume_path("Engineering Manager", "Acme", "Remote").write_bytes(b"x")
    expected_cover_letter_path("Engineering Manager", "Acme").write_bytes(b"x")

    client = TestClient(web_app.app)
    r = client.get("/api/jobs.json")
    assert r.status_code == 200
    rows = {row["hash"]: row for row in r.json()}

    assert rows["with-files"]["has_resume"] is True
    assert rows["with-files"]["has_cover_letter"] is True
    assert rows["no-files"]["has_resume"] is False
    assert rows["no-files"]["has_cover_letter"] is False
