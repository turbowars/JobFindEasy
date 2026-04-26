"""SmartRecruiters scraper.

Public API: https://api.smartrecruiters.com/v1/companies/{slug}/postings
No auth. Returns paginated postings with separate detail endpoint for full JD.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import httpx

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

LIST_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"


class SmartRecruitersScraper(BaseScraper):
    source = "smartrecruiters"

    def __init__(self, slugs: list[str], timeout: float = 30.0, fetch_descriptions: bool = True):
        self.slugs = slugs
        self.timeout = timeout
        self.fetch_descriptions = fetch_descriptions

    def scrape(self) -> Iterable[Job]:
        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "JobIntelAgent/0.2"}) as client:
            for slug in self.slugs:
                try:
                    yield from self._scrape_company(client, slug)
                    time.sleep(0.5)
                except Exception as e:
                    log.warning("smartrecruiters %s failed: %s", slug, e)

    def _scrape_company(self, client: httpx.Client, slug: str) -> Iterable[Job]:
        # Paginate
        offset = 0
        page_size = 100
        while True:
            r = client.get(LIST_API.format(slug=slug), params={"offset": offset, "limit": page_size})
            if r.status_code == 404:
                log.info("smartrecruiters: %s not found", slug)
                return
            r.raise_for_status()
            data = r.json()
            content = data.get("content", [])
            for jd in content:
                yield self._to_job(client, slug, jd)
            total_found = data.get("totalFound", 0)
            offset += page_size
            if offset >= total_found or not content:
                break

    def _to_job(self, client: httpx.Client, slug: str, jd: dict) -> Job:
        location = (jd.get("location") or {}).get("city", "")
        country = (jd.get("location") or {}).get("country", "")
        if country and location:
            location = f"{location}, {country}"
        elif country:
            location = country

        remote = bool((jd.get("location") or {}).get("remote", False))
        if not remote and "remote" in location.lower():
            remote = True

        company_display = slug.replace("-", " ").title()

        # Optionally fetch full description
        description = ""
        if self.fetch_descriptions:
            try:
                pid = jd.get("id")
                d = client.get(DETAIL_API.format(slug=slug, posting_id=pid)).json()
                sections = d.get("jobAd", {}).get("sections", {}) or {}
                parts = []
                for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
                    section = sections.get(key) or {}
                    if section.get("text"):
                        parts.append(self.clean_html(section["text"]))
                description = "\n\n".join(parts)
            except Exception as e:
                log.debug("smartrecruiters detail fetch failed: %s", e)

        return Job(
            source=self.source,
            company=company_display,
            title=jd.get("name", "").strip(),
            location=location or "Not specified",
            url=(jd.get("ref") or "").replace("api.", "jobs.").replace("/v1/companies", "").replace("/postings/", "/" + slug + "/jobs/") or f"https://jobs.smartrecruiters.com/{slug}/{jd.get('id')}",
            description=description,
            posted_at=jd.get("releasedDate"),
            remote=remote,
        )
