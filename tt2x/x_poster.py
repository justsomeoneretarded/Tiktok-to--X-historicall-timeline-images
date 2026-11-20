"""Posting to X with the user's browser cookies (twikit)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

MAX_TEXT = 280
FALLBACK_CHUNK = 4


_KEY_ALIASES = {"x_auth_token": "auth_token", "auth_token": "auth_token", "authtoken": "auth_token",
                "x_ct0": "ct0", "ct0": "ct0"}


def _normalize(data: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in data.items():
        key = str(k).strip().strip("'\"").lower()
        out[_KEY_ALIASES.get(key, str(k).strip())] = str(v).strip().strip("'\"")
    return out


def _require(cookies: dict[str, str], source: str) -> dict[str, str]:
    missing = [k for k in ("auth_token", "ct0") if not cookies.get(k)]
    if missing:
        raise ValueError(f"{source} is missing {' and '.join(missing)} "
                         "(expected e.g. 'X_AUTH_TOKEN = ..., X_CT0 = ...')")
    return cookies


def parse_cookie_string(s: str) -> dict[str, str]:
    """Read cookies from text in any common shape.

    Accepts `X_AUTH_TOKEN = abc, X_CT0 = def`, a browser Cookie header
    (`auth_token=abc; ct0=def; ...`), one pair per line, or JSON (dict or export list).
    """
    s = s.strip()
    if s.lower().startswith("cookie:"):
        s = s[7:].strip()
    if s[:1] in "[{":
        try:
            data = json.loads(s)
            if isinstance(data, list):
                data = {c["name"]: c["value"] for c in data if "name" in c and "value" in c}
            return _normalize(data)
        except (ValueError, TypeError):
            pass
    pairs: dict[str, str] = {}
    for part in re.split(r"[,;\n]+", s):
        m = re.match(r"\s*([^=:\s]+)\s*[=:]\s*(.*?)\s*$", part)
        if m:
            pairs[m.group(1)] = m.group(2)
    return _normalize(pairs)


def load_cookie_dict(path: Path) -> dict[str, str]:
    """Cookies from env TWEETKIT_COOKIE, else env X_AUTH_TOKEN + X_CT0, else the cookies file."""
    if os.environ.get("TWEETKIT_COOKIE", "").strip():
        return _require(parse_cookie_string(os.environ["TWEETKIT_COOKIE"]), "TWEETKIT_COOKIE")
    if os.environ.get("X_AUTH_TOKEN") and os.environ.get("X_CT0"):
        return _require(_normalize({"auth_token": os.environ["X_AUTH_TOKEN"],
                                    "ct0": os.environ["X_CT0"]}), "X_AUTH_TOKEN/X_CT0")
    if not path.exists():
        raise FileNotFoundError(
            f"no X cookies: set TWEETKIT_COOKIE (or X_AUTH_TOKEN and X_CT0), or create {path}")
    return _require(parse_cookie_string(path.read_text(encoding="utf-8")), str(path))


def fit_text(text: str, long_text: bool) -> str:
    text = text.strip()
    if long_text or len(text) <= MAX_TEXT:
        return text
    return text[: MAX_TEXT - 1].rstrip() + "…"


def chunk(items: list, size: int) -> list[list]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


class XPoster:
    def __init__(self, cookies_path: Path, *, max_images_per_post: int = 0, long_text: bool = False,
                 client=None):
        self.cookies_path = cookies_path
        self.max_images = max_images_per_post
        self.long_text = long_text
        self._client = client

    def _get_client(self):
        if self._client is None:
            from twikit import Client

            c = Client("en-US")
            c.set_cookies(load_cookie_dict(self.cookies_path))
            self._client = c
        return self._client

    async def _post(self, text: str, files: list[Path]) -> list[str]:
        client = self._get_client()
        media_ids: list[str] = []
        for f in files:  # one at a time, so media_ids follow slide order
            media_ids.append(await client.upload_media(str(f), wait_for_completion=True))
            log.info("uploaded %s", f.name)

        text = fit_text(text, self.long_text)
        groups = chunk(media_ids, self.max_images)
        if len(groups) == 1:
            try:
                tweet = await client.create_tweet(text=text, media_ids=groups[0],
                                                  is_note_tweet=self.long_text and len(text) > MAX_TEXT)
                return [str(tweet.id)]
            except Exception as e:  # noqa: BLE001
                if len(media_ids) <= FALLBACK_CHUNK:
                    raise
                # Nothing was posted yet, so splitting into a thread is safe.
                log.warning("X refused %d images in one post (%s); posting as a thread of %d",
                            len(media_ids), e, FALLBACK_CHUNK)
                groups = chunk(media_ids, FALLBACK_CHUNK)
        return await self._thread(client, text, groups)

    async def _thread(self, client, text: str, groups: list[list[str]]) -> list[str]:
        ids: list[str] = []
        reply_to = None
        total = len(groups)
        for n, group in enumerate(groups, start=1):
            body = text if n == 1 else f"({n}/{total})"
            try:
                tweet = await client.create_tweet(text=body, media_ids=group, reply_to=reply_to)
            except Exception as e:
                if ids:  # part of the thread is live: report exactly what exists
                    raise PartialThreadError(ids, e) from e
                raise
            ids.append(str(tweet.id))
            reply_to = tweet.id
            await asyncio.sleep(3)
        return ids

    def post(self, text: str, files: list[Path]) -> list[str]:
        return asyncio.run(self._post(text, files))


class PartialThreadError(RuntimeError):
    def __init__(self, tweet_ids: list[str], cause: Exception):
        super().__init__(f"thread stopped after {len(tweet_ids)} posts ({tweet_ids}): {cause}")
        self.tweet_ids = tweet_ids
