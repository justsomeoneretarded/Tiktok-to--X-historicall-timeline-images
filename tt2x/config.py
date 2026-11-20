"""Loading and saving config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CAPTION = "{caption}\n\n📷 via @{author} on TikTok"


@dataclass
class Config:
    accounts: list[str] = field(default_factory=list)
    check_every_minutes: int = 60
    timezone: str = "UTC"
    slots: list[str] = field(default_factory=lambda: ["09:00", "14:00", "20:00"])
    order: str = "oldest_first"  # or newest_first
    missed_slot_grace_minutes: int = 120
    caption_template: str = DEFAULT_CAPTION
    max_images_per_post: int = 0  # 0 = all slides in one post
    long_text: bool = False  # X Premium long posts; otherwise captions are cut to 280
    posts_per_account_scan: int = 30
    dedupe_max_distance: int = 6  # perceptual-hash bits that may differ
    dedupe_match_ratio: float = 0.8  # share of slides that must match to call it a duplicate
    cookies_file: str = "cookies.json"
    data_dir: str = "."
    path: Path | None = None

    @property
    def data_path(self) -> Path:
        base = self.path.parent if self.path else Path.cwd()
        return (base / self.data_dir).resolve()

    @property
    def db_path(self) -> Path:
        return self.data_path / "tt2x.db"

    @property
    def downloads_path(self) -> Path:
        return self.data_path / "downloads"

    @property
    def cookies_path(self) -> Path:
        base = self.path.parent if self.path else Path.cwd()
        return (base / self.cookies_file).resolve()


def normalize_handle(handle: str) -> str:
    handle = handle.strip()
    if "tiktok.com/@" in handle:
        handle = handle.split("tiktok.com/@", 1)[1].split("/")[0].split("?")[0]
    return handle.lstrip("@").lower()


def load_config(path: str | Path) -> Config:
    path = Path(path)
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    schedule = raw.pop("schedule", {}) or {}
    cfg = Config(path=path.resolve())
    for key, value in {**raw, **schedule}.items():
        if hasattr(cfg, key) and key != "path":
            setattr(cfg, key, value)
    cfg.accounts = [normalize_handle(a) for a in cfg.accounts or []]
    return cfg


def save_accounts(path: str | Path, accounts: list[str]) -> None:
    """Rewrite only the accounts list, keeping the rest of the file's keys."""
    path = Path(path)
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["accounts"] = ["@" + a for a in accounts]
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
