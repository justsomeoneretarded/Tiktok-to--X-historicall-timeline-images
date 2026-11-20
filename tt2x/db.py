"""SQLite store: every carousel ever seen, its slides, and whether it was posted."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY,
    tiktok_id     TEXT NOT NULL UNIQUE,
    author        TEXT,
    caption       TEXT,
    url           TEXT,
    created_at    INTEGER,
    image_count   INTEGER,
    content_hash  TEXT,
    status        TEXT NOT NULL,     -- queued | posting | posted | failed | skipped_duplicate | skipped_video
    duplicate_of  INTEGER,
    tweet_ids     TEXT,              -- comma separated, first is the main post
    posted_at     INTEGER,
    error         TEXT,
    added_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS posts_hash ON posts(content_hash);
CREATE TABLE IF NOT EXISTS images (
    post_id  INTEGER NOT NULL REFERENCES posts(id),
    idx      INTEGER NOT NULL,
    file     TEXT NOT NULL,
    sha256   TEXT,
    phash    TEXT,
    PRIMARY KEY (post_id, idx)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

ACTIVE = ("queued", "posting", "posted", "failed")  # statuses that count for duplicate checks


class DB:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ----- reads -----
    def seen(self, tiktok_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM posts WHERE tiktok_id=?", (tiktok_id,)).fetchone() is not None

    def get(self, post_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()

    def images(self, post_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM images WHERE post_id=? ORDER BY idx", (post_id,)
        ).fetchall()

    def by_content_hash(self, content_hash: str) -> sqlite3.Row | None:
        q = f"SELECT * FROM posts WHERE content_hash=? AND status IN ({','.join('?' * len(ACTIVE))})"
        return self.conn.execute(q, (content_hash, *ACTIVE)).fetchone()

    def active_phashes(self) -> dict[int, list[str]]:
        q = (
            "SELECT i.post_id, i.phash FROM images i JOIN posts p ON p.id=i.post_id "
            f"WHERE p.status IN ({','.join('?' * len(ACTIVE))}) ORDER BY i.post_id, i.idx"
        )
        out: dict[int, list[str]] = {}
        for row in self.conn.execute(q, ACTIVE):
            out.setdefault(row["post_id"], []).append(row["phash"])
        return out

    def queued(self, order: str = "oldest_first") -> list[sqlite3.Row]:
        direction = "DESC" if order == "newest_first" else "ASC"
        return self.conn.execute(
            f"SELECT * FROM posts WHERE status='queued' ORDER BY created_at {direction}, id {direction}"
        ).fetchall()

    def all(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM posts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def stuck_posting(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM posts WHERE status='posting'").fetchall()

    # ----- writes -----
    def add_post(self, *, tiktok_id, author, caption, url, created_at, status,
                 content_hash=None, duplicate_of=None, images=()) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO posts (tiktok_id, author, caption, url, created_at, image_count, "
                "content_hash, status, duplicate_of, added_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tiktok_id, author, caption, url, created_at, len(images), content_hash,
                 status, duplicate_of, int(time.time())),
            )
            pid = cur.lastrowid
            self.conn.executemany(
                "INSERT INTO images (post_id, idx, file, sha256, phash) VALUES (?,?,?,?,?)",
                [(pid, i, f, s, h) for i, (f, s, h) in enumerate(images, start=1)],
            )
        return pid

    def claim_for_posting(self, post_id: int) -> bool:
        """Atomically move queued -> posting. False if someone else already took it."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE posts SET status='posting' WHERE id=? AND status='queued'", (post_id,)
            )
        return cur.rowcount == 1

    def mark_posted(self, post_id: int, tweet_ids: list[str], error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE posts SET status='posted', tweet_ids=?, posted_at=?, error=? WHERE id=?",
                (",".join(tweet_ids), int(time.time()), error, post_id),
            )

    def mark_failed(self, post_id: int, error: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE posts SET status='failed', error=? WHERE id=?", (error[:1000], post_id))

    def set_status(self, post_id: int, status: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE posts SET status=? WHERE id=?", (status, post_id))

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))
