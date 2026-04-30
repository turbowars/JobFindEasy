"""OpenRouter chat client (OpenAI-compatible) with optional prompt caching.

When `cache_system=True` is passed, the system message is sent as a content
array with a `cache_control: ephemeral` breakpoint, which OpenRouter forwards
to Anthropic for prompt caching.

Caching savings (Anthropic ephemeral, 5-min TTL):
  - Sonnet:  cached input ≈ 10% of base price (saves ~40% on a typical call
             where the static prompt is the dominant cost)
  - Haiku:   cached input ≈ 10% of base price (saves ~67% on scoring calls
             where the rubric dominates the input)

Minimum cacheable prompt size:
  - Sonnet/Opus: 1024 tokens
  - Haiku:       2048 tokens
Below those, Anthropic silently does not cache; the call still succeeds.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Default model used by every "scoring-class" task when no per-role override
# is set. Per-role env vars are checked first, then SCORING_MODEL, then this.
_SCORING_DEFAULT = "anthropic/claude-haiku-4.5"


def get_model(role: str) -> str:
    """Resolve which model to use for a given task role.

    Resolution order:
      1. Role-specific env var: `<ROLE>_MODEL` (e.g. `JOB_SCORING_MODEL`,
         `ATS_EXTRACT_MODEL`, `HR_SIM_MODEL`, `URL_EXTRACT_MODEL`).
      2. Shared `SCORING_MODEL` env var (legacy fallback so existing setups
         keep working when the per-role var is unset).
      3. Built-in default (`anthropic/claude-haiku-4.5`).

    Generation (resume / cover letter) does NOT route through here — it has
    its own `GENERATION_MODEL` resolved at the call site.
    """
    var_name = f"{role.upper()}_MODEL"
    return os.environ.get(var_name) or os.environ.get("SCORING_MODEL") or _SCORING_DEFAULT


def chat(
    system: str,
    user: str,
    model: str,
    max_tokens: int = 1024,
    timeout: float = 60.0,
    cache_system: bool = False,
) -> str:
    """Send a single-turn chat completion to OpenRouter and return the text.

    Args:
        system: System prompt. Sent as a string by default; sent as a content
            array with a cache_control breakpoint when `cache_system=True`.
        user: User message text.
        model: OpenRouter model id (e.g. ``anthropic/claude-sonnet-4.5``).
        max_tokens: Output token cap.
        timeout: HTTP timeout in seconds.
        cache_system: If True, mark the system message as a prompt-cache
            breakpoint (OpenRouter forwards `cache_control: ephemeral` to
            Anthropic). Pays a small write penalty on the first call within a
            5-minute window; subsequent calls with the same system prompt are
            ~10x cheaper on the cached portion.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in environment")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # OpenRouter uses these headers to label the calling app in its
        # dashboard and analytics. Without them, calls show up as "Unknown".
        "HTTP-Referer": "http://127.0.0.1:8826",
        "X-Title": "JobFindEasy",
    }

    if cache_system:
        system_content = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        system_content = system

    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user},
        ],
    }
    # Pin provider to Anthropic when caching: OpenRouter's default routing
    # often picks Amazon Bedrock for Anthropic models, but Bedrock does NOT
    # pass through `cache_control` reliably — caching is silently dropped.
    # Anthropic direct works.
    if cache_system:
        payload["provider"] = {"order": ["Anthropic"], "allow_fallbacks": True}
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{OPENROUTER_BASE}/chat/completions", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    if cache_system:
        usage = data.get("usage") or {}
        # OpenRouter exposes Anthropic cache stats under `prompt_tokens_details`.
        details = usage.get("prompt_tokens_details") or {}
        read = details.get("cached_tokens", 0) or 0
        write = details.get("cache_write_tokens", 0) or 0
        if read or write:
            log.info(
                "[llm.chat cache] model=%s provider=%s prompt=%s cached_read=%s cache_write=%s",
                model,
                data.get("provider", "?"),
                usage.get("prompt_tokens"),
                read,
                write,
            )

    return data["choices"][0]["message"]["content"]
