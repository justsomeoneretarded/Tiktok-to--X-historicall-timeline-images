"""Slideshow videos -> ordered stills, using synthetic videos made with the bundled ffmpeg."""

import subprocess
from pathlib import Path

import imagehash
import numpy as np
import pytest
from PIL import Image

from tt2x.video import extract_slides, ffmpeg_exe
from conftest import make_slide

FPS = 25


def write_video(frames: list[Image.Image], path: Path) -> Path:
    w, h = frames[0].size
    proc = subprocess.Popen(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path)],
        stdin=subprocess.PIPE,
    )
    for f in frames:
        proc.stdin.write(f.convert("RGB").tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return path


def slideshow(tmp_path, seeds, hold=(1.5, 2.0, 1.0, 2.5, 1.2), fade=0.4, intro_black=0.5):
    slides = [Image.open(make_slide(tmp_path / f"src{i}.jpg", s)).convert("RGB") for i, s in enumerate(seeds)]
    frames = [Image.new("RGB", slides[0].size, (0, 0, 0))] * int(intro_black * FPS)
    for i, img in enumerate(slides):
        frames += [img] * int(hold[i % len(hold)] * FPS)
        if i + 1 < len(slides):  # crossfade to the next picture
            n = int(fade * FPS)
            frames += [Image.blend(img, slides[i + 1], (k + 1) / (n + 1)) for k in range(n)]
    return write_video(frames, tmp_path / "clip.mp4"), slides


def test_slides_come_out_in_order(tmp_path):
    clip, slides = slideshow(tmp_path, [3, 14, 27, 41, 58, 66])
    out = extract_slides(clip, tmp_path / "out")
    assert [p.name for p in out] == [f"{i:02d}.jpg" for i in range(1, 7)]
    for got, want in zip(out, slides):
        assert imagehash.phash(Image.open(got)) - imagehash.phash(want) <= 6


def test_returning_picture_is_one_slide(tmp_path):
    # A, brief flash of B (shorter than a slide), A again, then C  ->  A, C
    a, b, c = (Image.open(make_slide(tmp_path / f"{n}.jpg", s)).convert("RGB") for n, s in (("a", 5), ("b", 33), ("c", 71)))
    frames = [a] * 40 + [b] * 5 + [a] * 30 + [c] * 50
    out = extract_slides(write_video(frames, tmp_path / "v.mp4"), tmp_path / "out")
    assert len(out) == 2


def test_normal_footage_is_not_a_slideshow(tmp_path):
    rng = np.random.default_rng(0)
    frames = [Image.fromarray(rng.integers(0, 255, (400, 320, 3), dtype=np.uint8)) for _ in range(75)]
    assert extract_slides(write_video(frames, tmp_path / "noise.mp4"), tmp_path / "out") == []


def test_ingest_queues_video_slides(tmp_path, cfg, db, monkeypatch):
    from tt2x import core, tiktok

    clip, _ = slideshow(tmp_path, [7, 19, 88])

    def fake_dl(car, dest):
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / "video.mp4"
        target.write_bytes(clip.read_bytes())
        return target

    monkeypatch.setattr(tiktok, "download_video", fake_dl)
    car = tiktok.Carousel(id="v1", author="a", caption="c", url="u", created_at=1, video_url="https://x/v.mp4")
    assert core.ingest(cfg, db, car) == "queued"
    row = db.queued()[0]
    assert row["image_count"] == 3
    assert not (cfg.downloads_path / "v1" / "video.mp4").exists()  # only stills are kept
