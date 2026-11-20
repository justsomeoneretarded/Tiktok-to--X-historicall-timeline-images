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
