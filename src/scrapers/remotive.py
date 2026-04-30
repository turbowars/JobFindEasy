"""Remotive scraper.

Public JSON API: https://remotive.com/api/remote-jobs?category=software-dev
No auth, very stable. Returns ~100-200 jobs across categories.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

API = "https://remotive.com/api/remote-jobs"


class RemotiveScraper(BaseScraper):
    source = "remotive"

    def __init__(self, opts: dict):
        self.categories = opts.get("categories") or ["software-dev"]
        self.timeout = opts.get("timeout", 30)

    def scrape(self) -> Iterable[Job]:
        with httpx.Client(
            timeout=self.timeout, headers={"User-Agent": "JobIntelAgent/0.2"}
        ) as client:
            for cat in self.categories:
                try:
                    yield from self._scrape_category(client, cat)
                except Exception as e:
                    log.warning("remotive %s failed: %s", cat, e)

    def _scrape_category(self, client: httpx.Client, category: str) -> Iterable[Job]:
        r = client.get(API, params={"category": category})
        r.raise_for_status()
        data = r.json()
        for jd in data.get("jobs", []):
            yield self._to_job(jd)

    def _to_job(self, jd: dict) -> Job:
        title = (jd.get("title") or "").strip()
        company = (jd.get("company_name") or "").strip()
        location = jd.get("candidate_required_location") or "Remote"
        description = self.clean_html(jd.get("description") or "")

        # Salary often comes as a free-text string ("$120k - $180k") — leave structured slots empty
        return Job(
            source=self.source,
            company=company,
            title=title,
            location=location,
            url=jd.get("url", ""),
            description=description,
            posted_at=jd.get("publication_date"),
            salary_min=None,
            salary_max=None,
            remote=True,  # everything on Remotive is remote by definition
        )
