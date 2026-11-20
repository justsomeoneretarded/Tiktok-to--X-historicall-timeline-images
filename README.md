# tt2x: TikTok carousels → scheduled X posts

Watches TikTok accounts that post **photo carousels** (history timelines, step-by-step stories…),
downloads every slide **in the original order**, and posts each carousel to X at the times you
choose, **never posting the same thing twice**.

```
TikTok accounts ─▶ check for new carousels ─▶ download slides 01..NN ─▶ duplicate check ─▶ queue
                                                                                          │
                                              your posting times (09:00, 14:00, 20:00) ◀──┘
                                                                │
                                              one X post with all slides, in order
```

## Setup

Needs Python 3.10+ on a computer or server that stays on.

```bash
git clone <this repo> && cd <repo>
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
tt2x init            # creates config.yaml
```

### 1. Add the TikTok accounts
```bash
tt2x add-account @account_one @account_two https://www.tiktok.com/@account_three
```
Or edit `accounts:` in `config.yaml`. Set your `timezone` and posting `slots` there too.

### 2. Add your X cookie
Log in to x.com in your browser and export the cookies to `cookies.json` next to `config.yaml`,
for example with a "cookie editor" browser extension (its JSON export works as-is). A minimal file:
```json
{ "auth_token": "…", "ct0": "…" }
```
`cookies.json` is in `.gitignore`. **Never commit or share it**: it is a login to your account.

## Use

| Command | What it does |
|---|---|
| `tt2x check` | Scan all accounts once; download and queue new carousels |
| `tt2x add-url <link> …` | Queue specific TikTok posts by hand (short `vm.tiktok.com` links work) |
| `tt2x queue` / `tt2x queue --all` | See what's queued and when it will post / full history |
| `tt2x show <id>` | Details and slide files of one item |
| `tt2x post-next --dry-run` | Show exactly what would be posted next, without posting |
| `tt2x post-next [--id N]` | Post the next item (or item N) right now |
| `tt2x skip <id>` | Never post this item |
| `tt2x retry <id>` | Put a failed item back in the queue |
| `tt2x run` | **Leave this running**: checks accounts every N minutes and posts at each slot |

Start with `tt2x check`, then `tt2x queue`, then `tt2x post-next --dry-run`. Look in `downloads/<id>/`
to confirm the slide order. When it looks right, run `tt2x run`.

## How it avoids posting twice

1. **Same TikTok post**: every post id ever seen is stored in `tt2x.db`. A post seen again is ignored.
2. **Same images, different post or account**: each slide gets a perceptual fingerprint. If ≥80% of a
   new carousel's slides match a carousel already queued or posted (even re-encoded or slightly
   cropped), it is marked `skipped_duplicate`. Tune with `dedupe_max_distance` / `dedupe_match_ratio`.
3. **Crashes and errors**: an item is marked `posting` before it is sent and `posted` right after.
   Failed or interrupted items are **never retried automatically**, because the post may have gone
   through. Check X, then `tt2x retry <id>` or `tt2x skip <id>`.
4. **Each time slot fires once**, even after a restart. Slots missed while the computer was off for
   longer than `missed_slot_grace_minutes` are skipped instead of posting a burst.

Keep `tt2x.db`: it is the memory of what was posted. Deleting it would let old carousels be queued again.

## Order of slides

Slides are saved as `01.jpg, 02.jpg, …` straight from TikTok's slide list, uploaded to X one by one
in that order, and attached in that order. With `max_images_per_post: 0`, all slides go in **one
post**. If X refuses that many images, it automatically posts a thread of 4 per post in order (the
same happens if you set `max_images_per_post: 4`).

## Run it in the background

**Linux (systemd)**, in `~/.config/systemd/user/tt2x.service`:
```ini
[Unit]
Description=tt2x
[Service]
WorkingDirectory=/path/to/repo
ExecStart=/path/to/repo/.venv/bin/tt2x run
Restart=always
RestartSec=60
[Install]
WantedBy=default.target
```
```bash
systemctl --user enable --now tt2x
```

**Windows**: Task Scheduler → Create Task → trigger "At log on" → action
`C:\path\to\repo\.venv\Scripts\tt2x.exe` with argument `run` and "Start in" set to the repo folder.

## Where TikTok data comes from

Account listings use the public tikwm.com API. If it fails, a headless browser is used
(`pip install -e '.[browser]' && playwright install chromium`). Single posts are read from TikTok's
page and fall back to tikwm. TikTok changes its site often, so if `check` starts failing, update this tool.

## Be fair to creators

These are other people's images. Keep the `via @{author}` credit in `caption_template`, and ideally
get permission from the accounts you repost. Posting through cookies isn't X's official API, so keep
the posting rate modest.

## Development
```bash
pip install -e '.[dev]' && pytest
```
