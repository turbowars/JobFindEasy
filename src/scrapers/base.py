"""Base scraper with common helpers."""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Iterable

from ..models import Job

log = logging.getLogger(__name__)


class BaseScraper(ABC):
    source: str = "base"

    @abstractmethod
    def scrape(self) -> Iterable[Job]:
        """Yield Job objects."""
        ...

    @staticmethod
    def clean_html(html: str) -> str:
        """Strip HTML tags and collapse whitespace. Good enough for JD parsing."""
        if not html:
            return ""
        # Remove script/style blocks
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        # Replace block-level closes with newlines so paragraphs survive
        html = re.sub(r"</(p|div|li|h[1-6]|br)>", "\n", html, flags=re.IGNORECASE)
        # Strip remaining tags
        html = re.sub(r"<[^>]+>", "", html)
        # Decode common entities
        html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"')
        # Collapse whitespace
        html = re.sub(r"[ \t]+", " ", html)
        html = re.sub(r"\n{3,}", "\n\n", html)
        return html.strip()
