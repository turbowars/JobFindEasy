"""AI-Jobs.net scraper via RSS feed.

The RSS endpoint is https://aijobs.net/feed/
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import feedparser

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

FEED_URL = "https://aijobs.net/feed/"


class AIJobsScraper(BaseScraper):
    source = "aijobs"

    def __init__(self, opts: dict):
        self.feed_url = opts.get("feed_url", FEED_URL)

    def scrape(self) -> Iterable[Job]:
        try:
            feed = feedparser.parse(self.feed_url)
        except Exception as e:
            log.warning("aijobs RSS parse failed: %s", e)
            return

        for entry in feed.entries:
            try:
                yield self._to_job(entry)
            except Exception as e:
                log.debug("aijobs entry parse failed: %s", e)

    def _to_job(self, entry) -> Job:
        title = (entry.get("title") or "").strip()
        # AI-Jobs RSS titles often look like "Senior ML Engineer @ Anthropic"
        # Try to extract company from title
        company = ""
        if " @ " in title:
            title, company = [p.strip() for p in title.rsplit(" @ ", 1)]
        elif " at " in title and title.count(" at ") == 1:
            title, company = [p.strip() for p in title.rsplit(" at ", 1)]

        description = self.clean_html(entry.get("summary") or entry.get("description") or "")
        url = entry.get("link", "")

        # Tags often include location
        location = ""
        for tag in entry.get("tags", []):
            term = tag.get("term") or ""
            if any(
                loc in term.lower() for loc in ("remote", "us", "usa", "united states", "europe")
            ):
                location = term
                break

        remote = "remote" in (location + " " + description).lower()

        return Job(
            source=self.source,
            company=company or "Unknown (aijobs)",
            title=title,
            location=location or "See description",
            url=url,
            description=description,
            posted_at=entry.get("published") or entry.get("updated"),
            remote=remote,
        )
