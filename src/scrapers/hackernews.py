"""Hacker News 'Who is hiring?' monthly thread scraper.

Strategy:
  1. Find the most recent 'Ask HN: Who is hiring?' thread via Algolia HN Search API.
  2. Pull the thread's top-level comments (each is a job posting from a founder/HM).
  3. Filter by keyword (engineering manager, frontend lead, etc.).
  4. Convert each matching comment into a Job record.

These are founder-posted, no recruiter spam, and often pre-ATS.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime

import httpx

from ..models import Job
from .base import BaseScraper

log = logging.getLogger(__name__)

ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
HN_LINK_BASE = "https://news.ycombinator.com/item?id="


class HackerNewsScraper(BaseScraper):
    source = "hackernews"

    def __init__(self, opts: dict):
        self.auto = opts.get("auto", True)
        self.thread_id = opts.get("thread_id")  # override if you want a specific month
        self.keyword_filters = [k.lower() for k in opts.get("keyword_filters", [])]
        self.timeout = opts.get("timeout", 30)
        self.max_comments = opts.get("max_comments", 500)

    def scrape(self) -> Iterable[Job]:
        with httpx.Client(
            timeout=self.timeout, headers={"User-Agent": "JobIntelAgent/0.2"}
        ) as client:
            thread_id = self.thread_id or self._find_latest_thread(client)
            if not thread_id:
                log.warning("hackernews: could not locate Who's Hiring thread")
                return
            log.info("hackernews: using thread %s", thread_id)
            thread = self._get_item(client, thread_id)
            kids = thread.get("kids", [])[: self.max_comments]
            for kid_id in kids:
                comment = self._get_item(client, kid_id)
                if not comment or comment.get("deleted") or comment.get("dead"):
                    continue
                text = self.clean_html(comment.get("text", ""))
                if not text or not self._matches_keywords(text):
                    continue
                yield self._to_job(comment, text, thread_id)

    def _find_latest_thread(self, client: httpx.Client) -> int | None:
        """Search for the most recent 'who is hiring' Ask HN thread by user 'whoishiring'."""
        params = {
            "query": "Ask HN: Who is hiring?",
            "tags": "story,author_whoishiring",
            "hitsPerPage": 5,
        }
        r = client.get(ALGOLIA_SEARCH, params=params)
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            return None
        # Pick the most recent one
        hits.sort(key=lambda h: h.get("created_at_i", 0), reverse=True)
        try:
            return int(hits[0]["objectID"])
        except (KeyError, ValueError):
            return None

    def _get_item(self, client: httpx.Client, item_id: int) -> dict:
        try:
            r = client.get(HN_ITEM.format(id=item_id))
            r.raise_for_status()
            return r.json() or {}
        except Exception as e:
            log.debug("hn item %s failed: %s", item_id, e)
            return {}

    def _matches_keywords(self, text: str) -> bool:
        if not self.keyword_filters:
            return True
        lower = text.lower()
        return any(k in lower for k in self.keyword_filters)

    def _to_job(self, comment: dict, text: str, thread_id: int) -> Job:
        # First line of HN job posts is conventionally: COMPANY | TITLE | LOCATION | REMOTE
        first_line = text.split("\n", 1)[0].strip()
        company, title, location, remote = self._parse_header(first_line)

        posted_at = None
        if comment.get("time"):
            try:
                posted_at = datetime.fromtimestamp(comment["time"], tz=UTC).isoformat()
            except Exception:
                pass

        return Job(
            source=self.source,
            company=company or "Unknown (HN)",
            title=title or first_line[:80],
            location=location or "See description",
            url=f"{HN_LINK_BASE}{comment.get('id')}",
            description=text,
            posted_at=posted_at,
            remote=remote,
        )

    @staticmethod
    def _parse_header(line: str) -> tuple[str, str, str, bool]:
        """Best-effort parse of '<Company> | <Title> | <Location> | REMOTE | ...'"""
        parts = [p.strip(" |·-") for p in re.split(r"\s*[|·]\s*", line) if p.strip()]
        company = parts[0] if parts else ""
        title = parts[1] if len(parts) > 1 else ""
        location = parts[2] if len(parts) > 2 else ""
        remote = any("remote" in p.lower() for p in parts)
        return company, title, location, remote
