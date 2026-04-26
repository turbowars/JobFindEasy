# Job Intelligence Agent

Local-first job search agent for Dheeraj. Scrapes 9 high-signal source types covering ~150+ companies, scores roles using a 100-point fit rubric ported from your `dheeraj-job-search` skill, generates JD-tailored resumes and cover letters via Claude, and tracks applications in a Streamlit dashboard.

No LinkedIn (intentional — high noise, fragile, mostly duplicates ATS data). No 47 aggregator sites. Just primary sources with public APIs.

## What it does

1. **Scrapes** target companies from public ATS board APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee). No auth, no bot detection, no Playwright.
2. **Pulls** remote and AI-specific roles from Remotive, AI-Jobs.net, Working Nomads (RSS/JSON APIs).
3. **Pulls** founder-posted roles from HN "Who's Hiring" monthly thread and YC Work at a Startup.
4. **Pre-filters** every job against title/skill/sponsorship rules. Drops obvious mismatches before LLM cost is incurred.
5. **Scores** survivors with Claude Haiku against the locked 6-dimension rubric.
6. **Stores** everything in SQLite, deduplicated by content hash.
7. **Notifies** you of strong fits (80+) once per day via macOS notification or Pushover.
8. **Generates** ATS-tailored resume and cover letter on demand using your existing skill rules.
9. **Tracks** application status with one click in the Streamlit UI.

## Source coverage

| Source type | Mechanism | Companies covered |
|---|---|---|
| Greenhouse | Public boards-api | 41 (airbnb, stripe, anthropic, openai, ramp, ...) |
| Lever | Public postings API | 14 (netflix, spotify, github, palantir, ...) |
| Ashby | Public job-board API | 14 (vanta, posthog, cohere, linear, cursor, ...) |
| SmartRecruiters | Public companies API | 3 (atlassian, visa, twitch) |
| Workable | Public widget API | Add slugs as you find them |
| Recruitee | Per-tenant API | Add slugs as you find them |
| Remotive | Public JSON API | All categories you select |
| AI-Jobs.net | RSS feed | All AI/ML postings |
| Working Nomads | RSS feed | Remote dev + management |
| Hacker News | Algolia HN Search API | Latest "Who's hiring" thread, keyword filtered |
| YC Work at a Startup | Backend API | Curated YC, role-type filtered |

Adding a new company on an existing ATS provider = one line in `config/sources.yaml`. Adding a new provider type = one new file in `src/scrapers/` (~80 lines, copy any existing one as a template).

## Stack

| Layer | Choice | Why |
|---|---|---|
| Storage | SQLite (one file) | Zero infra, ACID, queryable as pandas dataframe anytime |
| Scrapers | `httpx` + `feedparser` | Sync HTTP calls, async fan-out via runner |
| Concurrency | `asyncio.gather` + thread pool | 50+ scrapers in ~30s with semaphore cap of 10 |
| Pre-filter | Pure Python regex | Free, instant, drops ~80% of noise |
| LLM scorer | Claude Haiku 4.5 | ~$0.001 per job, scores fast |
| Resume gen | Claude Sonnet 4.6 + your skill | High-quality .docx, embeds your locked rules |
| UI | Streamlit | Radio buttons, filters, dataframe view, one command |
| Scheduling | `cron` + `run_daily.sh` | OS-native, no APScheduler bloat |

## Install

```bash
cd job-intelligence-agent
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# edit .env, set ANTHROPIC_API_KEY
```

## First run

```bash
python -m src.cli init       # creates data/jobs.db
python -m src.cli run        # full pipeline: scrape -> prefilter -> score -> notify
python -m src.cli stats      # quick summary
streamlit run ui/app.py      # opens dashboard at http://localhost:8501
```

## CLI reference

```bash
python -m src.cli init         # create DB
python -m src.cli scrape       # scrape only
python -m src.cli prefilter    # rule-based filter on unfiltered jobs
python -m src.cli score        # LLM score the prefilter survivors
python -m src.cli run          # full pipeline (recommended)
python -m src.cli notify       # send notification of today's strong fits
python -m src.cli stats        # database summary + top 10 fits
```

## Cron setup

```bash
crontab -e
# Add (7:30 AM CST weekdays):
30 7 * * 1-5 cd /full/path/to/job-intelligence-agent && ./scripts/run_daily.sh
```

## Adding a new company

For an existing ATS (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee), add one line to `config/sources.yaml`:

```yaml
greenhouse:
  - airbnb
  - stripe
  - your-new-company-slug   # ← here
```

The slug is the URL fragment on the company's public board, e.g. `boards.greenhouse.io/{slug}`.

## Adding a new source type

1. Copy `src/scrapers/remotive.py` as a template (cleanest API-based example).
2. Implement `__init__(self, opts: dict)` and `scrape(self) -> Iterable[Job]`.
3. Add it to `src/scrapers/registry.py`.
4. Add an entry in `config/sources.yaml`.

## Cost estimate

At ~200 jobs/day scraped:
- Pre-filter drops ~80% → ~40 jobs sent to LLM
- 40 × Haiku at ~$0.001 = $0.04/day
- On-demand resume/cover letter (Sonnet) ~$0.05 per generation
- Monthly: under $5 with heavy use

## What's not in here (intentionally)

- LinkedIn scraping — fragile, mostly duplicates ATS, archived in `src/scrapers/_archived/linkedin.py.bak` if you change your mind
- 47 aggregator boards from your original list — they re-scrape the same upstream sources with 24-48h delay
- Auto-applying — legal/ethical minefield, kills the personal touch
- Email outreach automation — different problem
- Workday — session-based, gnarly. Worth a separate scraper later if you want FAANG coverage.

## Files

```
config/
  sources.yaml         # unified source registry, edit to add boards
src/
  db.py                # SQLite + pandas
  models.py            # Job dataclass
  cli.py               # entry point
  notify.py            # macOS / Pushover notifications
  scrapers/
    base.py            # BaseScraper + clean_html helper
    registry.py        # source-type → scraper class mapping
    runner.py          # async fan-out runner with semaphore
    greenhouse.py      lever.py      ashby.py
    smartrecruiters.py workable.py   recruitee.py
    remotive.py        aijobs.py     working_nomads.py
    hackernews.py      ycombinator.py
    _archived/         # linkedin.py.bak (kept, not run)
  enrichment/
    prefilter.py       # rule-based filter
    sponsorship.py     # H-1B regex detection
    llm_scorer.py      # Claude Haiku scoring with your 6-dimension rubric
  generate/
    resume.py          # Claude Sonnet + your resume skill → .docx
    cover_letter.py    # Claude Sonnet → .docx
ui/
  app.py               # Streamlit dashboard
scripts/
  run_daily.sh         # cron-friendly daily pipeline
data/
  jobs.db              # SQLite (created at init)
  exports/             # generated resumes/cover letters land here
```
