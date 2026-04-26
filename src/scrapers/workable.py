"""Workable scraper.

Public widget endpoint: https://apply.workable.com/api/v1/widget/accounts/{slug}
No auth. Returns all open jobs.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import httpx

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

API = "https://apply.workable.com/api/v1/widget/accounts/{slug}"


class WorkableScraper(BaseScraper):
    source = "workable"

    def __init__(self, slugs: list[str], timeout: float = 30.0):
        self.slugs = slugs
        self.timeout = timeout

    def scrape(self) -> Iterable[Job]:
        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "JobIntelAgent/0.2"}) as client:
            for slug in self.slugs:
                try:
                    yield from self._scrape_company(client, slug)
                    time.sleep(0.5)
                except Exception as e:
                    log.warning("workable %s failed: %s", slug, e)

    def _scrape_company(self, client: httpx.Client, slug: str) -> Iterable[Job]:
        r = client.get(API.format(slug=slug))
        if r.status_code == 404:
            log.info("workable: %s not found", slug)
            return
        r.raise_for_status()
        data = r.json()
        company_display = data.get("name") or slug.replace("-", " ").title()
        for jd in data.get("jobs", []):
            yield self._to_job(jd, slug, company_display)

    def _to_job(self, jd: dict, slug: str, company: str) -> Job:
        location = jd.get("location", {}) or {}
        loc_str = ", ".join(filter(None, [location.get("city"), location.get("region"), location.get("country")]))
        remote = bool(jd.get("remote") or jd.get("workplace") == "remote" or "remote" in loc_str.lower())

        # Workable description is in jd['description'] or jd['full_description']
        description = self.clean_html(jd.get("description") or jd.get("full_description") or "")

        url = jd.get("url") or jd.get("application_url") or f"https://apply.workable.com/{slug}/j/{jd.get('shortcode')}/"

        return Job(
            source=self.source,
            company=company,
            title=jd.get("title", "").strip(),
            location=loc_str or "Not specified",
            url=url,
            description=description,
            posted_at=jd.get("published_on") or jd.get("created_at"),
            remote=remote,
        )
