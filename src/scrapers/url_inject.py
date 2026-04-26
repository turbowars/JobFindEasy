"""Inject a single job posting from an arbitrary URL.

The user pastes a link (any ATS, careers page, LinkedIn, etc.) and we:
  1. Fetch the HTML
  2. Strip to plain text
  3. Hand it to Haiku to extract {title, company, location, description, ...}
  4. Build a Job and upsert it (source="manual")

Returning a Job lets the caller run the normal prefilter/score path on it.
"""
from __future__ import annotations

import html as html_mod
import json
import logging
import os
import re
from typing import Optional

import httpx

from ..llm import chat
from ..models import Job

log = logging.getLogger(__name__)

_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NEWLINES_RE = re.compile(r"\n{3,}")

# Workday postings (*.myworkdayjobs.com) are SPAs — the HTML shell has no
# job text. But Workday exposes a public JSON endpoint for every posting.
# Translate the user-facing URL to the API URL before fetching.
_WORKDAY_RE = re.compile(
    r"^(https?://([^./]+)\.[^/]*myworkdayjobs\.com)"
    r"/(?:[a-zA-Z]{2}-[A-Z]{2}/)?([^/]+)/job/(.+?)(?:\?.*)?$"
)


def _maybe_workday_api_url(url: str) -> Optional[str]:
    m = _WORKDAY_RE.match(url)
    if not m:
        return None
    base, tenant, site, job_path = m.group(1), m.group(2), m.group(3), m.group(4)
    return f"{base}/wday/cxs/{tenant}/{site}/job/{job_path}"

_EXTRACT_SYSTEM = """You are extracting structured fields from a job posting page.

Return ONLY a single JSON object, no markdown fences:
{
  "title": "string",
  "company": "string",
  "location": "string",
  "description": "string (multi-paragraph plain text)",
  "remote": true | false | null,
  "salary_min": int | null,
  "salary_max": int | null
}

RULES
- title and company should be the canonical posted values, no decorations.
- location: city/region (e.g. "San Francisco, CA"), or "Remote", or both ("Remote — US"). Empty string if truly unknown.
- description: preserve paragraph structure with \\n\\n between sections. Include responsibilities, requirements, perks, comp band if present. Skip cookie banners, navigation, footer text.
- remote: true if explicitly remote-eligible; false if explicitly on-site / hybrid-only. null if unclear.
- salary_min / salary_max: integers in USD if a range is published. null otherwise. Do not invent.
- If the page is clearly NOT a job posting (404, login wall, generic careers home), return all fields empty / null."""


def _strip_html(html: str) -> str:
    s = _SCRIPT_RE.sub(" ", html)
    s = _TAG_RE.sub(" ", s)
    s = html_mod.unescape(s)
    s = _WS_RE.sub(" ", s)
    s = _NEWLINES_RE.sub("\n\n", s)
    lines = [ln.strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def _coerce_int(v) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    return None


def inject_from_url(url: str, model: Optional[str] = None) -> tuple[Optional[Job], str]:
    """Fetch URL, extract a Job via Haiku.

    Returns (Job, "ok") on success, (None, error_msg) on failure.
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return None, "URL must start with http:// or https://"

    api_url = _maybe_workday_api_url(url)
    fetch_url = api_url or url
    accept = "application/json" if api_url else "text/html,application/xhtml+xml"

    try:
        r = httpx.get(
            fetch_url,
            timeout=20.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0.0.0 Safari/537.36",
                "Accept": accept,
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        return None, f"fetch failed: HTTP {e.response.status_code}"
    except Exception as e:
        return None, f"fetch failed: {e}"

    raw_body = r.text
    # Workday JSON contains HTML inside `jobDescription` — strip tags from
    # the whole body so the model sees clean text either way.
    text = _strip_html(raw_body)[:18000]
    if len(text) < 200:
        return None, "page contained too little text — likely a login wall or JS-rendered page"

    model = model or os.environ.get("SCORING_MODEL", "anthropic/claude-haiku-4.5")
    user_msg = f"URL: {url}\n\nPAGE TEXT:\n{text}\n\nReturn the JSON now."
    try:
        raw = chat(system=_EXTRACT_SYSTEM, user=user_msg, model=model, max_tokens=2000)
    except Exception as e:
        return None, f"extract failed: {e}"

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        d = json.loads(raw)
    except Exception as e:
        log.warning("inject_from_url JSON decode failed: %s | text=%s", e, raw[:200])
        return None, "could not parse extraction response"

    title = (d.get("title") or "").strip()
    company = (d.get("company") or "").strip()
    if not title or not company:
        return None, "couldn't identify title or company on the page"

    job = Job(
        source="manual",
        company=company,
        title=title,
        location=(d.get("location") or "").strip(),
        url=url,
        description=(d.get("description") or "").strip(),
        remote=d["remote"] if isinstance(d.get("remote"), bool) else None,
        salary_min=_coerce_int(d.get("salary_min")),
        salary_max=_coerce_int(d.get("salary_max")),
    )
    return job, "ok"
