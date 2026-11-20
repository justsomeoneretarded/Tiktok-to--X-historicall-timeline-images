"""Command line: tt2x <command>."""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import tiktok
from .config import load_config, normalize_handle, save_accounts
from .core import check_accounts, ingest, post_next, utcnow
from .db import DB
from .scheduler import due_slot, upcoming_slots
from .x_poster import XPoster

log = logging.getLogger("tt2x")


def _poster(cfg):
    return XPoster(cfg.cookies_path, max_images_per_post=cfg.max_images_per_post, long_text=cfg.long_text)


def cmd_init(args, cfg, db):
    example = Path(__file__).resolve().parent.parent / "config.example.yaml"
    target = Path(args.config)
    if target.exists():
        print(f"{target} already exists")
    else:
        shutil.copy(example, target)
        print(f"created {target}; edit accounts and schedule, then add cookies.json")


def cmd_add_account(args, cfg, db):
    accounts = list(cfg.accounts)
    for h in args.handles:
        h = normalize_handle(h)
        if h not in accounts:
            accounts.append(h)
    save_accounts(args.config, accounts)
    print("accounts:", ", ".join("@" + a for a in accounts))


def cmd_remove_account(args, cfg, db):
    drop = {normalize_handle(h) for h in args.handles}
    accounts = [a for a in cfg.accounts if a not in drop]
    save_accounts(args.config, accounts)
    print("accounts:", ", ".join("@" + a for a in accounts) or "(none)")


def cmd_add_url(args, cfg, db):
    for url in args.urls:
        car = tiktok.get_post(url)
        print(f"{url}: {ingest(cfg, db, car)} ({len(car.image_urls)} slides, @{car.author})")


def _all_blocked(cfg, totals) -> bool:
    return bool(cfg.accounts) and totals.get("blocked_accounts", 0) >= len(cfg.accounts)


BLOCKED_MSG = ("no account could be listed: TikTok/tikwm are blocking this connection "
               "(see the debug screenshots). Add posts with `tt2x add-url`, or run `tt2x check --sync` "
               "on a home computer.")


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def _pull_state(cfg) -> None:
    """Make the data folder an exact copy of the tt2x-state branch (the shared memory)."""
    d = cfg.data_path
    d.mkdir(parents=True, exist_ok=True)
    if not (d / ".git").exists():
        url = _git(cfg.path.parent, "remote", "get-url", "origin").stdout.strip()
        _git(d, "init", "-q", "-b", "tt2x-state")
        _git(d, "remote", "add", "origin", url)
    if _git(d, "fetch", "-q", "--depth", "1", "origin", "tt2x-state", check=False).returncode == 0:
        _git(d, "reset", "-q", "--hard", "FETCH_HEAD")
        _git(d, "clean", "-qfd")


