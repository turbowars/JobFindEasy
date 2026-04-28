# JobFindEasy — Agent Instructions

## Coding Philosophy

The Zen of Python (`import this`) is the baseline. The principles most relevant here:

- **Beautiful is better than ugly.** Readable code over clever code.
- **Simple is better than complex.** Don't add abstractions until they're earned.
- **Flat is better than nested.** Prefer early returns; avoid deep nesting.
- **Sparse is better than dense.** One idea per line; don't chain for terseness.
- **Readability counts.** Names should explain intent, not just type.
- **Special cases aren't special enough to break the rules.** Follow conventions consistently.
- **Errors should never pass silently.** Surface failures; don't swallow exceptions.
- **There should be one obvious way to do it.** Avoid parallel implementations.
- **If the implementation is hard to explain, it's a bad idea.**

## Project

Local-first job search pipeline for Dheeraj. Scrapes ATS boards → pre-filters → LLM-scores → generates tailored resumes/cover letters → tracks applications in a web UI.

No LinkedIn. No aggregators. Primary ATS sources only.

## Architecture

```
src/
  cli.py            — Click CLI entry point (`python -m src.cli <cmd>` or `jia <cmd>`)
  models.py         — Canonical `Job` dataclass; all scrapers yield this type
  db.py             — SQLite layer (data/jobs.db); idempotent upsert by content hash
  llm.py            — Anthropic client wrapper (Claude Haiku for scoring, Sonnet for generation)
  enrichment/       — prefilter.py, llm_scorer.py, ats_match.py, hr_score.py, sponsorship.py
  scrapers/         — One file per ATS provider; inherit BaseScraper, yield Job objects
  resume/           — docx_builder.py, pipeline.py, prompts.py, template.py, profile.py
  generate/         — cover_letter.py, resume.py (on-demand generation entrypoints)
web/
  app.py            — FastAPI/uvicorn app; dashboard UI
config/
  sources.yaml      — Company slugs per ATS provider (single-line change to add a company)
```

## Key Conventions

- **Adding a company on an existing ATS**: one line in `config/sources.yaml` — no code changes.
- **Adding a new ATS provider**: create `src/scrapers/<provider>.py`, subclass `BaseScraper`, implement `scrape() -> Iterable[Job]`. Register in `src/scrapers/registry.py`. ~80 lines, copy an existing scraper.
- **Deduplication**: `Job.compute_hash()` normalizes title abbreviations + URL before hashing. Never bypass the hash — it's the DB primary key.
- **LLM costs**: Pre-filter drops ~80% of noise before any LLM call. Keep pre-filter rules in `enrichment/prefilter.py` cheap (regex only, no I/O).
- **Scoring rubric**: 6-dimension, 100-point scale. Locked — do not change dimensions or weights without user approval.
- **Resume generation**: Uses Claude Sonnet via `src/resume/pipeline.py`. Outputs `.docx` to `data/exports/`. Filename pattern: `Dheeraj_Sampath_<Title>_<Company>_<Location>_<hash>.docx`.
- **No Playwright / no auth**: All scrapers use public APIs (`httpx` + `feedparser`). If a new scraper needs a browser or credentials, flag it first.
- **Async concurrency**: `asyncio.gather` with semaphore cap of 10 in `src/scrapers/runner.py`. Keep scrapers sync (`def scrape`) — the runner wraps them.

## Stack

| Layer    | Technology                                                        |
| -------- | ----------------------------------------------------------------- |
| Language | Python ≥ 3.11                                                     |
| HTTP     | `httpx` (sync), `feedparser` for RSS                              |
| Storage  | SQLite (`data/jobs.db`) via stdlib `sqlite3` + `pandas`           |
| LLM      | Anthropic SDK — Haiku for scoring, Sonnet for resume/cover letter |
| Docs     | `python-docx` for .docx generation, `mammoth` for reading         |
| CLI      | `click`                                                           |
| UI       | FastAPI + uvicorn (`web/app.py`), Jinja2 templates                |
| Config   | `pyyaml`, `.env` via `python-dotenv`                              |

## Build & Run

```bash
# Install
pip install -e .

# Full pipeline (scrape → prefilter → score → notify)
python -m src.cli run        # or: make pipeline

# Individual steps
make scrape / make prefilter / make score / make notify / make stats

# Dashboard (hot-reload dev mode)
make run    # http://127.0.0.1:8826

# Database
python -m src.cli init       # create/migrate data/jobs.db
python -m src.cli stats      # summary + top 10 fits
```

## Environment

Requires `ANTHROPIC_API_KEY` in `.env`. Copy `.env.example` to get started. No other external services.

## Do Not

- Do not add aggregator scrapers (LinkedIn).
- Do not change scoring rubric dimensions/weights without explicit approval.
- Do not store secrets in code or config files — use `.env` only.
- Do not add heavy dependencies without checking `pyproject.toml` first.
