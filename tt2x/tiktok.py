"""Reading TikTok photo carousels: list an account's posts, read one post, download slides in order."""

from __future__ import annotations

import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from PIL import Image

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Referer": "https://www.tiktok.com/", "Accept-Language": "en-US,en;q=0.9"}
TIKWM = "https://www.tikwm.com"


@dataclass
class Carousel:
    id: str
    author: str
    caption: str
    url: str
    created_at: int  # unix seconds
    image_urls: list[str] = field(default_factory=list)  # in slide order

    @property
    def is_photo(self) -> bool:
        return bool(self.image_urls)


class TikTokError(RuntimeError):
    pass


# ---------- parsing (pure functions, unit-tested) ----------

def carousel_from_item_struct(item: dict) -> Carousel:
    """Build a Carousel from TikTok's own itemStruct JSON (web page or item_list API)."""
    author = item.get("author") or {}
    handle = author.get("uniqueId") if isinstance(author, dict) else str(author)
    images = ((item.get("imagePost") or {}).get("images")) or []
    urls = []
    for img in images:  # list index == slide order
        url_list = (img.get("imageURL") or {}).get("urlList") or []
        if url_list:
            urls.append(url_list[0])
    pid = str(item["id"])
    return Carousel(
        id=pid,
        author=handle or "",
        caption=item.get("desc", "") or "",
        url=f"https://www.tiktok.com/@{handle}/photo/{pid}",
        created_at=int(item.get("createTime") or 0),
        image_urls=urls,
    )


def carousel_from_tikwm(data: dict) -> Carousel:
    """Build a Carousel from a tikwm.com post object."""
    author = data.get("author") or {}
    handle = author.get("unique_id", "") if isinstance(author, dict) else ""
    pid = str(data.get("video_id") or data.get("id") or data.get("aweme_id"))
    images = data.get("images") or []
    return Carousel(
        id=pid,
        author=handle,
        caption=data.get("title", "") or "",
        url=f"https://www.tiktok.com/@{handle}/photo/{pid}",
        created_at=int(data.get("create_time") or 0),
        image_urls=[u for u in images if isinstance(u, str)],
    )


def parse_post_html(html: str) -> Carousel:
    m = re.search(
        r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S
    )
    if not m:
        raise TikTokError("page has no rehydration data (blocked or layout changed)")
    data = json.loads(m.group(1))
    scope = data.get("__DEFAULT_SCOPE__", {})
    detail = scope.get("webapp.video-detail") or scope.get("webapp.photo-detail") or {}
    item = (detail.get("itemInfo") or {}).get("itemStruct")
    if not item:
        raise TikTokError(f"no itemStruct in page (statusCode={detail.get('statusCode')})")
    return carousel_from_item_struct(item)


# ---------- network ----------

def _client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30)


def _tikwm_post(url: str) -> Carousel:
    with _client() as c:
        r = c.get(f"{TIKWM}/api/", params={"url": url, "hd": 1})
        r.raise_for_status()
        body = r.json()
    if body.get("code") != 0:
        raise TikTokError(f"tikwm: {body.get('msg')}")
    return carousel_from_tikwm(body["data"])


def get_post(url: str) -> Carousel:
    """Read one TikTok post (short or full link)."""
    try:
        with _client() as c:
            r = c.get(url)
            r.raise_for_status()
            return parse_post_html(r.text)
    except Exception as e:  # noqa: BLE001
        log.info("direct TikTok read failed (%s); trying tikwm", e)
    return _tikwm_post(url)


def _tikwm_user_posts(handle: str, count: int) -> list[Carousel]:
    out: list[Carousel] = []
    cursor = 0
    with _client() as c:
        while len(out) < count:
            r = c.get(
                f"{TIKWM}/api/user/posts",
                params={"unique_id": handle, "count": min(35, count), "cursor": cursor},
            )
            r.raise_for_status()
            body = r.json()
            if body.get("code") != 0:
                raise TikTokError(f"tikwm: {body.get('msg')}")
            data = body.get("data") or {}
            for v in data.get("videos") or []:
                car = carousel_from_tikwm(v)
                if not car.author:
                    car.author = handle
                    car.url = f"https://www.tiktok.com/@{handle}/photo/{car.id}"
                out.append(car)
            if not data.get("hasMore"):
                break
            cursor = data.get("cursor", 0)
            time.sleep(1.2)  # tikwm free tier allows ~1 request per second
    return out[:count]


def _playwright_user_posts(handle: str, count: int) -> list[Carousel]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise TikTokError("playwright not installed (pip install 'tt2x[browser]')") from e
    items: list[dict] = []

    def on_response(resp):
        if "/api/post/item_list" in resp.url:
            try:
                items.extend(resp.json().get("itemList") or [])
            except Exception:  # noqa: BLE001
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA)
        page.on("response", on_response)
        page.goto(f"https://www.tiktok.com/@{handle}", wait_until="domcontentloaded")
        for _ in range(8):
            if len(items) >= count:
                break
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)
        browser.close()
    return [carousel_from_item_struct(i) for i in items[:count]]


def list_account_posts(handle: str, count: int = 30) -> list[Carousel]:
    """Recent posts of an account, newest first. Photo posts have image_urls."""
    try:
        return _tikwm_user_posts(handle, count)
    except Exception as e:  # noqa: BLE001
        log.info("tikwm listing for @%s failed (%s); trying browser", handle, e)
    return _playwright_user_posts(handle, count)


def download_images(car: Carousel, dest: Path) -> list[Path]:
    """Download every slide as 01.jpg, 02.jpg, ... in slide order."""
    dest.mkdir(parents=True, exist_ok=True)
    files = []
    with _client() as c:
        for i, url in enumerate(car.image_urls, start=1):
            target = dest / f"{i:02d}.jpg"
            if not target.exists():
                r = c.get(url)
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content))
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(target, "JPEG", quality=95)
                time.sleep(0.3)
            files.append(target)
    return files
