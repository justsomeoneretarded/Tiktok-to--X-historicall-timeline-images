import json

import httpx
import pytest

from tt2x import core
from tt2x.mentions import parse_suggestions, suggest_handles
from tt2x.tiktok import Carousel
from tt2x.x_poster import XPoster


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def gemini_reply(items):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(items)}]}}]}


def test_parse_cleans_and_dedupes():
    data = [{"name": "SpaceX", "x_handle": "@SpaceX"}, {"name": "Elon", "x_handle": "elonmusk"},
            {"name": "x", "x_handle": "spacex"}, {"name": "Someone", "x_handle": None},
            {"name": "bad", "x_handle": "not a handle!"}, {"name": "NASA", "x_handle": "NASA"}]
    assert parse_suggestions(data, 8) == ["SpaceX", "elonmusk", "NASA"]
    assert parse_suggestions(data, 2) == ["SpaceX", "elonmusk"]


def test_gemini_request_and_key_not_leaked(tmp_path):
    from conftest import make_slide
    img = make_slide(tmp_path / "01.jpg", 3)
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=gemini_reply([{"name": "SpaceX", "x_handle": "SpaceX"}]))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = suggest_handles("Evolution of SpaceX", [img], limit=8, model="m", api_key="k123", client=client)
    assert out == ["SpaceX"]
    assert seen["key"] == "k123"
    parts = seen["body"]["contents"][0]["parts"]
    assert "Evolution of SpaceX" in parts[0]["text"] and "inline_data" in parts[1]


def test_gemini_failure_never_blocks(tmp_path, caplog, monkeypatch):
    import tt2x.mentions as m
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom k123")))
    assert suggest_handles("c", [], limit=8, model="m", api_key="k123", client=client) is None
    assert "k123" not in caplog.text


def test_no_key_means_no_mentions():
    assert suggest_handles("c", [], limit=8, model="m") is None


def row(caption, author="acct"):
    return {"caption": caption, "author": author, "url": "u"}


def test_long_caption_is_trimmed_but_mentions_and_credit_stay(cfg):
    handles = ["SpaceX", "elonmusk", "NASA", "Boeing", "ULA", "BlueOrigin", "RocketLab", "ESA"]
    text = core.render_caption(cfg, row("word " * 100), handles)
    assert len(text) <= 280
    assert all("@" + h in text for h in handles)
    assert text.endswith("via acct on TikTok") and "…" in text


def test_no_mentions_leaves_no_blank_gap(cfg):
    text = core.render_caption(cfg, row("Short caption"), [])
    assert text == "Short caption\n\n📷 via acct on TikTok"


class Client:
    """TweetKit-X-like: user_profile -> {ok, username, followers, verified}."""
    users = {"spacex": ("SpaceX", True, 40_000_000), "elonmusk": ("elonmusk", True, 200_000_000),
             "tiny": ("tiny", False, 12), "nasa": ("NASA", False, 80_000_000)}

    def __init__(self):
        self.tweets = []

    def user_profile(self, username):
        u = self.users.get(username.lower())
        if not u:
            return {"ok": False, "error": "not found"}
        return {"ok": True, "username": u[0], "verified": u[1], "followers": u[2]}

    def upload_media(self, path, media_type=None, alt_text=None):
        return "m"

    def create_tweet(self, text, media_ids=None, reply_to=None, quote_tweet_id=None):
        self.tweets.append(text)
        return {"ok": True, "id": "1"}


def test_post_mentions_only_verified_existing_accounts(cfg, db, fake_download, monkeypatch):
    core.ingest(cfg, db, Carousel(id="1", author="acct", caption="Evolution of SpaceX", url="u",
                                  created_at=1, image_urls=["seed:1", "seed:2"]))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(core, "suggest_handles",
                        lambda *a, **k: ["SpaceX", "elonmusk", "madeup_handle", "tiny", "NASA"])
    client = Client()
    assert core.post_next(cfg, db, XPoster(None, client=client)).startswith("posted")
    assert "@SpaceX @elonmusk @NASA" in client.tweets[0]
    assert "@tiny" not in client.tweets[0] and "@madeup_handle" not in client.tweets[0]


def test_dry_run_shows_suggestions_and_asks_gemini_once(cfg, db, fake_download, monkeypatch):
    core.ingest(cfg, db, Carousel(id="1", author="acct", caption="c", url="u", created_at=1, image_urls=["seed:1"]))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []
    monkeypatch.setattr(core, "suggest_handles", lambda *a, **k: calls.append(1) or ["SpaceX"])
    assert "['SpaceX']" in core.post_next(cfg, db, None, dry_run=True)
    core.post_next(cfg, db, None, dry_run=True)
    assert len(calls) == 1  # remembered in the DB


def test_sample_keeps_first_and_last():
    from tt2x.mentions import _sample
    s = _sample(list(range(24)), 10)
    assert len(s) == 10 and s[0] == 0 and s[-1] == 23 and s == sorted(s)


def test_gemini_retries_when_busy(monkeypatch):
    import tt2x.mentions as m
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json=gemini_reply([{"name": "SpaceX", "x_handle": "SpaceX"}]))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert suggest_handles("c", [], limit=8, model="m", api_key="k", client=client) == ["SpaceX"]
    assert len(calls) == 3


def test_gemini_gives_up_after_retries(monkeypatch):
    import tt2x.mentions as m
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert suggest_handles("c", [], limit=8, model="m", api_key="k", client=client) is None
