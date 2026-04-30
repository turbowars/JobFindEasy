# JobFindEasy — Engineering Guidelines for AI Assistants

## The Zen of Python (PEP 20)

> Beautiful is better than ugly.
> Explicit is better than implicit.
> Simple is better than complex.
> Complex is better than complicated.
> Flat is better than nested.
> Sparse is better than dense.
> Readability counts.
> Special cases aren't special enough to break the rules.
> Although practicality beats purity.
> Errors should never pass silently.
> Unless explicitly silenced.
> In the face of ambiguity, refuse the temptation to guess.
> There should be one — and preferably only one — obvious way to do it.
> Although that way may not be obvious at first unless you're Dutch.
> Now is better than never.
> Although never is often better than *right* now.
> If the implementation is hard to explain, it's a bad idea.
> If the implementation is easy to explain, it may be a good idea.
> Namespaces are one honking great idea — let's do more of those!

These are the working values for this codebase. When a rule below conflicts with the Zen, the Zen wins.

---

## Project context

JobFindEasy is a single-user, local-first job-search agent for Dheeraj Sampath. The pipeline is:

```
scrape → prefilter (regex) → score (LLM, Haiku/Gemini) → tier strong/possible/skip
                                                                     ↓
                       generate tailored .docx resume (Sonnet, with retry)
                                                                     ↓
                       ATS keyword match (rapidfuzz) + HR sim (Haiku)
                                                                     ↓
                       sidecar .scores.json → mirror to ~/Public/JobFindEasy
```

The dashboard ([web/app.py](web/app.py)) is FastAPI + HTMX + AG Grid; SQLite is the only data store. Cost target ~$5/mo at 200 jobs/day.

## Module layout (where things live)

| Concern | Module | Notes |
|---|---|---|
| Locked profile facts | `src/resume/profile.py` | Name, contact, education, certs, work history, master skills, projects, application defaults. Single source of truth. |
| Resume generation | `src/resume/` | Lean module: profile.py + template.py + prompts.py + pipeline.py + docx_builder.py. ~5 files, no skill-MD prompt sprawl. |
| Cover letter | `src/generate/cover_letter.py` | Legacy shape; scheduled to be rewritten in the same lean style as `src/resume/`. |
| LLM calls | `src/llm.py` | One `chat()` function. `get_model(role)` resolves model per task. |
| Scoring (job-fit, ATS extract, HR sim, URL extract) | `src/enrichment/` | All Haiku/Gemini-class. Routes through `get_model(role)`. |
| Scrapers | `src/scrapers/` | One file per source. Register in `registry.py`. |
| Web UI | `web/app.py` + `web/templates/` + `web/static/` | Routes are intentionally flat. |
| Persistent state | `src/state.py` | Generation queue, autoscrape daemon, executor singleton. |
| DB | `src/db.py` | SQLite, single file at `data/jobs.db`. Migrations in `init_db()`. |
| Shared utilities | `src/utils.py` | `PROJECT_ROOT`, `OUTPUT_DIR`, `scrub_dashes`, `safe_filename_part`, `safe_loc_suffix`. |

---

## Hard rules

### 1. Single source of truth, always.

- Profile facts live in `src/resume/profile.py`. The LLM prompt references them, the dashboard's "Apply with Claude" prompt references them, future autofill tooling references them. **Never restate.**
- Model selection goes through `src.llm.get_model(role)`. Never inline `os.environ.get("SCORING_MODEL", ...)`.
- File paths come from `src.utils.PROJECT_ROOT` / `OUTPUT_DIR`. Never recompute `Path(__file__).parent.parent.parent`.
- Constants live next to the code they govern. If the same value appears in two files, one of them is wrong.

### 2. No new files unless justified.

Editing an existing file beats creating a new one. New files are only justified when:
- They collect 3+ pieces of related logic that don't currently have a home, **or**
- They replace a deleted file wholesale.

Never add `helpers.py`, `utils2.py`, or `more_utils.py`. If `src/utils.py` is the wrong home, the answer is "rename it" not "create a sibling."

### 3. No defensive code for cases that can't happen.

Internal callers are trusted. Validate at system boundaries: HTTP requests, LLM responses, database rows, file system. Inside the module, don't `try/except` around code that has no failure mode.

A `try/except: pass` is almost always a bug — it silences errors that should surface.

### 4. No speculative abstractions.

"This might be useful later" is a no.
- 3+ usages → extract.
- 2 usages → inline.
- 1 usage → inline and delete the comment about extracting later.

### 5. Never restate a rule in two places.

