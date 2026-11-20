"""The workflow: ingest carousels into the queue, and post the next one."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import tiktok, video
from .config import Config
from .db import DB
from .dedupe import content_hash, find_similar, fingerprint
from .mentions import suggest_handles
from .x_poster import MAX_TEXT, PartialThreadError

log = logging.getLogger(__name__)


class _Blank(dict):
    def __missing__(self, key):
        return ""


def render_caption(cfg: Config, row, handles: list[str] | tuple = ()) -> str:
    """Fill the template. When the post must fit 280 characters, only the caption is shortened:
    mentions and the credit line always stay."""
    values = dict(author=row["author"] or "", url=row["url"] or "",
                  mentions=" ".join("@" + h for h in handles))

    def fill(caption: str) -> str:
        text = cfg.caption_template.format_map(_Blank(caption=caption, **values))
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    caption = (row["caption"] or "").strip()
    text = fill(caption)
    if cfg.long_text or len(text) <= MAX_TEXT:
        return text
    room = MAX_TEXT - len(fill("")) - 3  # small margin: X counts emoji as 2
    if room <= 0:
        return fill("")
    short = caption[: room - 1].rstrip()
    if " " in short[room // 2:]:
        short = short[: short.rfind(" ")]  # don't cut a word in half
    return fill(short + "…")


def ensure_mentions(cfg: Config, db: DB, row, files: list[Path]) -> list[str]:
    """Suggested handles for a post (asks Gemini once, then remembers the answer)."""
    if not cfg.mentions:
        return []
    if row["mentions"] is not None:
        return json.loads(row["mentions"])
    handles = suggest_handles(row["caption"] or "", files, limit=cfg.max_mentions, model=cfg.gemini_model)
    if handles or os.environ.get("GEMINI_API_KEY"):
        db.set_mentions(row["id"], handles)  # don't store "nothing" when there was no key to ask with
    return handles


def ingest(cfg: Config, db: DB, car: tiktok.Carousel) -> str:
    """Add one post. Returns: seen | skipped_video | skipped_duplicate | queued."""
    if db.seen(car.id):
        return "seen"
    common = dict(tiktok_id=car.id, author=car.author, caption=car.caption,
                  url=car.url, created_at=car.created_at)
    dest = cfg.downloads_path / car.id
    if car.is_photo:
        files = tiktok.download_images(car, dest)
    elif car.video_url and cfg.include_videos:
        files = video_to_slides(cfg, car, dest)
        if not files:  # normal footage, not a slideshow
            shutil.rmtree(dest, ignore_errors=True)
            db.add_post(status="skipped_video", **common)
            return "skipped_video"
    else:
        db.add_post(status="skipped_video", **common)
        return "skipped_video"

    prints = [fingerprint(f) for f in files]
    phashes = [p for _, p in prints]
    ch = content_hash(phashes)

    dup = db.by_content_hash(ch)
    dup_id = dup["id"] if dup else find_similar(
        phashes, db.active_phashes(), cfg.dedupe_max_distance, cfg.dedupe_match_ratio
    )
    status = "skipped_duplicate" if dup_id else "queued"
    db.add_post(
        status=status, content_hash=ch, duplicate_of=dup_id,
        images=[(str(f), s, p) for f, (s, p) in zip(files, prints)], **common,
    )
    if dup_id:
        log.info("@%s %s looks like post #%s already in the queue/history; skipped", car.author, car.id, dup_id)
    else:
        log.info("queued @%s %s (%d slides)", car.author, car.id, len(files))
    return status


def video_to_slides(cfg: Config, car: tiktok.Carousel, dest: Path) -> list[Path]:
    clip = tiktok.download_video(car, dest)
    try:
        return video.extract_slides(clip, dest, min_seconds=cfg.video_min_slide_seconds,
                                    threshold=cfg.video_change_threshold)
    finally:
        clip.unlink(missing_ok=True)  # keep only the stills


def check_accounts(cfg: Config, db: DB) -> dict[str, int]:
    """Scan every account. totals["blocked_accounts"] counts accounts that could not be listed."""
    totals: dict[str, int] = {}
    for handle in cfg.accounts:
        try:
            posts = tiktok.list_account_posts(handle, cfg.posts_per_account_scan,
                                              debug_dir=cfg.data_path / "debug")
        except Exception as e:  # noqa: BLE001
            log.error("could not list @%s: %s", handle, e)
            totals["blocked_accounts"] = totals.get("blocked_accounts", 0) + 1
            continue
        for car in posts:
            try:
                result = ingest(cfg, db, car)
            except Exception as e:  # noqa: BLE001
                log.error("could not ingest %s: %s", car.url, e)
                result = "errors"
            totals[result] = totals.get(result, 0) + 1
        time.sleep(1.5)
    return totals


def post_next(cfg: Config, db: DB, poster, *, dry_run: bool = False, post_id: int | None = None) -> str:
    rows = [db.get(post_id)] if post_id else db.queued(cfg.order)
    rows = [r for r in rows if r is not None and r["status"] == "queued"]
    if not rows:
        return "nothing queued"
    row = rows[0]
    files = []
    for img in db.images(row["id"]):
        f = Path(img["file"])
        if not f.exists():  # data folder moved (e.g. another machine/runner): look in downloads/<id>/
            f = cfg.downloads_path / row["tiktok_id"] / f.name
        files.append(f)
    missing = [f for f in files if not f.exists()]
    if missing or not files:
        db.mark_failed(row["id"], f"missing image files: {missing or 'none'}")
        return f"#{row['id']} failed: image files missing"
    suggested = ensure_mentions(cfg, db, row, files)

    if dry_run:
        text = render_caption(cfg, row, suggested[: cfg.max_mentions])
        return (f"[dry run] would post #{row['id']} (@{row['author']} {row['tiktok_id']}) with "
                f"{len(files)} images in this order: {', '.join(f.name for f in files)}\n"
                f"mentions suggested (checked on X before a real post): {suggested or 'none'}\n---\n{text}")

    handles = poster.verify_handles(suggested, cfg.mention_min_followers)[: cfg.max_mentions] if suggested else []
    text = render_caption(cfg, row, handles)

    if not db.claim_for_posting(row["id"]):
        return f"#{row['id']} is no longer queued; not posting"
    try:
        tweet_ids = poster.post(text, files)
    except PartialThreadError as e:
        # Some of it is live: never retry automatically.
        db.mark_posted(row["id"], e.tweet_ids, error=str(e))
        return f"#{row['id']} partly posted: {e}"
    except Exception as e:  # noqa: BLE001
        db.mark_failed(row["id"], f"{type(e).__name__}: {e}")
        return f"#{row['id']} failed: {e} (check X, then `tt2x retry {row['id']}` if nothing went up)"
    db.mark_posted(row["id"], tweet_ids)
    return f"posted #{row['id']} -> https://x.com/i/status/{tweet_ids[0]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
