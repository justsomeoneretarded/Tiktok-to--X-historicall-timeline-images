"""Turn a slideshow video (one picture after another) into ordered still images."""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
from pathlib import Path

import imagehash
from PIL import Image, ImageStat

log = logging.getLogger(__name__)


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("ffmpeg not found (pip install imageio-ffmpeg)")
        return exe


def _is_blank(path: Path) -> bool:
    """Near-uniform frames (black/white fades, empty intro) are not slides."""
    with Image.open(path) as img:
        return max(ImageStat.Stat(img.convert("L").resize((64, 64))).stddev) < 8


def extract_slides(
    video: Path,
    dest: Path,
    *,
    fps: int = 5,
    min_seconds: float = 0.6,
    threshold: int = 10,
    min_coverage: float = 0.5,
) -> list[Path]:
    """Save one full-resolution still per picture shown in `video`, in order, as 01.jpg, 02.jpg, ...

    Frames are sampled at `fps`. Consecutive frames whose perceptual hash stays within
    `threshold` bits of the segment's first frame belong to the same picture. Segments
    shorter than `min_seconds` are transitions and are dropped; neighbouring segments
    that show the same picture are merged.

    Returns [] when the video does not look like a slideshow (fewer than 2 pictures, or
    the stable pictures cover less than `min_coverage` of the video, as in normal footage).
    """
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / "_frames"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(video),
             "-vf", f"fps={fps}", "-q:v", "2", str(tmp / "f%05d.jpg")],
            check=True, capture_output=True,
        )
        frames = sorted(tmp.glob("f*.jpg"))
        if not frames:
            return []
        hashes = []
        for f in frames:
            with Image.open(f) as img:
                hashes.append(imagehash.phash(img))

        segments: list[tuple[int, int]] = []
        start = 0
        for i in range(1, len(frames)):
            if hashes[i] - hashes[start] > threshold:
                segments.append((start, i))
                start = i
        segments.append((start, len(frames)))

        min_frames = max(1, math.ceil(min_seconds * fps))
        kept = [s for s in segments if s[1] - s[0] >= min_frames and not _is_blank(frames[(s[0] + s[1]) // 2])]

        def mid(seg):
            return (seg[0] + seg[1]) // 2

        # each picture = list of stable segments showing it (same picture can return after an overlay)
        merged: list[list[tuple[int, int]]] = []
        for s in kept:
            if merged and hashes[mid(merged[-1][-1])] - hashes[mid(s)] <= threshold:
                merged[-1].append(s)
            else:
                merged.append([s])

        coverage = sum(e - s for s, e in kept) / len(frames)
        if len(merged) < 2 or coverage < min_coverage:
            log.info("%s: not a slideshow (%d pictures, %.0f%% stable)", video.name, len(merged), coverage * 100)
            return []

        out = []
        for n, parts in enumerate(merged, start=1):
            best = max(parts, key=lambda seg: seg[1] - seg[0])  # longest time fully on screen
            target = dest / f"{n:02d}.jpg"
            shutil.copy(frames[mid(best)], target)
            out.append(target)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
