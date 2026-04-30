"""Lever public postings scraper.

Uses: https://api.lever.co/v0/postings/{company}?mode=json
No auth required.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime

import httpx

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

API = "https://api.lever.co/v0/postings/{slug}?mode=json"


class LeverScraper(BaseScraper):
    source = "lever"

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
                    log.warning("lever %s failed: %s", slug, e)

    def _scrape_company(self, client: httpx.Client, slug: str) -> Iterable[Job]:
        r = client.get(API.format(slug=slug))
        if r.status_code == 404:
            log.info("lever: %s board not found", slug)
            return
        r.raise_for_status()
        for jd in r.json():
            yield self._to_job(jd, slug)

    def _to_job(self, jd: dict, slug: str) -> Job:
        categories = jd.get("categories") or {}
        location = categories.get("location", "") or ""
        all_locations = jd.get("allLocations") or []
        if all_locations and len(all_locations) > 1:
            location = ", ".join(all_locations[:3])

        commitment = categories.get("commitment", "")

        # Lever returns descriptionPlain and lists. Stitch them.
        parts = [jd.get("descriptionPlain", "") or ""]
        for lst in jd.get("lists", []):
            parts.append(lst.get("text", ""))
            content = lst.get("content", "")
            if content:
                parts.append(self.clean_html(content))
        parts.append(jd.get("additionalPlain", "") or "")
        description = "\n\n".join(p for p in parts if p)

        # createdAt is epoch ms
        posted_at = None
        if jd.get("createdAt"):
            try:
                posted_at = datetime.fromtimestamp(jd["createdAt"] / 1000, tz=UTC).isoformat()
            except Exception:
                pass

        loc_lower = (location + " " + " ".join(all_locations)).lower()
        remote = "remote" in loc_lower or "anywhere" in loc_lower or commitment.lower() == "remote"

        salary_min, salary_max = self._extract_salary(jd.get("salaryRange") or {})

        company_display = slug.replace("-", " ").title()

        return Job(
            source=self.source,
            company=company_display,
            title=jd.get("text", "").strip(),
            location=location or "Not specified",
            url=jd.get("hostedUrl", ""),
            description=description,
            posted_at=posted_at,
            salary_min=salary_min,
            salary_max=salary_max,
            remote=remote,
        )

    @staticmethod
    def _extract_salary(sr: dict) -> tuple[int | None, int | None]:
        if not sr:
            return None, None
        try:
            return int(sr.get("min") or 0) or None, int(sr.get("max") or 0) or None
        except Exception:
            return None, None
