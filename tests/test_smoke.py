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
# 8. Equifax title override per track
# ---------------------------------------------------------------------------
def test_equifax_title_override_by_track():
    """Equifax is the only role with a JD-flexed title. IC track gets
    `(Tech Lead)`, EM track gets `(Engineering Lead)`."""
    from src.resume.pipeline import _equifax_title_override

    assert (
        _equifax_title_override("Staff Frontend Engineer", "ic")
        == "Staff Frontend Engineer (Tech Lead)"
    )
    assert (
        _equifax_title_override("Engineering Manager, Identity Frontend", "em")
        == "Engineering Manager, Identity Frontend (Engineering Lead)"
    )


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

    client = TestClient(web_app.app)
    r = client.post(
        "/actions/status/abc123",
        data={"status": "closed", "closed_reason": "ghosted"},
    )
    assert r.status_code == 204
    assert len(set_status_calls) == 1
    h, status, args, _ = set_status_calls[0]
    assert h == "abc123"
    assert status == "closed"
    # `closed_reason` is the second positional arg to db.set_status
    assert args == ("ghosted",)
