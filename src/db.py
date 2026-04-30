"""SQLite persistence layer with idempotent upsert and pandas export."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path

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

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
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

        # One-shot migration: rebuild hashes from URL and merge duplicates that
        # the old (source, company, title, location) hash failed to dedupe.
        _migrate_url_hash(c)


# Status priority used by the URL-hash migration to pick the surviving row
# when multiple old rows collapse into the same new hash.
_STATUS_PRIORITY = {
    "offer": 7,
    "interviewing": 6,
    "applied": 5,
    "applying": 4,
    "shortlisted": 3,
    "new": 2,
    "closed": 1,
    "not_interested": 0,  # never engaged; lowest priority on conflict
}


def _migrate_url_hash(c: sqlite3.Connection) -> None:
    """Rebuild the `jobs.hash` column from URL and merge duplicates.

    Runs once: a sentinel row is written to `meta` after success and
    subsequent boots are no-ops. When multiple old rows collapse into the
    same new hash, the winner is chosen by (score_total, status priority,
    scraped_at), and the losers are deleted.
    """
    cur = c.execute("SELECT value FROM meta WHERE key = 'url_hash_v1'")
    if cur.fetchone():
        return

    import hashlib
    from collections import defaultdict
    from datetime import datetime

    from .models import _normalize_for_hash, _normalize_url

    def _new_hash(url, source, company, title, location) -> str:
        if url:
            key = ("url|" + _normalize_url(url)).encode()
        else:
            key = "|".join(
                _normalize_for_hash(p or "") for p in (source, company, title, location)
            ).encode()
        return hashlib.sha256(key).hexdigest()[:16]

    rows = c.execute(
        "SELECT hash, source, company, title, location, url, score_total, "
        "status, scraped_at FROM jobs"
    ).fetchall()

    # group old hashes by new hash
    groups: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        nh = _new_hash(r["url"] or "", r["source"], r["company"], r["title"], r["location"] or "")
        groups[nh].append(r)

    deleted = 0
    rehashed = 0

    for new_h, members in groups.items():
        if len(members) == 1:
            old_h = members[0]["hash"]
            if old_h != new_h:
                # Defensive: don't crash if a row already sits at new_h
                # (shouldn't happen given the grouping, but cheap to check).
                exists = c.execute("SELECT 1 FROM jobs WHERE hash = ?", (new_h,)).fetchone()
                if exists:
                    c.execute("DELETE FROM jobs WHERE hash = ?", (old_h,))
                    deleted += 1
                else:
                    c.execute("UPDATE jobs SET hash = ? WHERE hash = ?", (new_h, old_h))
                    rehashed += 1
            continue

        # Multiple old rows → one new hash. Pick the most-progressed winner.
        def _key(row):
            score = row["score_total"] if row["score_total"] is not None else -1
            pri = _STATUS_PRIORITY.get(row["status"] or "new", 0)
            ts = row["scraped_at"] or ""
            return (score, pri, ts)

        ordered = sorted(members, key=_key, reverse=True)
        winner = ordered[0]["hash"]
        losers = [m["hash"] for m in ordered[1:]]

        placeholders = ",".join("?" * len(losers))
        c.execute(f"DELETE FROM jobs WHERE hash IN ({placeholders})", losers)
        deleted += len(losers)

        if winner != new_h:
            c.execute("UPDATE jobs SET hash = ? WHERE hash = ?", (new_h, winner))
            rehashed += 1

    c.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        ("url_hash_v1", f"{datetime.utcnow().isoformat()} rehashed={rehashed} deleted={deleted}"),
    )


_UPSERT_SQL = """
INSERT INTO jobs (
    hash, source, company, title, location, url, description,
    posted_at, salary_min, salary_max, remote, sponsorship_status,
    prefilter_passed, prefilter_reason, score_total, score_breakdown,
    score_rationale, tier, applied_at, notes, scraped_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(hash) DO UPDATE SET
    source = excluded.source,
    company = excluded.company,
    title = excluded.title,
    location = excluded.location,
    url = excluded.url,
    description = excluded.description,
    posted_at = excluded.posted_at,
    salary_min = excluded.salary_min,
    salary_max = excluded.salary_max,
    remote = excluded.remote,
    scraped_at = excluded.scraped_at
WHERE excluded.scraped_at >= jobs.scraped_at
"""


def _row_tuple(j: Job) -> tuple:
    return (
        j.hash,
        j.source,
        j.company,
        j.title,
        j.location,
        j.url,
        j.description,
        j.posted_at,
        j.salary_min,
        j.salary_max,
        int(j.remote) if j.remote is not None else None,
        j.sponsorship_status,
        int(j.prefilter_passed),
        j.prefilter_reason,
        j.score_total,
        j.score_breakdown,
        j.score_rationale,
        j.tier,
        j.applied_at,
        j.notes,
        j.scraped_at,
    )


def upsert_job(job: Job) -> bool:
    """Insert if hash is new; otherwise update the content fields (title,
    location, description, salary, posted_at, scraped_at) on the existing
    row while preserving enrichment (score, prefilter, sponsorship) and
    workflow (status, applied_at, notes, closed_reason).

    Returns True if a new row was inserted, False if an existing row was
    updated (or left unchanged because the incoming `scraped_at` was older).
    """
    with conn() as c:
        existed = c.execute("SELECT 1 FROM jobs WHERE hash = ?", (job.hash,)).fetchone() is not None
        c.execute(_UPSERT_SQL, _row_tuple(job))
        return not existed


def upsert_many(jobs: Iterable[Job]) -> tuple[int, int]:
    """Returns (new_inserted, updated_existing).

    Batched into a single transaction with executemany — at 6k rows this is
    ~30x faster than per-row connections. Existing rows have their content
    fields refreshed but enrichment and workflow state are preserved.
    """
    job_list = list(jobs)
    if not job_list:
        return 0, 0
    rows = [_row_tuple(j) for j in job_list]
    hashes = [j.hash for j in job_list]
    with conn() as c:
        # Pre-classify so we can return clear (new, updated) counts. SQLite's
        # default SQLITE_MAX_VARIABLE_NUMBER is 32766 since 3.32, so a single
        # IN (...) covers any realistic scrape batch.
        placeholders = ",".join("?" * len(hashes))
        existing = {
            row[0]
            for row in c.execute(
                f"SELECT hash FROM jobs WHERE hash IN ({placeholders})", hashes
            ).fetchall()
        }
        c.executemany(_UPSERT_SQL, rows)
    new = sum(1 for h in hashes if h not in existing)
    updated = len(hashes) - new
    return new, updated


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
        cur = c.execute("SELECT * FROM jobs WHERE prefilter_passed = 0 AND prefilter_reason = ''")
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
    closed_reason: str | None = None,
) -> None:
    """Transition a job to a new status.

    Validates against `status.STATUSES` / `status.CLOSED_REASONS`. Sets
    `status_at = now` on every call. Sets `applied_at = now` the first time
    a job transitions to status='applied' (preserved on later transitions).
    Clears `closed_reason` automatically when status != 'closed'.
    """
    from datetime import datetime

    from .status import is_valid_closed_reason, is_valid_status

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


def to_dataframe(filters: dict | None = None) -> pd.DataFrame:
    """Read entire jobs table as a pandas DataFrame for the UI."""
    with conn() as c:
        df = pd.read_sql_query(
            "SELECT * FROM jobs ORDER BY score_total DESC NULLS LAST, scraped_at DESC", c
        )
    if filters:
        for k, v in filters.items():
            if v is not None and k in df.columns:
                df = df[df[k] == v]
    return df


def get_job(job_hash: str) -> dict | None:
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