def _push_state(cfg) -> bool:
    d = cfg.data_path
    shutil.rmtree(d / "debug", ignore_errors=True)
    _git(d, "add", "-A")
    if _git(d, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return True
    _git(d, "-c", "user.name=tt2x", "-c", "user.email=tt2x@users.noreply.github.com",
         "commit", "-q", "-m", f"tt2x state (local check) {utcnow():%Y-%m-%dT%H:%MZ}")
    return _git(d, "push", "-q", "origin", "HEAD:tt2x-state", check=False).returncode == 0


def cmd_check(args, cfg, db):
    if not cfg.accounts:
        print("no accounts in config; use `tt2x add-account @handle`")
        return
    if not args.sync:
        totals = check_accounts(cfg, db)
    else:
        # Share the queue with GitHub Actions: start from its latest state, add new posts, push.
        # If GitHub pushed in the meantime, start over from its version (checking is repeatable).
        for attempt in range(3):
            db.conn.close()
            _pull_state(cfg)
            db = DB(cfg.db_path)
            totals = check_accounts(cfg, db)
            db.conn.close()
            if _push_state(cfg):
                print("state pushed to the tt2x-state branch")
                break
            log.warning("tt2x-state changed while checking; retrying (%d/3)", attempt + 1)
        else:
            sys.exit("could not push state; try again later")
    print(totals)
    if _all_blocked(cfg, totals):
        sys.exit(BLOCKED_MSG)


def cmd_queue(args, cfg, db):
    queued = db.queued(cfg.order)
    slots = upcoming_slots(utcnow(), cfg.slots, cfg.timezone, len(queued))
    print(f"QUEUED ({len(queued)})")
    for row, when in zip(queued, slots):
        print(f"  #{row['id']:<4} {when:%a %d %b %H:%M}  @{row['author']:<20} "
              f"{row['image_count']:>2} slides  {(row['caption'] or '')[:50]!r}")
    if args.all:
        print("\nHISTORY")
        for row in db.all(args.limit):
            extra = row["tweet_ids"] or row["error"] or (f"dup of #{row['duplicate_of']}" if row["duplicate_of"] else "")
            print(f"  #{row['id']:<4} {row['status']:<18} @{row['author'] or '':<20} {row['tiktok_id']}  {extra or ''}")


def cmd_show(args, cfg, db):
    row = db.get(args.id)
    if not row:
        sys.exit(f"no post #{args.id}")
    for k in row.keys():
        print(f"{k:>13}: {row[k]}")
    for img in db.images(args.id):
        print(f"      slide {img['idx']:>2}: {img['file']}")


def cmd_post_next(args, cfg, db):
    print(post_next(cfg, db, None if args.dry_run else _poster(cfg), dry_run=args.dry_run, post_id=args.id))


def cmd_retry(args, cfg, db):
    row = db.get(args.id)
    if not row or row["status"] not in ("failed", "posting"):
        sys.exit("only failed/posting items can be retried")
    db.set_status(args.id, "queued")
    print(f"#{args.id} queued again")


def cmd_skip(args, cfg, db):
    db.set_status(args.id, "skipped_manual")
    print(f"#{args.id} will not be posted")


def cmd_run(args, cfg, db):
    for row in db.stuck_posting():
        log.warning("#%s was mid-post when the program stopped. Check X, then run "
                    "`tt2x retry %s` or `tt2x skip %s`.", row["id"], row["id"], row["id"])
    poster = _poster(cfg)
    blocked = False
    print(f"running: {len(cfg.accounts)} accounts, check every {cfg.check_every_minutes} min, "
          f"slots {cfg.slots} ({cfg.timezone}).{'' if args.once else ' Ctrl+C to stop.'}")
    while True:
        last_check = float(db.get_meta("last_check") or 0)
        # small margin so a 60-min interval is not missed by a run that starts a few seconds early
        if time.time() >= last_check + cfg.check_every_minutes * 60 - 120:
            db.set_meta("last_check", str(time.time()))
            totals = check_accounts(cfg, db)
            log.info("check: %s", totals)
            blocked = _all_blocked(cfg, totals)
            if blocked:
                log.error(BLOCKED_MSG)
        last = db.get_meta("last_slot")
        last_dt = datetime.fromisoformat(last) if last else None
        slot = due_slot(utcnow(), cfg.slots, cfg.timezone, last_dt, cfg.missed_slot_grace_minutes)
        if slot:
            db.set_meta("last_slot", slot.isoformat())  # mark first: a slot fires at most once
            log.info("slot %s: %s", slot.strftime("%H:%M"), post_next(cfg, db, poster))
        if args.once:
            if blocked:  # still posted whatever was due above; now make the run visibly fail
                sys.exit(1)
            return
        time.sleep(30)


def cmd_prune(args, cfg, db):
    removed = 0
    for row in db.conn.execute("SELECT id, tiktok_id FROM posts WHERE status NOT IN ('queued','posting','failed')"):
        folder = cfg.downloads_path / row["tiktok_id"]
        if folder.is_dir():
            shutil.rmtree(folder)
            removed += 1
    print(f"removed image folders of {removed} finished items (their fingerprints are kept)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="tt2x", description="TikTok photo carousels -> scheduled X posts")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)
    s = sub.add_parser("add-account"); s.add_argument("handles", nargs="+"); s.set_defaults(fn=cmd_add_account)
    s = sub.add_parser("remove-account"); s.add_argument("handles", nargs="+"); s.set_defaults(fn=cmd_remove_account)
    s = sub.add_parser("add-url"); s.add_argument("urls", nargs="+"); s.set_defaults(fn=cmd_add_url)
    s = sub.add_parser("check")
    s.add_argument("--sync", action="store_true",
                   help="pull the tt2x-state branch first and push the result (home computer + GitHub posting)")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("queue"); s.add_argument("--all", action="store_true"); s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_queue)
    s = sub.add_parser("show"); s.add_argument("id", type=int); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("post-next"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--id", type=int)
    s.set_defaults(fn=cmd_post_next)
    s = sub.add_parser("retry"); s.add_argument("id", type=int); s.set_defaults(fn=cmd_retry)
    s = sub.add_parser("skip"); s.add_argument("id", type=int); s.set_defaults(fn=cmd_skip)
    sub.add_parser("prune").set_defaults(fn=cmd_prune)
    s = sub.add_parser("run"); s.add_argument("--once", action="store_true"); s.set_defaults(fn=cmd_run)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)
    db = None if args.cmd == "init" else DB(cfg.db_path)
    try:
        args.fn(args, cfg, db)
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
