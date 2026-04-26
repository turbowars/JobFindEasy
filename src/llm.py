"""OpenRouter chat client (OpenAI-compatible)."""
from __future__ import annotations

import os

import httpx

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def chat(system: str, user: str, model: str, max_tokens: int = 1024, timeout: float = 60.0) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in environment")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{OPENROUTER_BASE}/chat/completions", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"]
