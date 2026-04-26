"""Recruitee scraper.

Public endpoint: https://{slug}.recruitee.com/api/offers/
No auth. Returns all open offers as JSON.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import httpx

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

API = "https://{slug}.recruitee.com/api/offers/"


class RecruiteeScraper(BaseScraper):
    source = "recruitee"

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
                    log.warning("recruitee %s failed: %s", slug, e)

    def _scrape_company(self, client: httpx.Client, slug: str) -> Iterable[Job]:
        r = client.get(API.format(slug=slug))
        if r.status_code == 404:
            log.info("recruitee: %s not found", slug)
            return
        r.raise_for_status()
        data = r.json()
        offers = data.get("offers", [])
        company_display = (offers[0].get("company_name") if offers else None) or slug.replace("-", " ").title()
        for jd in offers:
            yield self._to_job(jd, slug, company_display)

    def _to_job(self, jd: dict, slug: str, company: str) -> Job:
        loc_parts = [jd.get("city"), jd.get("country")]
        location = ", ".join(p for p in loc_parts if p)
        remote = bool(jd.get("remote") or "remote" in location.lower())

        description = self.clean_html(jd.get("description") or "") + "\n\n" + self.clean_html(jd.get("requirements") or "")
        description = description.strip()

        url = jd.get("careers_url") or jd.get("url") or f"https://{slug}.recruitee.com/o/{jd.get('slug')}"

        return Job(
            source=self.source,
            company=company,
            title=jd.get("title", "").strip(),
            location=location or "Not specified",
            url=url,
            description=description,
            posted_at=jd.get("published_at") or jd.get("created_at"),
            remote=remote,
        )
