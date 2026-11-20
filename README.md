# tt2x: TikTok carousels & slideshow videos → scheduled X posts

Watches TikTok accounts that post **photo carousels** or **slideshow videos** (history timelines,
step-by-step stories…), gets every picture **in the original order**, and posts each carousel to X at the times you
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
```

### 1. Add the TikTok accounts
```bash
tt2x add-account @account_one @account_two https://www.tiktok.com/@account_three
```
Or edit `accounts:` in `config.yaml` (already set to the four watched accounts). Set your `timezone` and posting `slots` there too.

### 2. Add your X cookie
Set the environment variable `TWEETKIT_COOKIE="X_AUTH_TOKEN = …, X_CT0 = …"` (see
[how to get the values](#1-get-your-x-cookies)), or put them in a `cookies.json` next to
`config.yaml`. `cookies.json` is in `.gitignore`. **Never commit or share it**: it is a login to your account.

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

## @mentions

Each post tags up to 8 people or companies it is about (for example a SpaceX timeline →
@SpaceX @elonmusk @NASA).

1. **Gemini suggests them.** It reads the caption and the slide images. Get a free key at
   https://aistudio.google.com/apikey and add it as a repository secret named **`GEMINI_API_KEY`**.
   Without the key, posts simply go out without mentions.
2. **Each handle is checked on X before posting.** A handle is only used if the account exists and is
   verified or has at least `mention_min_followers` (10,000) followers. This blocks wrong guesses
   and impostor accounts.
3. **Mentions always fit.** If the text is too long for one post, the caption is shortened; the
   mentions and the credit line are kept.

A dry run shows the suggested handles before anything is posted. Change `max_mentions` or turn
mentions off with `mentions: false` in `config.yaml`.

## Holding items

Run the workflow with **manage** set to, for example, `hold 1` (keep #1 queued but don't post it),
`release 1` (back in line), `skip 4` (never post) or `retry 5` (after a failure). Item numbers come
from the queue list in any run's log.

## Slideshow videos

Video posts are downloaded and turned into one still per picture, in the order they appear on screen.
Fades and transitions are dropped, and a picture that briefly returns isn't doubled. Normal videos
(people talking, moving footage) aren't slideshows; they're detected and skipped (`skipped_video`).
Tune with `video_min_slide_seconds` and `video_change_threshold` in `config.yaml`. Always check a few
results in `downloads/<id>/` at first.

## When TikTok blocks the account scan

TikTok (and tikwm.com) often refuse to list accounts for cloud servers like GitHub's. When every
account fails, the run turns **red**, and the run page has a **tiktok-debug** artifact with a
screenshot of what TikTok showed. You have two ways around it:

- **Paste links:** Actions → tt2x → Run workflow, put TikTok links in *links* (space or comma
  separated). They're queued with the same order and duplicate checks and posted on schedule.
- **Scan from home, post from GitHub:** on your own computer, in the repo folder, run
  ```bash
  tt2x check --sync
  ```
  It loads the shared memory from the `tt2x-state` branch, finds and downloads new posts with your
  home connection, and pushes them back. GitHub then posts them at the scheduled times. Run it
  whenever you like, or schedule it (Task Scheduler / cron) every few hours. Your computer only
  needs to be on when it runs.

## Order of slides

Slides are saved as `01.jpg, 02.jpg, …` straight from TikTok's slide list, uploaded to X one by one
in that order, and attached in that order. With `max_images_per_post: 0`, all slides go in **one
post**. If X refuses that many images, it automatically posts a thread of 4 per post in order (the
same happens if you set `max_images_per_post: 4`).

## Run on GitHub Actions (no computer needed)

The workflow `.github/workflows/tt2x.yml` runs every 30 minutes on GitHub. It checks the accounts
in `config.yaml` (at most every `check_every_minutes`) and posts one carousel when a time slot is due.
The memory of what was already seen and posted (`tt2x.db` + queued images) is saved to the
`tt2x-state` branch after every run. **Don't delete that branch**, or old carousels could be queued again.

### 1. Get your X cookies
1. On a computer, open **x.com** in Chrome or Edge, logged in to the account that should post.
2. Press **F12** → **Application** tab (Firefox: **Storage**) → **Cookies** → `https://x.com`.
3. Copy the **Value** of `auth_token` and of `ct0`.
4. Stay logged in in that browser. Logging out kills the cookie, and you'd have to repeat this.

### 2. Save them as one secret
GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:
- Name: `TWEETKIT_COOKIE`
- Value: `X_AUTH_TOKEN = <auth_token value>, X_CT0 = <ct0 value>`

Other accepted value formats: `auth_token=…; ct0=…` (a copied Cookie header) or JSON.
Alternatively, use two secrets, `X_AUTH_TOKEN` and `X_CT0`.

### 3. Test, then let it run
- **Actions tab → tt2x → Run workflow** with *dry_run* ticked: it checks TikTok and prints what it
  would post, slide by slide, without touching X.
- After that, the 30-minute schedule posts for real at your slots. Untick *dry_run* to post the
  next due item right away.
- To pause: **Actions → tt2x → ⋯ → Disable workflow**.
- On the first run it queues the recent posts of every account (up to `posts_per_account_scan`
  each) and works through them oldest first. Use `newest_first` in `config.yaml` to post latest first.

If the log shows TikTok listing errors, GitHub's servers are probably being blocked by TikTok.
Run `tt2x run` on your own computer instead, with the same secret as an environment variable
(`TWEETKIT_COOKIE`).

## Run it in the background (your own computer)

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