If a rule lives in `profile.py`, the LLM prompt **references** it; it does not restate it. If a rule lives in the LLM prompt, the post-processing code **references** it (or trusts it); it does not re-validate the same thing differently with different wording.

Drift always wins this fight. The version you forgot to update will eventually run.

### 6. Test your bug fix.

Every bug fix lands with a test (in `tests/`) that would have caught the bug. No exceptions for "trivial" fixes — the trivial ones are the ones that come back. If you can't write a test, the fix is probably wrong or the bug isn't understood.

### 7. Errors never pass silently.

Per the Zen: `errors should never pass silently. unless explicitly silenced.`

If you're catching an exception, log it. If you're swallowing it, comment why. If you can't explain why in one line, you shouldn't be swallowing it.

---

## Style and structure

### Function length

- 80 lines is the soft ceiling. If a function is longer, ask whether the extra length is structural (e.g., a route handler that touches 5 fields linearly — fine) or accidental (e.g., a retry loop inlined — extract).
- Extract only when it removes duplication or genuinely clarifies intent. Extracting "for shortness" produces worse code.

### Imports

- Prefer top-level imports. Inline imports are acceptable for: (a) breaking circular import cycles, (b) lazy-loading a heavy or rarely-used dependency.
- Never inline-import to "be defensive" about availability. If a dependency is in `pyproject.toml`, trust it's there.

### Comments

- Comments explain *why*, not *what*. The code already says what it does; the comment says why.
- Stale comments are worse than no comments. Delete on sight.
- No `# TODO:` markers. File an issue, do it now, or accept the current state.

### Type hints

- New code is typed. Existing code is gradually typed — don't try to type the whole codebase in one PR.
- `src/resume/` is the strict-typing reference module. New modules adopt that bar.

### Logging

- Use `logging.getLogger(__name__)`, not `print()`. The Click CLI is the exception (`console.print()` from rich is the right choice for user-facing output).
- Log at the right level: `info` for one-line milestones, `warning` for recoverable failures, `error` for failures that need human attention.
- Don't log API keys, OAuth tokens, or full LLM responses. Tokens, scores, durations are fine.

---

## When in doubt

Ask one focused question rather than guessing. The cost of a clarifying question is one round-trip; the cost of guessing wrong is a regression plus the cleanup.

Use [`AskUserQuestion`](.claude/) for choice-driven decisions. Use plain text for "I'm seeing X, here's why I think Y, want me to proceed?" framing.

---

## Things to skip (anti-patterns we've already paid for)

- **Don't reformat working code "for consistency"** while doing something else. One change per turn.
- **Don't add config knobs nobody will turn.** YAGNI is law here.
- **Don't restate the same rule across SKILL.md + SYSTEM_TEMPLATE + post-process** — we did this and burned a day removing duplication.
- **Don't add a "fallback" string constant for when the source file is missing.** Fail loud (`raise FileNotFoundError`) — the fallback drifts and silently produces bad output.
- **Don't cache failed API responses.** `lru_cache` only stores successful returns; if you wrap a function that returns `{}` on error, the empty dict gets cached and the next call can't recover. Either re-raise or check inside the wrapper.
- **Don't add `# noqa` / `# type: ignore` to silence linters.** Fix the underlying issue or change the lint rule. Inline ignores are the loudest possible "I gave up here" signal.

---

## Architecture invariants (don't break these)

1. **SQLite is the only persistent store.** No Postgres, no Redis, no S3.
2. **One process.** No microservices, no separate workers — the FastAPI app + autoscrape daemon share one Python process.
3. **OpenRouter is the only LLM provider.** Models can change (Sonnet, Haiku, Gemini, etc.); the routing layer doesn't.
4. **Profile is data, not prompt.** `profile.py` is structured Python. The prompt builds a string view of it at call time. Never invert this.
5. **The .docx is the artifact.** Mammoth-rendered HTML in the dashboard is preview only — the .docx is what gets sent to recruiters.

---

## Tooling

| Tool | Config | Runs on |
|---|---|---|
| `ruff` | `pyproject.toml` `[tool.ruff]` | every save, pre-commit, CI |
| `pytest` | `tests/` | pre-commit (smoke), CI (full) |
| `mypy` | `pyproject.toml` `[tool.mypy]`, gradual | CI |
| `pre-commit` | `.pre-commit-config.yaml` | every commit |

Run locally:
```bash
ruff check src/ web/ tests/        # lint
ruff format src/ web/ tests/       # format (in place)
pytest tests/                       # tests
mypy src/resume/                    # strict-type the lean module
```
