"""Tests for the `inject-csv` bulk URL-injection command.

Network + LLM are mocked: `inject_from_url` (fetch/extract), `prefilter`, and
`score_job` are stubbed so the test exercises only the command's
orchestration — parsing, skip classification, score persistence, and the
strong-fit autogen gate.
"""

from __future__ import annotations

import csv

import pytest
from click.testing import CliRunner

from src import cli as cli_module
from src import db as db_module
from src.enrichment import pipeline as pipeline_module
from src.models import Job

_HEADER = [
    "#", "Tier", "Title", "Company", "Location", "Work Mode", "Salary",
    "Score", "Title", "Skills", "Scope", "Domain", "Loc", "Comp", "Apply URL",
]  # fmt: skip


def _row(n, title, company, url, csv_score="12345"):
    # csv_score is deliberately a sentinel: the DB must never pick it up.
    return [
        str(n), "Possible", title, company, "Austin, TX", "Remote", "$100K",
        csv_score, "18", "12", "12", "9", "9", "5", url,
    ]  # fmt: skip


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        w.writerows(rows)


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "jobs.db")
    db_module.init_db()
    return tmp_path


def _fake_inject(url, model=None):
    if "404" in url:
        return None, "fetch failed: HTTP 404"
    return (
        Job(
            source="manual",
            company="Acme",
            title="Engineering Manager",
            location="Austin, TX",
            url=url,
            description="Lead a team building React micro-frontends.",
        ),
        "ok",
    )


def _patch_pipeline(monkeypatch, *, total, tier):
    monkeypatch.setattr(
        pipeline_module, "prefilter", lambda title, desc: (True, "title match", "unknown")
    )
    monkeypatch.setattr(
        pipeline_module,
        "score_job",
        lambda *a, **k: {
            "title_match": 28,
            "skills_match": 20,
            "leadership_scope": 14,
            "domain_alignment": 9,
            "location_fit": 9,
            "comp_confidence": 8,
            "total": total,
            "tier": tier,
            "rationale": "test",
        },
    )


def _patch_autogen(monkeypatch):
    calls = {"resume": 0, "cover": 0}

    def fake_resume(*a, **k):
        calls["resume"] += 1
        return "/tmp/fake_resume.docx"

    def fake_cover(*a, **k):
        calls["cover"] += 1
        return "/tmp/fake_cover.docx"

    monkeypatch.setattr(cli_module, "autogen_resume_if_missing", fake_resume)
    monkeypatch.setattr(cli_module, "autogen_cover_letter_if_missing", fake_cover)
    return calls


def test_no_url_rows_are_skipped_not_inserted(tmp_db, monkeypatch):
    monkeypatch.setattr(cli_module, "inject_from_url", _fake_inject)
    _patch_pipeline(monkeypatch, total=70, tier="possible")
    _patch_autogen(monkeypatch)
    csv_path = tmp_db / "in.csv"
    _write_csv(
        csv_path,
        [
            _row(1, "EM A", "Acme", "Open"),
            _row(2, "EM B", "Beta", ""),
        ],
    )

    result = CliRunner().invoke(cli_module.cli, ["inject-csv", str(csv_path)])

    assert result.exit_code == 0, result.output
    assert db_module.to_dataframe().empty
    skipped = tmp_db / "in_skipped.csv"
    assert skipped.exists()
    body = skipped.read_text()
    assert "no-url" in body and "EM A" in body and "EM B" in body


def test_fetch_failure_skipped_with_reason(tmp_db, monkeypatch):
    monkeypatch.setattr(cli_module, "inject_from_url", _fake_inject)
    _patch_pipeline(monkeypatch, total=70, tier="possible")
    _patch_autogen(monkeypatch)
    csv_path = tmp_db / "in.csv"
    _write_csv(csv_path, [_row(1, "EM", "Acme", "https://example.com/404")])

    result = CliRunner().invoke(cli_module.cli, ["inject-csv", str(csv_path)])

    assert result.exit_code == 0, result.output
    assert db_module.to_dataframe().empty
    assert "HTTP 404" in (tmp_db / "in_skipped.csv").read_text()


