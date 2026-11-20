"""The workflow: ingest carousels into the queue, and post the next one."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import tiktok
from .config import Config
from .db import DB
from .dedupe import content_hash, find_similar, fingerprint
from .x_poster import PartialThreadError

log = logging.getLogger(__name__)


class _Blank(dict):
    def __missing__(self, key):
        return ""


def render_caption(cfg: Config, row) -> str:
    return cfg.caption_template.format_map(
        _Blank(caption=(row["caption"] or "").strip(), author=row["author"] or "", url=row["url"] or "")
    ).strip()


def ingest(cfg: Config, db: DB, car: tiktok.Carousel) -> str:
    """Add one carousel. Returns: seen | skipped_video | skipped_duplicate | queued."""
    if db.seen(car.id):
        return "seen"
    common = dict(tiktok_id=car.id, author=car.author, caption=car.caption,
                  url=car.url, created_at=car.created_at)
    if not car.is_photo:  # a normal video: no slides to repost
        db.add_post(status="skipped_video", **common)
        return "skipped_video"

    files = tiktok.download_images(car, cfg.downloads_path / car.id)
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


def check_accounts(cfg: Config, db: DB) -> dict[str, int]:
    totals: dict[str, int] = {}
    for handle in cfg.accounts:
        try:
            posts = tiktok.list_account_posts(handle, cfg.posts_per_account_scan)
        except Exception as e:  # noqa: BLE001
            log.error("could not list @%s: %s", handle, e)
            totals["errors"] = totals.get("errors", 0) + 1
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
    text = render_caption(cfg, row)

    if dry_run:
        return (f"[dry run] would post #{row['id']} (@{row['author']} {row['tiktok_id']}) with "
                f"{len(files)} images in this order: {', '.join(f.name for f in files)}\n---\n{text}")

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
