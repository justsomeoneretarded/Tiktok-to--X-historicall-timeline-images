"""Posting to X with the user's browser session cookie, via TweetKit-X
(the same library the X-soccer-news repo uses with the same TWEETKIT_COOKIE secret)."""

from __future__ import annotations

import json
import logging
import time
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


def balanced_chunks(items: list, limit: int) -> list[list]:
    """Fewest groups of at most `limit`, as even as possible, order kept (24 @ 20 -> 12 + 12)."""
    if limit <= 0 or len(items) <= limit:
        return [list(items)]
    n = -(-len(items) // limit)
    base, extra = divmod(len(items), n)
    out, i = [], 0
    for g in range(n):
        size = base + (1 if g < extra else 0)
        out.append(items[i:i + size])
        i += size
    return out


def media_limit_from_error(message: str) -> int | None:
    """X says e.g. 'Tweet must not have more than 20 mediaIds.'"""
    m = re.search(r"more than (\d+) media", message, re.I)
    return int(m.group(1)) if m else None


def chunk(items: list, size: int) -> list[list]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]



class PostedButUnconfirmed(RuntimeError):
    """X answered without an error but also without a post id: the post may exist. Never retry automatically."""


class PartialThreadError(RuntimeError):
    def __init__(self, tweet_ids: list[str], cause: Exception):
        super().__init__(f"thread stopped after {len(tweet_ids)} posts ({tweet_ids}): {cause}")
        self.tweet_ids = tweet_ids


def cookie_header(cookies: dict[str, str]) -> str:
    """`auth_token=...; ct0=...; ...` as TweetKit-X expects."""
    keep = {k: v for k, v in cookies.items() if re.fullmatch(r"[A-Za-z0-9_\-]+", k) and v}
    first = [k for k in ("auth_token", "ct0") if k in keep]
    return "; ".join(f"{k}={keep[k]}" for k in first + [k for k in keep if k not in first])


def _check(result: dict, what: str) -> str:
    """Return the new post id, or raise. TweetKit-X returns {ok, id} / {ok: False, status, error}."""
    if result.get("ok") and str(result.get("id", "")).isdigit():
        return str(result["id"])
    error = str(result.get("error", ""))[:300]
    status = result.get("status")
    if status == 200 and error.lstrip().startswith("{"):
        # HTTP 200 with a body we couldn't read an id from: X may have created the post.
        raise PostedButUnconfirmed(f"{what}: X answered 200 without a post id; check X before retrying")
    raise RuntimeError(f"{what} refused by X (HTTP {status}): {error}")


class XPoster:
    def __init__(self, cookies_path: Path | None, *, max_images_per_post: int = 0, long_text: bool = False,
                 client=None):
        self.cookies_path = cookies_path
        self.max_images = max_images_per_post
        self.long_text = long_text
        self._client = client

    def _get_client(self):
        if self._client is None:
            from tweetkit_x import TweetKit

            cookies = load_cookie_dict(self.cookies_path or Path("cookies.json"))
            try:
                self._client = TweetKit(cookie=cookie_header(cookies), timeout=60)
            except Exception as e:  # noqa: BLE001  (never echo cookie values)
                raise ValueError(f"X session cookie not accepted ({type(e).__name__}); "
                                 "update the TWEETKIT_COOKIE secret") from None
        return self._client

    def _create(self, text: str, media_ids: list[str], reply_to: str | None = None) -> str:
        return _check(self._get_client().create_tweet(text, media_ids=media_ids, reply_to=reply_to), "post")

    def post(self, text: str, files: list[Path]) -> list[str]:
        client = self._get_client()
        media_ids: list[str] = []
        for f in files:  # one at a time, so media_ids follow slide order
            media_ids.append(str(client.upload_media(str(f))))
            log.info("uploaded %s (%d/%d)", f.name, len(media_ids), len(files))

        text = fit_text(text, self.long_text)
        groups = chunk(media_ids, self.max_images)
        if len(groups) == 1:
            try:
                return [self._create(text, groups[0])]
            except PostedButUnconfirmed:
                raise
            except Exception as e:  # noqa: BLE001
                if len(media_ids) <= FALLBACK_CHUNK:
                    raise
                # Nothing was posted, so splitting into an ordered thread is safe.
                limit = media_limit_from_error(str(e)) or FALLBACK_CHUNK
                groups = balanced_chunks(media_ids, limit)
                log.warning("X refused %d images in one post (%s); posting a thread of %s",
                            len(media_ids), e, "+".join(str(len(g)) for g in groups))
        return self._thread(text, groups)

    def _thread(self, text: str, groups: list[list[str]]) -> list[str]:
        ids: list[str] = []
        total = len(groups)
        for n, group in enumerate(groups, start=1):
            body = text if n == 1 else f"({n}/{total})"
            try:
                ids.append(self._create(body, group, reply_to=ids[-1] if ids else None))
            except Exception as e:
                if ids:  # part of the thread is live: report exactly what exists
                    raise PartialThreadError(ids, e) from e
                raise
            time.sleep(3)
        return ids

    def whoami(self) -> str:
        """Login check (read-only): which account the cookie belongs to."""
        client = self._get_client()
        info = client.whoami()
        if not info.get("ready"):
            raise ValueError("cookie lacks auth_token/ct0")
        uid = info.get("user_id")
        name = None
        if uid and hasattr(client, "user_by_id"):
            try:
                name = (client.user_by_id(uid) or {}).get("username")
            except Exception:  # noqa: BLE001
                name = None
        # A real authenticated call proves the session works (not just that the cookie looks complete).
        probe = client.user_profile("X")
        if not probe.get("ok"):
            raise RuntimeError(f"X rejected the session: {str(probe.get('error'))[:200]}")
        return f"@{name}" if name else f"user id {uid}"

    def verify_handles(self, handles: list[str], min_followers: int = 10000) -> list[str]:
        """Keep only handles that exist on X and look official (verified, or many followers)."""
        client = self._get_client()
        ok = []
        for h in handles:
            try:
                prof = client.user_profile(h)
            except Exception as e:  # noqa: BLE001
                log.info("not mentioning @%s: %s", h, str(e)[:100])
                continue
            if not prof.get("ok") or not prof.get("username"):
                log.info("not mentioning @%s: account not found", h)
                continue
            followers = int(prof.get("followers") or 0)
            if prof.get("verified") or followers >= min_followers:
                ok.append(prof["username"])
            else:
                log.info("not mentioning @%s: unverified with %d followers", h, followers)
        return ok
