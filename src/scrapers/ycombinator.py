"""YC Work at a Startup scraper.

workatastartup.com has a public job listing endpoint that returns curated YC
company jobs. This is a backend API used by their own SPA — public but undocumented.

We hit the public listing page and the per-company job feed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

# Public listings endpoint used by their SPA
# Filter to engineering manager roles via role_types parameter
LISTING_API = "https://www.workatastartup.com/api/jobs/search"


class YCWorkAtStartupScraper(BaseScraper):
    source = "ycombinator"

    def __init__(self, opts: dict):
        self.role_types = (opts.get("filters") or {}).get("role_types", ["eng_manager", "manager"])
        self.remote_only = (opts.get("filters") or {}).get("remote", False)
        self.timeout = opts.get("timeout", 30)

    def scrape(self) -> Iterable[Job]:
        # The endpoint shape changes occasionally; we try the most reliable path.
        # Failures here should NOT block other scrapers — caller catches.
        params = {
            "role_types[]": self.role_types,
            "remote": "true" if self.remote_only else "false",
        }
        with httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": "JobIntelAgent/0.2",
                "Accept": "application/json",
            },
        ) as client:
            try:
                r = client.get(LISTING_API, params=params)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log.warning("ycombinator API call failed: %s — endpoint may have changed", e)
                return

            jobs_data = (
                data if isinstance(data, list) else data.get("jobs", []) or data.get("results", [])
            )
            for jd in jobs_data:
                try:
                    yield self._to_job(jd)
                except Exception as e:
                    log.debug("yc job parse failed: %s", e)

    def _to_job(self, jd: dict) -> Job:
        company = (jd.get("company_name") or jd.get("company", {}).get("name") or "").strip()
        title = (jd.get("title") or jd.get("role") or "").strip()
        location = jd.get("location") or jd.get("locations", [""])[0] if jd.get("locations") else ""
        if not location and jd.get("remote"):
            location = "Remote"

        description = jd.get("description") or jd.get("job_description") or ""
        description = self.clean_html(description)

        url = jd.get("url") or jd.get("apply_url") or ""
        if url and not url.startswith("http"):
            url = f"https://www.workatastartup.com{url}"

        salary_min = jd.get("salary_min") or jd.get("min_salary")
        salary_max = jd.get("salary_max") or jd.get("max_salary")
        try:
            salary_min = int(salary_min) if salary_min else None
            salary_max = int(salary_max) if salary_max else None
        except Exception:
            salary_min, salary_max = None, None

        return Job(
            source=self.source,
            company=company or "Unknown (YC)",
            title=title,
            location=location or "Not specified",
            url=url,
            description=description,
            posted_at=jd.get("created_at") or jd.get("posted_at"),
            salary_min=salary_min,
            salary_max=salary_max,
            remote=bool(jd.get("remote")) or "remote" in (location or "").lower(),
        )
