import pytest

from tt2x.x_poster import load_cookie_dict, parse_cookie_string

FORMATS = [
    "X_AUTH_TOKEN = abc123 , X_CT0 = def456",
    "X_AUTH_TOKEN=abc123,X_CT0=def456",
    'X_AUTH_TOKEN = "abc123", X_CT0 = "def456"',
    "X_AUTH_TOKEN = abc123\nX_CT0 = def456\n",
    "auth_token=abc123; ct0=def456; lang=en",
    "Cookie: guest_id=v1; auth_token=abc123; ct0=def456",
    "x_auth_token: abc123, x_ct0: def456",
    '{"auth_token": "abc123", "ct0": "def456"}',
    '[{"name": "auth_token", "value": "abc123"}, {"name": "ct0", "value": "def456"}]',
]


@pytest.mark.parametrize("text", FORMATS)
def test_parse_formats(text):
    c = parse_cookie_string(text)
    assert c["auth_token"] == "abc123" and c["ct0"] == "def456"


def test_secret_env_wins(monkeypatch, tmp_path):
    (tmp_path / "cookies.json").write_text('{"auth_token": "file", "ct0": "file"}')
    monkeypatch.setenv("TWEETKIT_COOKIE", "X_AUTH_TOKEN = env1, X_CT0 = env2")
    assert load_cookie_dict(tmp_path / "cookies.json") == {"auth_token": "env1", "ct0": "env2"}


def test_separate_env_vars(monkeypatch, tmp_path):
    monkeypatch.delenv("TWEETKIT_COOKIE", raising=False)
    monkeypatch.setenv("X_AUTH_TOKEN", "a")
    monkeypatch.setenv("X_CT0", "b")
    assert load_cookie_dict(tmp_path / "none.json") == {"auth_token": "a", "ct0": "b"}


def test_missing_ct0_is_clear_and_hides_values(monkeypatch, tmp_path):
    monkeypatch.setenv("TWEETKIT_COOKIE", "X_AUTH_TOKEN = supersecret")
    with pytest.raises(ValueError) as e:
        load_cookie_dict(tmp_path / "none.json")
    assert "ct0" in str(e.value) and "supersecret" not in str(e.value)


def test_nothing_configured(monkeypatch, tmp_path):
    for k in ("TWEETKIT_COOKIE", "X_AUTH_TOKEN", "X_CT0"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(FileNotFoundError):
        load_cookie_dict(tmp_path / "none.json")


def test_cookie_header_for_tweetkit():
    from tt2x.x_poster import cookie_header
    c = parse_cookie_string("X_AUTH_TOKEN = abc , X_CT0 = def")
    assert cookie_header(c) == "auth_token=abc; ct0=def"
    c = parse_cookie_string("guest_id=v1; ct0=def; twid=u%3D42; auth_token=abc")
    assert cookie_header(c).startswith("auth_token=abc; ct0=def") and "twid=u%3D42" in cookie_header(c)


def test_tweetkit_accepts_our_cookie_and_reads_it_back():
    from tweetkit_x import TweetKit
    from tt2x.x_poster import cookie_header
    tk = TweetKit(cookie=cookie_header(parse_cookie_string("X_AUTH_TOKEN = abc, X_CT0 = def")))
    info = tk.whoami()  # local check, no network
    assert info["ready"] and info["ct0_len"] == 3


def test_poster_builds_client_from_secret(monkeypatch):
    from tt2x.x_poster import XPoster
    monkeypatch.setenv("TWEETKIT_COOKIE", "X_AUTH_TOKEN = abc, X_CT0 = def")
    client = XPoster(None)._get_client()
    assert client.whoami()["ready"]


def test_bad_secret_error_hides_values(monkeypatch):
    from tt2x.x_poster import XPoster
    monkeypatch.setenv("TWEETKIT_COOKIE", "X_AUTH_TOKEN = secretvalue")
    with pytest.raises(ValueError) as e:
        XPoster(None)._get_client()
    assert "secretvalue" not in str(e.value)

