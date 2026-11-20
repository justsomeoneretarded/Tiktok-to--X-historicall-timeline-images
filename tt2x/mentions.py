"""Who a post is about: Gemini reads the caption + slides and suggests X handles to @mention."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
from PIL import Image

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRIES = 4
RETRY_WAIT = 10  # seconds, grows each try: 10, 20, 30
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

PROMPT = """These images are the slides, in order, of a social media post. Its caption is:
\"\"\"{caption}\"\"\"

List the real people, companies and organizations this post is mainly ABOUT, most important first
(at most {limit}). Skip anyone only mentioned in passing, and skip the post's creator.
For each, give their official X (Twitter) handle without the @, only if you are confident it is the
official account; otherwise use null. Never invent handles."""

SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {"name": {"type": "STRING"}, "x_handle": {"type": "STRING", "nullable": True}},
        "required": ["name"],
    },
}


def clean_handle(h) -> str | None:
    if not isinstance(h, str):
        return None
    h = h.strip().lstrip("@").strip()
    return h if HANDLE_RE.match(h) else None


def parse_suggestions(data, limit: int) -> list[str]:
    """Gemini JSON -> ordered, unique, syntactically valid handles."""
    out: list[str] = []
    for item in data if isinstance(data, list) else []:
        h = clean_handle(item.get("x_handle")) if isinstance(item, dict) else None
        if h and h.lower() not in {o.lower() for o in out}:
            out.append(h)
        if len(out) >= limit:
            break
    return out


def _sample(items: list, n: int) -> list:
    """At most n items spread evenly from first to last (keeps the title and the end)."""
    if len(items) <= n:
        return list(items)
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


def _image_part(path: Path) -> dict:
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((384, 384))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}


def suggest_handles(caption: str, images: list[Path], *, limit: int, model: str,
                    api_key: str | None = None, client: httpx.Client | None = None) -> list[str] | None:
    """Ask Gemini who the post is about. Returns None when it could not ask (no key, error,
    timeout), so the caller can try again later instead of remembering "nobody"."""
    api_key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        log.info("GEMINI_API_KEY not set; no mentions suggested")
        return None
    # Ask for a few extra: some may fail the existence check on X later.
    ask = limit + 2
    body = {
        "contents": [{"parts": [{"text": PROMPT.format(caption=caption or "(none)", limit=ask)},
                                *[_image_part(p) for p in _sample(images, 10)]]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA},
    }
    c = client or httpx.Client(timeout=httpx.Timeout(180, connect=15))
    for attempt in range(1, RETRIES + 1):
        try:
            r = c.post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": api_key}, json=body)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < RETRIES:
                raise _Transient(f"HTTP {r.status_code}")
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return parse_suggestions(json.loads(text), ask)
        except (_Transient, httpx.TimeoutException, httpx.TransportError) as e:
            if attempt == RETRIES:
                log.warning("Gemini mention lookup failed after %d tries: %s", RETRIES,
                            str(e).replace(api_key, "***")[:200])
                return None
            wait = RETRY_WAIT * attempt
            log.info("Gemini busy (%s); retrying in %ds", e, wait)
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001  (never block a post because of mentions)
            log.warning("Gemini mention lookup failed: %s", str(e).replace(api_key, "***")[:300])
            return None
    return None


class _Transient(Exception):
    pass
