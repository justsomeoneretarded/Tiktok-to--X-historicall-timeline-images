from pathlib import Path

from tt2x.core import ingest, post_next
from tt2x.tiktok import Carousel
from tt2x.x_poster import XPoster, chunk, fit_text


class FakeClient:
    """Behaves like TweetKit-X: upload_media -> id, create_tweet -> {ok, id} / {ok: False, status, error}."""

    def __init__(self, max_images=None, fail=False, ambiguous=False):
        self.uploads: list[str] = []
        self.folders: list[str] = []
        self.tweets: list[dict] = []
        self.max_images = max_images
        self.fail = fail
        self.ambiguous = ambiguous

    def upload_media(self, path, media_type=None, alt_text=None):
        self.uploads.append(Path(path).name)
        self.folders.append(Path(path).parent.name)
        return f"m-{Path(path).stem}"

    def create_tweet(self, text, media_ids=None, reply_to=None, quote_tweet_id=None):
        if self.fail:
            return {"ok": False, "status": 403, "error": "Authorization: denied"}
        if self.ambiguous:
            return {"ok": False, "status": 200, "error": '{"data": {"create_tweet": {}}}'}
        if self.max_images and len(media_ids) > self.max_images:
            return {"ok": False, "status": 200, "error": "Your media IDs are invalid."}
        self.tweets.append({"text": text, "media_ids": list(media_ids), "reply_to": reply_to})
        rid = str(1000 + len(self.tweets))
        return {"ok": True, "id": rid, "url": f"https://x.com/i/status/{rid}"}


def queue_one(cfg, db, n=7, pid="1"):
    ingest(cfg, db, Carousel(id=pid, author="acct", caption="Timeline of X", url="u",
                             created_at=int(pid), image_urls=[f"seed:{i + int(pid) * 50}" for i in range(n)]))


def test_all_slides_in_one_post_in_order(cfg, db, fake_download, monkeypatch):
    monkeypatch.setattr("tt2x.x_poster.time.sleep", lambda s: None)
    queue_one(cfg, db, 7)
    client = FakeClient()
    out = post_next(cfg, db, XPoster(Path("x"), client=client))
    assert out.startswith("posted")
    assert client.uploads == [f"{i:02d}.jpg" for i in range(1, 8)]
    assert len(client.tweets) == 1
    assert client.tweets[0]["media_ids"] == [f"m-{i:02d}" for i in range(1, 8)]
    assert "via acct on TikTok" in client.tweets[0]["text"]


def test_never_posts_same_item_twice(cfg, db, fake_download, monkeypatch):
    monkeypatch.setattr("tt2x.x_poster.time.sleep", lambda s: None)
    queue_one(cfg, db, 3, pid="1")
    queue_one(cfg, db, 3, pid="2")
    client = FakeClient()
    poster = XPoster(Path("x"), client=client)
    post_next(cfg, db, poster)
    post_next(cfg, db, poster)
    assert post_next(cfg, db, poster) == "nothing queued"
    assert len(client.tweets) == 2
    assert {r["status"] for r in db.all()} == {"posted"}
    assert client.folders == ["1", "1", "1", "2", "2", "2"]  # each carousel uploaded once


def test_falls_back_to_ordered_thread(cfg, db, fake_download, monkeypatch):
    monkeypatch.setattr("tt2x.x_poster.time.sleep", lambda s: None)
    queue_one(cfg, db, 10)
    client = FakeClient(max_images=4)
    post_next(cfg, db, XPoster(Path("x"), client=client))
    groups = [t["media_ids"] for t in client.tweets]
    assert groups == [[f"m-{i:02d}" for i in r] for r in ([1, 2, 3, 4], [5, 6, 7], [8, 9, 10])]  # balanced, order kept
    assert client.tweets[0]["reply_to"] is None
    assert client.tweets[1]["reply_to"] == "1001" and client.tweets[2]["reply_to"] == "1002"


def test_failure_is_not_retried_automatically(cfg, db, fake_download):
    queue_one(cfg, db, 3)
    out = post_next(cfg, db, XPoster(Path("x"), client=FakeClient(fail=True)))
    assert "failed" in out
    assert db.all()[0]["status"] == "failed"
    assert post_next(cfg, db, XPoster(Path("x"), client=FakeClient())) == "nothing queued"


def test_dry_run_touches_nothing(cfg, db, fake_download):
    queue_one(cfg, db, 3)
    out = post_next(cfg, db, None, dry_run=True)
    assert "01.jpg, 02.jpg, 03.jpg" in out
    assert db.all()[0]["status"] == "queued"


def test_helpers():
    assert chunk([1, 2, 3, 4, 5], 0) == [[1, 2, 3, 4, 5]]
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert len(fit_text("a" * 400, False)) == 280
    assert len(fit_text("a" * 400, True)) == 400


def test_ambiguous_answer_is_never_retried(cfg, db, fake_download):
    queue_one(cfg, db, 6)  # > 4 images: an ambiguous answer must NOT trigger the thread fallback either
    client = FakeClient(ambiguous=True)
    out = post_next(cfg, db, XPoster(Path("x"), client=client))
    assert "failed" in out and "check X" in out
    assert db.all()[0]["status"] == "failed"
    assert client.tweets == [] and len(client.uploads) == 6


def test_balanced_split_from_x_limit(cfg, db, fake_download, monkeypatch):
    from tt2x.x_poster import balanced_chunks, media_limit_from_error
    monkeypatch.setattr("tt2x.x_poster.time.sleep", lambda s: None)
    assert media_limit_from_error("BadRequest: Tweet must not have more than 20 mediaIds. (324)") == 20
    assert [len(g) for g in balanced_chunks(list(range(24)), 20)] == [12, 12]
    assert [len(g) for g in balanced_chunks(list(range(41)), 20)] == [14, 14, 13]

    class Limit20(FakeClient):
        def create_tweet(self, text, media_ids=None, reply_to=None, quote_tweet_id=None):
            if len(media_ids) > 20:
                return {"ok": False, "status": 200,
                        "error": "BadRequest: Tweet must not have more than 20 mediaIds. (324)"}
            return super().create_tweet(text, media_ids, reply_to)

    queue_one(cfg, db, 24)
    client = Limit20()
    post_next(cfg, db, XPoster(Path("x"), client=client))
    groups = [t["media_ids"] for t in client.tweets]
    assert [len(g) for g in groups] == [12, 12]
    assert sum(groups, []) == [f"m-{i:02d}" for i in range(1, 25)]  # order kept
