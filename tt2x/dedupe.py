"""Fingerprints that catch the same carousel re-uploaded under another id or account."""

from __future__ import annotations

import hashlib
from pathlib import Path

import imagehash
from PIL import Image


def fingerprint(path: Path) -> tuple[str, str]:
    """(sha256 of the file, perceptual hash of the picture)."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    with Image.open(path) as img:
        ph = str(imagehash.phash(img))
    return sha, ph


def content_hash(phashes: list[str]) -> str:
    """One hash for the whole carousel, slide order included."""
    return hashlib.sha256("|".join(phashes).encode()).hexdigest()


def _distance(a: str, b: str) -> int:
    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)


def find_similar(
    new: list[str],
    existing: dict[int, list[str]],
    max_distance: int = 6,
    match_ratio: float = 0.8,
) -> int | None:
    """Return the id of an existing post whose slides mostly match `new`, or None.

    A slide matches when some slide of the other post is within `max_distance` bits.
    It is a duplicate when at least `match_ratio` of the new slides match. This
    survives re-encoding, small crops and watermarks better than exact hashes.
    """
    if not new:
        return None
    need = max(1, round(len(new) * match_ratio))
    for post_id, old in existing.items():
        if not old:
            continue
        hits = sum(1 for h in new if any(_distance(h, o) <= max_distance for o in old if o))
        if hits >= need:
            return post_id
    return None
