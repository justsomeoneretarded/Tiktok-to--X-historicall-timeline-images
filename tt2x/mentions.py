"""Who a post is about: Gemini reads the caption + slides and suggests X handles to @mention."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from pathlib import Path

import httpx
from PIL import Image

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
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


def _image_part(path: Path) -> dict:
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((512, 512))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}


def suggest_handles(caption: str, images: list[Path], *, limit: int, model: str,
                    api_key: str | None = None, client: httpx.Client | None = None) -> list[str]:
    """Ask Gemini who the post is about. Returns [] when no key is set or anything fails."""
    api_key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        log.info("GEMINI_API_KEY not set; no mentions suggested")
        return []
    # Ask for a few extra: some may fail the existence check on X later.
    ask = limit + 2
    body = {
        "contents": [{"parts": [{"text": PROMPT.format(caption=caption or "(none)", limit=ask)},
                                *[_image_part(p) for p in images[:24]]]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA},
    }
    try:
        c = client or httpx.Client(timeout=90)
        r = c.post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": api_key}, json=body)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return parse_suggestions(json.loads(text), ask)
    except Exception as e:  # noqa: BLE001  (never block a post because of mentions)
        msg = str(e).replace(api_key, "***")
        log.warning("Gemini mention lookup failed: %s", msg[:300])
        return []
