"""Concurrent scraper runner.

Reads config/sources.yaml, instantiates the right scraper for each entry,
runs them concurrently with a semaphore cap, and returns all yielded Jobs.

The scrapers themselves are sync (they use httpx Client). We run each
scraper's .scrape() in a thread to keep concurrency simple and because the
scrapers internally already loop over slugs/endpoints.

Resume-from-where-it-left-off: each scraper-instance has one or more
"source_keys" (e.g. ``greenhouse:airbnb`` for ATS slugs, ``remotive`` for
endpoint scrapers). Before running, the runner consults
``db.get_recently_scraped_keys(within_minutes)`` and skips any source_key
that was scraped recently. After a scraper succeeds, its source_keys are
marked. Consecutive scrape cycles thus continue with whichever sources are
stale instead of re-fetching everything.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

import yaml

from .. import db
from ..models import Job
from .registry import ATS_SCRAPERS, ENDPOINT_SCRAPERS

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sources.yaml"

# Default skip window: don't re-scrape a source if its last successful run
# is within this many minutes. Override via config/sources.yaml under
# `defaults.skip_if_scraped_within_minutes`. Set to 0 to disable skipping.
DEFAULT_SKIP_MINUTES = 15


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def build_scrapers(
    config: dict, fresh_keys: set[str] | None = None
) -> list[tuple[str, object, list[str]]]:
    """Returns a list of (label, scraper_instance, source_keys) ready to run.

    `fresh_keys` is the set of source_keys scraped recently — anything in
    this set is filtered out before constructing scraper instances.
    """
    fresh_keys = fresh_keys or set()
    scrapers: list[tuple[str, object, list[str]]] = []

    # ATS scrapers: take a list of slugs. Filter out slugs whose
    # `kind:slug` source_key was scraped recently.
    for kind, slugs in config.items():
        if kind in ATS_SCRAPERS and isinstance(slugs, list) and slugs:
            stale_slugs = [s for s in slugs if f"{kind}:{s}" not in fresh_keys]
            skipped = len(slugs) - len(stale_slugs)
            if skipped:
                log.info("[%s] skipping %d slug(s) scraped recently", kind, skipped)
            if not stale_slugs:
                continue
            cls = ATS_SCRAPERS[kind]
            keys = [f"{kind}:{s}" for s in stale_slugs]
            scrapers.append((kind, cls(stale_slugs), keys))

    # Endpoint scrapers: keyed solely by `kind`. Skip if recently scraped.
    for kind, opts in config.items():
        if kind in ENDPOINT_SCRAPERS and isinstance(opts, dict):
            if not opts.get("enabled", True):
                continue
            if kind in fresh_keys:
                log.info("[%s] skipping (scraped recently)", kind)
                continue
            cls = ENDPOINT_SCRAPERS[kind]
            scrapers.append((kind, cls(opts), [kind]))

    return scrapers


async def run_all(config: dict | None = None) -> list[Job]:
    """Run every configured scraper concurrently. Returns all jobs collected."""
    config = config or load_config()
    defaults = config.get("defaults") or {}
    concurrency = defaults.get("concurrency", 10)
    skip_minutes = defaults.get("skip_if_scraped_within_minutes", DEFAULT_SKIP_MINUTES)

    fresh_keys = db.get_recently_scraped_keys(skip_minutes) if skip_minutes else set()
    if fresh_keys:
        log.info(
            "skip-recent: %d source_key(s) within %dmin window, will be skipped",
            len(fresh_keys), skip_minutes,
        )
    scrapers = build_scrapers(config, fresh_keys=fresh_keys)
    if not scrapers:
        log.info("nothing to scrape — all sources are within the skip window")
        return []

    sem = asyncio.Semaphore(concurrency)
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=concurrency)

    async def _one(
        label: str, scraper, source_keys: list[str]
    ) -> tuple[str, list[Job], list[str]]:
        async with sem:
            try:
                jobs = await loop.run_in_executor(pool, lambda: list(scraper.scrape()))
                if not jobs:
                    # Don't mark a source as fresh when it returns nothing —
                    # could mask a silent API outage. Retry on the next cycle.
                    log.warning(
                        "[%s] returned 0 jobs (not marking as scraped — will retry)",
                        label,
                    )
                    return label, [], []
                log.info("[%s] %d jobs", label, len(jobs))
                return label, jobs, source_keys
            except Exception as e:
                log.warning("[%s] FAILED: %s", label, e)
                return label, [], []  # don't mark on failure → retry next cycle

    results = await asyncio.gather(
        *(_one(label, s, keys) for label, s, keys in scrapers)
    )
    pool.shutdown(wait=False)

    flat: list[Job] = []
    succeeded_keys: list[str] = []
    for _, jobs, keys in results:
        flat.extend(jobs)
        succeeded_keys.extend(keys)
    if succeeded_keys:
        db.mark_scraped(succeeded_keys)
    return flat


def run_all_sync() -> list[Job]:
    """Sync wrapper for CLI use."""
    return asyncio.run(run_all())
