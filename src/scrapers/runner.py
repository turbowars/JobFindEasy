"""Concurrent scraper runner.

Reads config/sources.yaml, instantiates the right scraper for each entry,
runs them concurrently with a semaphore cap, and returns all yielded Jobs.

The scrapers themselves are sync (they use httpx Client). We run each
scraper's .scrape() in a thread to keep concurrency simple and because the
scrapers internally already loop over slugs/endpoints.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

import yaml

from ..models import Job
from .registry import ATS_SCRAPERS, ENDPOINT_SCRAPERS

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sources.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def build_scrapers(config: dict) -> list[tuple[str, object]]:
    """Returns a list of (label, scraper_instance) ready to run."""
    scrapers: list[tuple[str, object]] = []

    # ATS scrapers: take a list of slugs
    for kind, slugs in config.items():
        if kind in ATS_SCRAPERS and isinstance(slugs, list) and slugs:
            cls = ATS_SCRAPERS[kind]
            scrapers.append((kind, cls(slugs)))

    # Endpoint scrapers: take their own options dict
    for kind, opts in config.items():
        if kind in ENDPOINT_SCRAPERS and isinstance(opts, dict):
            if not opts.get("enabled", True):
                continue
            cls = ENDPOINT_SCRAPERS[kind]
            scrapers.append((kind, cls(opts)))

    return scrapers


async def run_all(config: dict | None = None) -> list[Job]:
    """Run every configured scraper concurrently. Returns all jobs collected."""
    config = config or load_config()
    scrapers = build_scrapers(config)
    concurrency = (config.get("defaults") or {}).get("concurrency", 10)

    sem = asyncio.Semaphore(concurrency)
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=concurrency)

    async def _one(label: str, scraper) -> tuple[str, list[Job]]:
        async with sem:
            try:
                jobs = await loop.run_in_executor(pool, lambda: list(scraper.scrape()))
                log.info("[%s] %d jobs", label, len(jobs))
                return label, jobs
            except Exception as e:
                log.warning("[%s] FAILED: %s", label, e)
                return label, []

    results = await asyncio.gather(*(_one(label, s) for label, s in scrapers))
    pool.shutdown(wait=False)

    flat: list[Job] = []
    for _, jobs in results:
        flat.extend(jobs)
    return flat


def run_all_sync() -> list[Job]:
    """Sync wrapper for CLI use."""
    return asyncio.run(run_all())
