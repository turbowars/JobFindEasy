"""SQLite persistence layer with idempotent upsert and pandas export."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from .models import Job

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    hash TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    url TEXT,
    description TEXT,
    posted_at TEXT,
    salary_min INTEGER,
    salary_max INTEGER,
    remote INTEGER,
    sponsorship_status TEXT DEFAULT 'unknown',
    prefilter_passed INTEGER DEFAULT 0,
    prefilter_reason TEXT,
    score_total INTEGER,
    score_breakdown TEXT,
    score_rationale TEXT,
    tier TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    status_at TEXT,
    closed_reason TEXT,
    applied_at TEXT,
    notes TEXT,
    scraped_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_score ON jobs(score_total DESC);
CREATE INDEX IF NOT EXISTS idx_tier ON jobs(tier);
CREATE INDEX IF NOT EXISTS idx_scraped ON jobs(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_company ON jobs(company);
-- idx_status is created in init_db() below, after ALTER adds the column
-- on existing DBs that predate it.

CREATE TABLE IF NOT EXISTS scrape_state (
    source_key TEXT PRIMARY KEY,
    last_scraped_at TEXT NOT NULL
);
"""


@contextmanager
def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    """Create tables and indexes. Idempotent.

    Also runs lightweight migrations for columns added after the initial
    schema (SQLite has no `ADD COLUMN IF NOT EXISTS`, so we try and swallow
    duplicate-column errors).
    """
    with conn() as c:
        c.executescript(SCHEMA)
        # Forward-compat ALTERs for columns added since the original schema.
        # Each is idempotent (swallows "duplicate column" on re-run).
        for ddl in (
            "ALTER TABLE jobs ADD COLUMN score_fail_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN scored_at TEXT",
            "ALTER TABLE jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'new'",
            "ALTER TABLE jobs ADD COLUMN status_at TEXT",
            "ALTER TABLE jobs ADD COLUMN closed_reason TEXT",
            "CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)",
        ):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column / index already exists

        # One-time backfill: rows previously marked applied=1 (legacy boolean
        # column) become status='applied'. Idempotent — only touches rows
        # still in the default 'new' state.
        try:
            c.execute(
                """UPDATE jobs
                      SET status = 'applied',
                          status_at = COALESCE(applied_at, scraped_at)
                    WHERE applied = 1 AND status = 'new'"""
            )
        except sqlite3.OperationalError:
            pass  # `applied` column already removed

        # Drop the legacy `applied` boolean column. Status is now the source
        # of truth; `applied_at` (a timestamp) is retained.
        for ddl in (
            "DROP INDEX IF EXISTS idx_applied",
            "ALTER TABLE jobs DROP COLUMN applied",
        ):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass  # already dropped, or sqlite < 3.35 — column orphans harmlessly


