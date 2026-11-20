from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tt2x.config import Config
from tt2x.db import DB


def make_slide(path: Path, seed: int) -> Path:
    """A distinct picture per seed (different shapes, so perceptual hashes differ)."""
    img = Image.new("RGB", (320, 400), (240, 235, 220))
    d = ImageDraw.Draw(img)
    for k in range(6):
        x = (seed * 53 + k * 97) % 260
        y = (seed * 31 + k * 71) % 340
        d.rectangle([x, y, x + 40 + (seed % 5) * 8, y + 50], fill=((seed * 40) % 255, k * 40, 120))
    d.ellipse([(seed * 17) % 200, (seed * 29) % 300, (seed * 17) % 200 + 90, (seed * 29) % 300 + 90], fill=(20, 20, 20))
    img.save(path, "JPEG", quality=95)
    return path


@pytest.fixture
def cfg(tmp_path):
    c = Config(path=tmp_path / "config.yaml")
    c.timezone = "UTC"
    return c


@pytest.fixture
def db(cfg):
    return DB(cfg.db_path)


@pytest.fixture
def fake_download(monkeypatch):
    """Replace network downloads: image 'URLs' look like 'seed:<n>' and become generated slides."""
    from tt2x import tiktok

    def fake(car, dest):
        dest.mkdir(parents=True, exist_ok=True)
        files = []
        for i, url in enumerate(car.image_urls, start=1):
            files.append(make_slide(dest / f"{i:02d}.jpg", int(url.split(":")[1])))
        return files

    monkeypatch.setattr(tiktok, "download_images", fake)
