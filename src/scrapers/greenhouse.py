"""Greenhouse public job board scraper.

Uses the public Boards API: https://developers.greenhouse.io/job-board.html
No auth required, no rate limits beyond reasonable use.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import httpx

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


class GreenhouseScraper(BaseScraper):
    source = "greenhouse"

    def __init__(self, slugs: list[str], timeout: float = 30.0):
        self.slugs = slugs
        self.timeout = timeout

    def scrape(self) -> Iterable[Job]:
        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "JobIntelAgent/0.1"}) as client:
            for slug in self.slugs:
                try:
                    yield from self._scrape_company(client, slug)
                    time.sleep(0.5)  # be polite
                except Exception as e:
                    log.warning("greenhouse %s failed: %s", slug, e)

    def _scrape_company(self, client: httpx.Client, slug: str) -> Iterable[Job]:
        r = client.get(API.format(slug=slug))
        if r.status_code == 404:
            log.info("greenhouse: %s board not found", slug)
            return
        r.raise_for_status()
        data = r.json()
        company_display = self._guess_company_name(slug, data)
        for jd in data.get("jobs", []):
            yield self._to_job(jd, company_display, slug)

    def _guess_company_name(self, slug: str, data: dict) -> str:
        # Greenhouse doesn't return company name in this endpoint; fall back to slug
        # but title-case it ("airbnb" -> "Airbnb")
        return slug.replace("-", " ").title()

    def _to_job(self, jd: dict, company: str, slug: str) -> Job:
        location = (jd.get("location") or {}).get("name", "")
        offices = jd.get("offices") or []
        if not location and offices:
            location = ", ".join(o.get("name", "") for o in offices if o.get("name"))

        url = jd.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{jd.get('id')}"
        description = self.clean_html(jd.get("content", ""))

        # Heuristic remote flag
        loc_lower = location.lower()
        remote = "remote" in loc_lower or "anywhere" in loc_lower

        # Pull pay range if Greenhouse exposes it (some companies use pay_input_ranges)
        salary_min, salary_max = self._extract_salary(jd)

        return Job(
            source=self.source,
            company=company,
            title=jd.get("title", "").strip(),
            location=location or "Not specified",
            url=url,
            description=description,
            posted_at=jd.get("updated_at"),
            salary_min=salary_min,
            salary_max=salary_max,
            remote=remote,
        )

    @staticmethod
    def _extract_salary(jd: dict) -> tuple[int | None, int | None]:
        ranges = jd.get("pay_input_ranges") or []
        if ranges:
            try:
                r0 = ranges[0]
                lo = int(r0.get("min_cents", 0)) // 100
                hi = int(r0.get("max_cents", 0)) // 100
                if lo > 0 and hi > 0:
                    return lo, hi
            except Exception:
                pass
        return None, None
