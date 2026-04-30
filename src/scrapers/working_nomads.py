"""Working Nomads RSS scraper.

Endpoint pattern: https://www.workingnomads.com/jobsrss?category={cat}
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import feedparser

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

FEED_URL = "https://www.workingnomads.com/jobsrss"


class WorkingNomadsScraper(BaseScraper):
    source = "working_nomads"

    def __init__(self, opts: dict):
        self.categories = opts.get("categories") or ["development", "management"]

    def scrape(self) -> Iterable[Job]:
        for cat in self.categories:
            try:
                feed = feedparser.parse(f"{FEED_URL}?category={cat}")
                for entry in feed.entries:
                    try:
                        yield self._to_job(entry, cat)
                    except Exception as e:
                        log.debug("working_nomads entry parse failed: %s", e)
            except Exception as e:
                log.warning("working_nomads %s failed: %s", cat, e)

    def _to_job(self, entry, category: str) -> Job:
        title = (entry.get("title") or "").strip()
        # Working Nomads titles: "Senior Engineer at Acme Corp"
        company = ""
        if " at " in title:
            title, company = [p.strip() for p in title.rsplit(" at ", 1)]

        description = self.clean_html(entry.get("summary") or entry.get("description") or "")
        url = entry.get("link", "")

        return Job(
            source=self.source,
            company=company or "Unknown (working_nomads)",
            title=title,
            location="Remote",
            url=url,
            description=description,
            posted_at=entry.get("published") or entry.get("updated"),
            remote=True,
        )