def test_strong_fit_inserted_scored_and_autogen_fires(tmp_db, monkeypatch):
    monkeypatch.setattr(cli_module, "inject_from_url", _fake_inject)
    _patch_pipeline(monkeypatch, total=85, tier="strong")
    calls = _patch_autogen(monkeypatch)
    csv_path = tmp_db / "in.csv"
    _write_csv(csv_path, [_row(1, "EM", "Acme", "https://example.com/job/1")])

    result = CliRunner().invoke(cli_module.cli, ["inject-csv", str(csv_path)])

    assert result.exit_code == 0, result.output
    df = db_module.to_dataframe()
    assert len(df) == 1
    assert int(df.iloc[0]["score_total"]) == 85
    assert df.iloc[0]["tier"] == "strong"
    assert calls == {"resume": 1, "cover": 1}


def test_possible_fit_inserted_but_no_autogen(tmp_db, monkeypatch):
    monkeypatch.setattr(cli_module, "inject_from_url", _fake_inject)
    _patch_pipeline(monkeypatch, total=70, tier="possible")
    calls = _patch_autogen(monkeypatch)
    csv_path = tmp_db / "in.csv"
    _write_csv(csv_path, [_row(1, "EM", "Acme", "https://example.com/job/2")])

    result = CliRunner().invoke(cli_module.cli, ["inject-csv", str(csv_path)])

    assert result.exit_code == 0, result.output
    df = db_module.to_dataframe()
    assert len(df) == 1 and df.iloc[0]["tier"] == "possible"
    assert calls == {"resume": 0, "cover": 0}


def test_csv_score_columns_never_written_to_db(tmp_db, monkeypatch):
    monkeypatch.setattr(cli_module, "inject_from_url", _fake_inject)
    _patch_pipeline(monkeypatch, total=85, tier="strong")
    _patch_autogen(monkeypatch)
    csv_path = tmp_db / "in.csv"
    # csv_score sentinel is 12345; the app must score it 85 instead.
    _write_csv(csv_path, [_row(1, "EM", "Acme", "https://example.com/job/3", csv_score="12345")])

    result = CliRunner().invoke(cli_module.cli, ["inject-csv", str(csv_path)])

    assert result.exit_code == 0, result.output
    row = db_module.to_dataframe().iloc[0]
    assert int(row["score_total"]) == 85
    assert "12345" not in (row["score_breakdown"] or "")


def test_unexpected_header_fails_loud(tmp_db, monkeypatch):
    monkeypatch.setattr(cli_module, "inject_from_url", _fake_inject)
    csv_path = tmp_db / "in.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "title"])
        w.writerow(["https://example.com/x", "EM"])

    result = CliRunner().invoke(cli_module.cli, ["inject-csv", str(csv_path)])

    assert result.exit_code != 0
    assert "unexpected CSV header" in result.output
    assert db_module.to_dataframe().empty


def test_runs_against_fresh_db_without_explicit_init(monkeypatch, tmp_path):
    """inject-csv is a standalone command; it must create the schema itself
    rather than crash with 'no such table: jobs' when `init` wasn't run."""
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "jobs.db")  # NOT init'd
    monkeypatch.setattr(cli_module, "inject_from_url", _fake_inject)
    _patch_pipeline(monkeypatch, total=70, tier="possible")
    _patch_autogen(monkeypatch)
    csv_path = tmp_path / "in.csv"
    _write_csv(csv_path, [_row(1, "EM", "Acme", "https://example.com/job/9")])

    result = CliRunner().invoke(cli_module.cli, ["inject-csv", str(csv_path)])

    assert result.exit_code == 0, result.output
    assert len(db_module.to_dataframe()) == 1


def test_limit_caps_processed_url_rows(tmp_db, monkeypatch):
    monkeypatch.setattr(cli_module, "inject_from_url", _fake_inject)
    _patch_pipeline(monkeypatch, total=70, tier="possible")
    _patch_autogen(monkeypatch)
    csv_path = tmp_db / "in.csv"
    _write_csv(
        csv_path,
        [_row(i, f"EM {i}", "Acme", f"https://example.com/job/{i}") for i in range(1, 6)],
    )

    result = CliRunner().invoke(cli_module.cli, ["inject-csv", str(csv_path), "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert len(db_module.to_dataframe()) == 2
