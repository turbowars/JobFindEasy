"""Jobspresso RSS scraper.

Endpoint: https://jobspresso.co/jobs/feed/
Curated remote-tech postings. The `author` field encodes "Company<br>⚲ Location",
the `title` is the role only.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Iterable

import feedparser

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

FEED_URL = "https://jobspresso.co/jobs/feed/"
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


class JobspressoScraper(BaseScraper):
    source = "jobspresso"

    def __init__(self, opts: dict):
        self.enabled = opts.get("enabled", True)

    def scrape(self) -> Iterable[Job]:
        if not self.enabled:
            return
        try:
            feed = feedparser.parse(FEED_URL)
        except Exception as e:
            log.warning("jobspresso feed fetch failed: %s", e)
            return

        for entry in feed.entries:
            try:
                yield self._to_job(entry)
            except Exception as e:
                log.debug("jobspresso entry parse failed: %s", e)

    def _to_job(self, entry) -> Job:
        title = (entry.get("title") or "").strip()
        company, location = _parse_author(entry.get("author") or "")

        description = self.clean_html(entry.get("summary") or entry.get("description") or "")
        url = entry.get("link", "")
        is_remote = "remote" in location.lower() or "anywhere" in location.lower() or not location

        return Job(
            source=self.source,
            company=company or "Unknown (jobspresso)",
            title=title,
            location=location or "Remote",
            url=url,
            description=description,
            posted_at=entry.get("published") or entry.get("updated"),
            remote=is_remote,
        )


def _parse_author(raw: str) -> tuple[str, str]:
    """`Company<br>⚲&nbsp;Location` → ('Company', 'Location').
    Falls back gracefully if the format changes.
    """
    if not raw:
        return "", ""
    parts = _BR_RE.split(raw, maxsplit=1)
    company = _clean(parts[0])
    location = _clean(parts[1]) if len(parts) > 1 else ""
    # Strip leading pin emoji / non-letters from location
    location = re.sub(r"^[^\w]+", "", location).strip()
    return company, location


def _clean(s: str) -> str:
    s = html.unescape(s)
    s = _TAG_RE.sub("", s)
    return s.strip()
