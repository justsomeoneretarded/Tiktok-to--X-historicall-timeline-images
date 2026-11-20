from tt2x.core import ingest
from tt2x.tiktok import Carousel


def car(pid, seeds, author="acct", t=0):
    return Carousel(id=pid, author=author, caption=f"post {pid}", url=f"u/{pid}",
                    created_at=t, image_urls=[f"seed:{s}" for s in seeds])


def test_same_tiktok_id_is_ignored(cfg, db, fake_download):
    assert ingest(cfg, db, car("1", [1, 2, 3])) == "queued"
    assert ingest(cfg, db, car("1", [1, 2, 3])) == "seen"
    assert len(db.queued()) == 1


def test_same_images_other_account_is_duplicate(cfg, db, fake_download):
    assert ingest(cfg, db, car("1", [1, 2, 3, 4])) == "queued"
    assert ingest(cfg, db, car("2", [1, 2, 3, 4], author="reuploader")) == "skipped_duplicate"
    assert len(db.queued()) == 1


def test_mostly_same_images_is_duplicate(cfg, db, fake_download):
    ingest(cfg, db, car("1", [1, 2, 3, 4, 5]))
    # one extra slide added to the end: 5 of 6 slides match (>= 80%)
    assert ingest(cfg, db, car("2", [1, 2, 3, 4, 5, 6])) == "skipped_duplicate"


def test_different_carousels_both_queued(cfg, db, fake_download):
    assert ingest(cfg, db, car("1", [1, 2, 3])) == "queued"
    assert ingest(cfg, db, car("2", [10, 11, 12])) == "queued"
    assert len(db.queued()) == 2


def test_duplicate_of_already_posted_is_skipped(cfg, db, fake_download):
    ingest(cfg, db, car("1", [1, 2, 3]))
    pid = db.queued()[0]["id"]
    db.claim_for_posting(pid)
    db.mark_posted(pid, ["123"])
    assert ingest(cfg, db, car("9", [1, 2, 3])) == "skipped_duplicate"


def test_videos_are_skipped_once(cfg, db, fake_download):
    v = Carousel(id="5", author="a", caption="", url="", created_at=0, image_urls=[])
    assert ingest(cfg, db, v) == "skipped_video"
    assert ingest(cfg, db, v) == "seen"
