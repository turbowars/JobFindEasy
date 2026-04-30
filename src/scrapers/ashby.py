"""Ashby public job board scraper.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
No auth required.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable

import httpx

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


class AshbyScraper(BaseScraper):
    source = "ashby"

    def __init__(self, slugs: list[str], timeout: float = 30.0):
        self.slugs = slugs
        self.timeout = timeout

    def scrape(self) -> Iterable[Job]:
        with httpx.Client(
            timeout=self.timeout, headers={"User-Agent": "JobIntelAgent/0.1"}
        ) as client:
            for slug in self.slugs:
                try:
                    yield from self._scrape_company(client, slug)
                    time.sleep(0.5)
                except Exception as e:
                    log.warning("ashby %s failed: %s", slug, e)

    def _scrape_company(self, client: httpx.Client, slug: str) -> Iterable[Job]:
        r = client.get(API.format(slug=slug))
        if r.status_code == 404:
            log.info("ashby: %s board not found", slug)
            return
        r.raise_for_status()
        data = r.json()
        company_display = data.get("apiVersion") and slug or slug
        company_display = slug.replace("-", " ").title()
        for jd in data.get("jobs", []):
            yield self._to_job(jd, slug, company_display)

    def _to_job(self, jd: dict, slug: str, company: str) -> Job:
        location = jd.get("location", "") or ""
        secondary = jd.get("secondaryLocations") or []
        if secondary:
            extra = ", ".join(s.get("location", "") for s in secondary if s.get("location"))
            if extra:
                location = f"{location}, {extra}" if location else extra

        # Ashby returns descriptionHtml, descriptionPlain
        description = jd.get("descriptionPlain") or self.clean_html(jd.get("descriptionHtml", ""))

        loc_lower = location.lower()
        remote = jd.get("isRemote", False) or "remote" in loc_lower

        salary_min, salary_max = self._extract_salary(jd.get("compensation") or {})

        return Job(
            source=self.source,
            company=company,
            title=jd.get("title", "").strip(),
            location=location or "Not specified",
            url=jd.get("jobUrl", "") or f"https://jobs.ashbyhq.com/{slug}",
            description=description,
            posted_at=jd.get("publishedAt") or jd.get("updatedAt"),
            salary_min=salary_min,
            salary_max=salary_max,
            remote=remote,
        )

    @staticmethod
    def _extract_salary(comp: dict) -> tuple[int | None, int | None]:
        # Ashby sometimes returns compensationTierSummary as a string ("$180k–$220k")
        # and sometimes structured tiers. Parse what we can.
        if not comp:
            return None, None
        tiers = comp.get("compensationTiers") or []
        for t in tiers:
            cs = t.get("componentSummary") or []
            for c in cs:
                if c.get("compensationType") == "Salary":
                    try:
                        return int(c.get("minValue") or 0) or None, int(
                            c.get("maxValue") or 0
                        ) or None
                    except Exception:
                        pass
        return None, None