def upsert_job(job: Job) -> bool:
    """Insert if hash is new; skip entirely if already present.

    Returns True if newly inserted, False if skipped as duplicate.
    `INSERT OR IGNORE` makes this a single round-trip — no SELECT, no UPDATE.
    """
    with conn() as c:
        cur = c.execute(
            """
            INSERT OR IGNORE INTO jobs (
                hash, source, company, title, location, url, description,
                posted_at, salary_min, salary_max, remote, sponsorship_status,
                prefilter_passed, prefilter_reason, score_total, score_breakdown,
                score_rationale, tier, applied_at, notes, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.hash, job.source, job.company, job.title, job.location, job.url,
                job.description, job.posted_at, job.salary_min, job.salary_max,
                int(job.remote) if job.remote is not None else None,
                job.sponsorship_status, int(job.prefilter_passed),
                job.prefilter_reason, job.score_total, job.score_breakdown,
                job.score_rationale, job.tier,
                job.applied_at, job.notes, job.scraped_at,
            ),
        )
        return cur.rowcount == 1


def upsert_many(jobs: Iterable[Job]) -> tuple[int, int]:
    """Returns (new_inserted, skipped_duplicates).

    Batched into a single transaction with executemany — at 6k rows this is
    ~30x faster than per-row connections.
    """
    rows = [
        (
            j.hash, j.source, j.company, j.title, j.location, j.url,
            j.description, j.posted_at, j.salary_min, j.salary_max,
            int(j.remote) if j.remote is not None else None,
            j.sponsorship_status, int(j.prefilter_passed),
            j.prefilter_reason, j.score_total, j.score_breakdown,
            j.score_rationale, j.tier,
            j.applied_at, j.notes, j.scraped_at,
        )
        for j in jobs
    ]
    if not rows:
        return 0, 0
    with conn() as c:
        before = c.total_changes
        c.executemany(
            """
            INSERT OR IGNORE INTO jobs (
                hash, source, company, title, location, url, description,
                posted_at, salary_min, salary_max, remote, sponsorship_status,
                prefilter_passed, prefilter_reason, score_total, score_breakdown,
                score_rationale, tier, applied_at, notes, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        new = c.total_changes - before
    skipped = len(rows) - new
    return new, skipped


SCORE_FAIL_DEAD_LETTER_THRESHOLD = 3


def get_unscored_passed() -> list[dict]:
    """Jobs that passed prefilter but haven't been LLM-scored yet.

    Newest first (so when score_limit is hit, fresh JDs win over backlog).
    Excludes rows that have hit the score-fail dead-letter threshold —
    those are skipped going forward to stop burning tokens on JDs that
    consistently fail to parse.
    """
    with conn() as c:
        cur = c.execute(
            """
            SELECT * FROM jobs
            WHERE prefilter_passed = 1
              AND score_total IS NULL
              AND score_fail_count < ?
            ORDER BY scraped_at DESC
            """,
            (SCORE_FAIL_DEAD_LETTER_THRESHOLD,),
        )
        return [dict(r) for r in cur.fetchall()]


def record_score_failure(job_hash: str) -> int:
    """Increment score_fail_count for a job. Returns the new count.

    Called by the scoring loop when score_job() returns None (LLM error,
    JSON parse failure, etc.). When the count hits SCORE_FAIL_DEAD_LETTER_THRESHOLD,
    the row stops appearing in get_unscored_passed() — the job is treated
    as unscoreable and won't burn more tokens.
    """
    with conn() as c:
        c.execute(
            "UPDATE jobs SET score_fail_count = score_fail_count + 1 WHERE hash = ?",
            (job_hash,),
        )
        cur = c.execute("SELECT score_fail_count FROM jobs WHERE hash = ?", (job_hash,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


def get_unfiltered() -> list[dict]:
    """Jobs that haven't been pre-filtered yet."""
    with conn() as c:
        cur = c.execute(
            "SELECT * FROM jobs WHERE prefilter_passed = 0 AND prefilter_reason = ''"
        )
        return [dict(r) for r in cur.fetchall()]


def update_score(job_hash: str, total: int, breakdown: str, rationale: str, tier: str) -> None:
    from datetime import datetime
    with conn() as c:
        c.execute(
            "UPDATE jobs SET score_total=?, score_breakdown=?, score_rationale=?, tier=?, scored_at=?, score_fail_count=0 WHERE hash=?",
            (total, breakdown, rationale, tier, datetime.utcnow().isoformat(), job_hash),
        )


def update_prefilter(job_hash: str, passed: bool, reason: str, sponsorship: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE jobs SET prefilter_passed=?, prefilter_reason=?, sponsorship_status=? WHERE hash=?",
            (int(passed), reason, sponsorship, job_hash),
        )


def set_status(
    job_hash: str,
    status: str,
    closed_reason: Optional[str] = None,
) -> None:
    """Transition a job to a new status.

    Validates against `status.STATUSES` / `status.CLOSED_REASONS`. Sets
    `status_at = now` on every call. Sets `applied_at = now` the first time
    a job transitions to status='applied' (preserved on later transitions).
    Clears `closed_reason` automatically when status != 'closed'.
    """
    from datetime import datetime
    from .status import is_valid_status, is_valid_closed_reason
    if not is_valid_status(status):
        raise ValueError(f"invalid status: {status!r}")
    if closed_reason is not None and not is_valid_closed_reason(closed_reason):
        raise ValueError(f"invalid closed_reason: {closed_reason!r}")
    if status != "closed":
        closed_reason = None
    now = datetime.utcnow().isoformat()
    with conn() as c:
        if status == "applied":
            c.execute(
                """UPDATE jobs
                      SET status = ?, status_at = ?, closed_reason = ?,
                          applied_at = COALESCE(applied_at, ?)
                    WHERE hash = ?""",
                (status, now, closed_reason, now, job_hash),
            )
        else:
            c.execute(
                "UPDATE jobs SET status=?, status_at=?, closed_reason=? WHERE hash=?",
                (status, now, closed_reason, job_hash),
            )


def get_status_counts() -> dict[str, int]:
    """Returns {status: count} across all jobs. Missing keys → 0 in caller."""
    with conn() as c:
        cur = c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
        return {row[0]: int(row[1]) for row in cur.fetchall()}


def sweep_ghosted(days: int) -> int:
    """Flip status='applied' rows older than `days` to status='closed' /
    closed_reason='ghosted'. Returns the number of rows updated.
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    now = datetime.utcnow().isoformat()
    with conn() as c:
        cur = c.execute(
            """UPDATE jobs
                  SET status = 'closed',
                      closed_reason = 'ghosted',
                      status_at = ?
                WHERE status = 'applied' AND status_at IS NOT NULL
                  AND status_at < ?""",
            (now, cutoff),
        )
        return cur.rowcount or 0


def set_notes(job_hash: str, notes: str) -> None:
    with conn() as c:
        c.execute("UPDATE jobs SET notes=? WHERE hash=?", (notes, job_hash))


def get_strong_fits_today(min_score: int = 80) -> list[dict]:
    """For the daily notification — strong fits from the last 24h that
    haven't been touched yet (status='new' or 'shortlisted')."""
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    with conn() as c:
        cur = c.execute(
            """SELECT * FROM jobs
               WHERE score_total >= ? AND scraped_at >= ?
               AND sponsorship_status != 'denied'
               AND status IN ('new', 'shortlisted')
               ORDER BY score_total DESC""",
            (min_score, cutoff),
        )
        return [dict(r) for r in cur.fetchall()]


def to_dataframe(filters: Optional[dict] = None) -> pd.DataFrame:
    """Read entire jobs table as a pandas DataFrame for the UI."""
    with conn() as c:
        df = pd.read_sql_query("SELECT * FROM jobs ORDER BY score_total DESC NULLS LAST, scraped_at DESC", c)
    if filters:
        for k, v in filters.items():
            if v is not None and k in df.columns:
                df = df[df[k] == v]
    return df


def get_job(job_hash: str) -> Optional[dict]:
    with conn() as c:
        cur = c.execute("SELECT * FROM jobs WHERE hash = ?", (job_hash,))
        row = cur.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Scrape state — used by the runner to resume-from-where-it-left-off
#
# Each per-source-instance gets a key (e.g. "greenhouse:airbnb", "remotive",
# "hackernews"). The runner records the time of each successful scrape and
# skips any source whose last_scraped_at is within the configured window on
# subsequent runs. Consecutive autoscrape cycles thus continue with whichever
# sources are stale, rather than re-fetching everything from scratch.
# ---------------------------------------------------------------------------

def get_recently_scraped_keys(within_minutes: int) -> set[str]:
    """Return source_keys scraped within the last N minutes."""
    if within_minutes is None or within_minutes <= 0:
        return set()
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(minutes=within_minutes)).isoformat()
    with conn() as c:
        cur = c.execute(
            "SELECT source_key FROM scrape_state WHERE last_scraped_at >= ?",
            (cutoff,),
        )
        return {row[0] for row in cur.fetchall()}


def mark_scraped(source_keys: Iterable[str]) -> None:
    """Mark one or many source_keys as scraped 'now'."""
    from datetime import datetime
    keys = [k for k in source_keys if k]
    if not keys:
        return
    now = datetime.utcnow().isoformat()
    rows = [(k, now) for k in keys]
    with conn() as c:
        c.executemany(
            "INSERT INTO scrape_state (source_key, last_scraped_at) "
            "VALUES (?, ?) "
            "ON CONFLICT(source_key) DO UPDATE SET last_scraped_at = excluded.last_scraped_at",
            rows,
        )
