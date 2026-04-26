"""Notification module.

Default: macOS osascript (no deps). Optional: Pushover for mobile push.
Fall through to terminal print if neither is configured.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
from typing import Iterable

import httpx

log = logging.getLogger(__name__)


def notify_macos(title: str, body: str) -> bool:
    try:
        script = f'display notification {shlex.quote(body)} with title {shlex.quote(title)}'
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=5)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def notify_pushover(title: str, body: str) -> bool:
    token = os.environ.get("PUSHOVER_TOKEN")
    user = os.environ.get("PUSHOVER_USER")
    if not (token and user):
        return False
    try:
        with httpx.Client(timeout=10) as c:
            r = c.post(
                "https://api.pushover.net/1/messages.json",
                data={"token": token, "user": user, "title": title, "message": body},
            )
            return r.status_code == 200
    except Exception as e:
        log.warning("pushover failed: %s", e)
        return False


def notify_strong_fits(jobs: list[dict]) -> None:
    """Notify on the daily top fits. jobs = list of dict rows from db.get_strong_fits_today()."""
    if not jobs:
        return

    n = len(jobs)
    title = f"Job Intel: {n} strong fit{'s' if n != 1 else ''} today"
    top = jobs[:3]
    body_lines = [f"{j['title']} at {j['company']} ({j['score_total']})" for j in top]
    if n > 3:
        body_lines.append(f"+ {n - 3} more in dashboard")
    body = "\n".join(body_lines)

    pushed = notify_pushover(title, body)
    if not pushed:
        notify_macos(title, body)
    print(f"\n{title}")
    for line in body_lines:
        print(f"  - {line}")
