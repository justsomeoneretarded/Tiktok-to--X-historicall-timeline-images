"""Posting to X with the user's browser cookies (twikit)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

MAX_TEXT = 280
FALLBACK_CHUNK = 4


def load_cookie_dict(path: Path) -> dict[str, str]:
    """Accept {"auth_token": "...", "ct0": "..."} or a browser-extension export list."""
    if not path.exists():
        raise FileNotFoundError(f"cookie file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {c["name"]: c["value"] for c in data if "name" in c and "value" in c}
    if not isinstance(data, dict) or "auth_token" not in data or "ct0" not in data:
        raise ValueError("cookie file must contain auth_token and ct0")
    return {k: str(v) for k, v in data.items()}


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
