"""The GitHub Actions cycle: `run --once`, then `prune`, repeated, against the same state folder."""

from datetime import datetime, timezone

from tt2x import cli, core, tiktok
from tt2x.tiktok import Carousel


def setup(tmp_path, monkeypatch, fake_download, posts):
    (tmp_path / "config.yaml").write_text(
        "accounts: ['@acct']\ncheck_every_minutes: 60\ndata_dir: state\n"
        "schedule:\n  timezone: UTC\n  slots: ['09:00', '14:00', '20:00']\n"
    )
    monkeypatch.setattr(tiktok, "list_account_posts", lambda h, n: list(posts))
    sent = []

    class Poster:
        def post(self, text, files):
            sent.append([f.parent.name + "/" + f.name for f in files])
            return [str(len(sent))]

    monkeypatch.setattr(cli, "_poster", lambda cfg: Poster())
    monkeypatch.setattr("tt2x.core.time.sleep", lambda s: None)
    return str(tmp_path / "config.yaml"), sent


def at(monkeypatch, h, m=0):
    now = datetime(2026, 11, 20, h, m, tzinfo=timezone.utc)
    monkeypatch.setattr(cli, "utcnow", lambda: now)
    monkeypatch.setattr("time.time", lambda: now.timestamp())


def car(pid, seeds):
    return Carousel(id=pid, author="acct", caption=pid, url="u", created_at=int(pid),
                    image_urls=[f"seed:{s}" for s in seeds])


def test_repeated_runs_post_each_carousel_once_in_order(tmp_path, monkeypatch, fake_download):
    cfgp, sent = setup(tmp_path, monkeypatch, fake_download,
                       [car("2", [20, 21, 22]), car("1", [10, 11, 12]), car("3", [10, 11, 12])])

    at(monkeypatch, 8, 0)    # before any slot today... yesterday's 20:00 is too old -> no post
    cli.main(["-c", cfgp, "run", "--once"])
    assert sent == []

    at(monkeypatch, 9, 10)   # 09:00 slot due -> oldest first (#1)
    cli.main(["-c", cfgp, "run", "--once"])
    cli.main(["-c", cfgp, "prune"])
    at(monkeypatch, 9, 40)   # same slot again -> nothing
    cli.main(["-c", cfgp, "run", "--once"])
    at(monkeypatch, 14, 30)  # next slot -> #2
    cli.main(["-c", cfgp, "run", "--once"])
    at(monkeypatch, 20, 5)   # nothing left: "3" is a duplicate of "1"
    cli.main(["-c", cfgp, "run", "--once"])

    assert sent == [["1/01.jpg", "1/02.jpg", "1/03.jpg"], ["2/01.jpg", "2/02.jpg", "2/03.jpg"]]
    assert not (tmp_path / "state" / "downloads" / "1").exists()  # pruned after posting


def test_check_is_throttled_across_runs(tmp_path, monkeypatch, fake_download):
    cfgp, _ = setup(tmp_path, monkeypatch, fake_download, [])
    calls = []
    monkeypatch.setattr(cli, "check_accounts", lambda cfg, db: calls.append(1) or {})
    for h, m in [(10, 0), (10, 30), (11, 0), (11, 30)]:
        at(monkeypatch, h, m)
        cli.main(["-c", cfgp, "run", "--once"])
    assert len(calls) == 2  # 10:00 and 11:00 only
