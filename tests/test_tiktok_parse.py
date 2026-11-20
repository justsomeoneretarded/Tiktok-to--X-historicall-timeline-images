import json

from tt2x.tiktok import carousel_from_tikwm, parse_post_html


def page(item):
    data = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"statusCode": 0, "itemInfo": {"itemStruct": item}}}}
    return (
        '<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps(data)
        + "</script></html>"
    )


def test_page_keeps_slide_order():
    item = {
        "id": "7400000000000000001",
        "desc": "History of Rome in 5 slides #history",
        "createTime": 1730000000,
        "author": {"uniqueId": "romefacts"},
        "imagePost": {"images": [{"imageURL": {"urlList": [f"https://cdn/{i}.jpeg", "https://alt"]}} for i in range(1, 6)]},
    }
    car = parse_post_html(page(item))
    assert car.id == "7400000000000000001"
    assert car.author == "romefacts"
    assert car.image_urls == [f"https://cdn/{i}.jpeg" for i in range(1, 6)]
    assert car.is_photo
    assert car.url.endswith("/@romefacts/photo/7400000000000000001")


def test_video_has_no_slides():
    car = parse_post_html(page({"id": "1", "desc": "", "author": {"uniqueId": "a"}}))
    assert not car.is_photo


def test_tikwm_keeps_slide_order():
    car = carousel_from_tikwm({
        "video_id": "99", "title": "t", "create_time": 5,
        "author": {"unique_id": "acct"}, "images": ["a", "b", "c"],
    })
    assert car.image_urls == ["a", "b", "c"] and car.author == "acct" and car.id == "99"


def test_video_post_has_video_url():
    item = {"id": "8", "desc": "", "author": {"uniqueId": "a"}, "video": {"playAddr": "https://v/play.mp4"}}
    car = parse_post_html(page(item))
    assert car.kind == "video" and car.video_url == "https://v/play.mp4" and car.url.endswith("/video/8")


def test_tikwm_video():
    car = carousel_from_tikwm({"video_id": "9", "author": {"unique_id": "a"}, "play": "/video/media/play/9.mp4"})
    assert car.kind == "video" and car.video_url == "https://www.tikwm.com/video/media/play/9.mp4"


def test_profile_video_count():
    from tt2x.tiktok import parse_profile_video_count
    data = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"stats": {"videoCount": 5}}}}}
    html = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">' + json.dumps(data) + "</script>"
    assert parse_profile_video_count(html) == 5


def test_blocked_listing_raises(monkeypatch):
    import pytest
    from tt2x import tiktok
    monkeypatch.setattr(tiktok, "_tikwm_user_posts", lambda h, n: (_ for _ in ()).throw(RuntimeError("403")))
    monkeypatch.setattr(tiktok, "_playwright_user_posts", lambda h, n, d=None: [])
    monkeypatch.setattr(tiktok, "profile_video_count", lambda h: 5)
    with pytest.raises(tiktok.TikTokError, match="blocked"):
        tiktok.list_account_posts("acct")
    monkeypatch.setattr(tiktok, "profile_video_count", lambda h: 0)
    assert tiktok.list_account_posts("acct") == []
