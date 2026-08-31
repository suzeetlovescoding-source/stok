# Stok — TikTok & Instagram downloader

Developed by SuJit Thapa.

A clean, custom-designed downloader that runs entirely on your own
computer. Paste a TikTok or Instagram link, see the caption/author/stats,
then save the video (with or without the TikTok watermark), the original
sound, a single photo, or every item in a carousel/slideshow post —
straight into your browser's download folder.

`/api/info` looks at the pasted URL and automatically routes it to the
right platform:

- **TikTok** — ports the core logic of
  [ssinsta/tiktok-downloader](https://github.com/ssinsta/tiktok-downloader)
  (a C#/WinForms desktop app, MIT License — see `LICENSE`) to Python.
- **Instagram** — uses
  [instaloader](https://github.com/instaloader/instaloader) (MIT
  License), a maintained Python library, to read a public post's
  metadata and media URLs.

Either way, `/api/download` streams the actual bytes back as a real
download — it's a validated proxy, only re-streaming URLs that were just
returned by `/api/info` and that resolve to a real TikTok/tikwm/Instagram
media host, so it can't be used as an open proxy for arbitrary URLs.

## 1. Setup

```bash
cd stok
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Run it

```bash
python app.py
```

Open **http://127.0.0.1:5000**.

## 3. Using it

1. Paste a link — TikTok (`tiktok.com/...`, `vm.tiktok.com/...`,
   `vt.tiktok.com/...`) or Instagram (`instagram.com/p/...`,
   `/reel/...`, `/tv/...`) both work.
2. Click **Fetch** — Stok shows the caption, creator, and stats, and
   lists whatever's actually downloadable: for TikTok, video without
   watermark, video with watermark, the original sound, or a grid of
   photos for slideshow posts; for Instagram, a single photo, a single
   video/Reel, or a grid of every item in a carousel post.
3. Click an option — it streams straight to your browser with a live
   progress bar.

## Project layout

```
stok/
├── app.py              Flask app: pages + API (/api/info, /api/download)
├── requirements.txt
├── templates/
│   └── index.html
├── static/
│   ├── style.css
│   └── script.js
└── LICENSE              Original TikTok-downloader project's MIT license
```

## About the TikTok lookup

TikTok doesn't publish an official public API for this, and direct
attempts to call TikTok's own mobile-app API from a script (what the
original desktop app does) run into TikTok's anti-bot measures pretty
quickly — rate-limiting a shared device identity, or silently returning
an empty response instead of a clear error. So the TikTok path in
`/api/info` actually tries two things in order:

1. **tikwm.com's free public API** (the primary path) — the same
   resolver most "download TikTok without watermark" sites use under the
   hood, since it handles TikTok's request-signing on its own backend
   instead of asking every caller to spoof it.
2. **TikTok's own mobile API directly**, the way the original desktop app
   did (with a per-run randomized device identity, and automatic retries
   on HTTP 429) — used only if tikwm.com is unreachable.

Both are unofficial and can break if TikTok or tikwm.com change something
on their end. If a link fails, the error message shows what each path
actually said — that's the fastest way to tell whether it's a one-off (a
specific link is private/removed) or both services are having a bad day.
The relevant constants are all in `app.py`: `TIKWM_API_URL` for the
primary path, `FEED_API_URL` / `FEED_API_STATIC_PARAMS` /
`MOBILE_USER_AGENT` for the fallback.

## About the Instagram lookup

instaloader is a real, maintained library (unlike the hand-written TikTok
API calls above), but Instagram itself is stricter than TikTok about
anonymous, unauthenticated access — a public post's page *might* load
fine without logging in, but Instagram can also throttle or flatly
require a login for the same kind of request, and this can vary by post,
by time, and by how many requests have come from your network recently.

If you hit that consistently, you can give Stok a logged-in session
without ever putting a password in this project's code:

```bash
pip install instaloader   # already covered by requirements.txt
instaloader --login=<your-username>
```

That command logs in interactively (prompting for your password and any
2FA code right there in your terminal — Stok never sees it) and saves a
reusable session file in instaloader's own config directory. Then set an
environment variable before starting Stok:

```bash
export INSTAGRAM_SESSION_USERNAME=<your-username>   # Windows: set INSTAGRAM_SESSION_USERNAME=...
python app.py
```

Stok will load that saved session automatically. Without it, Stok simply
tries anonymously, which works for plenty of public posts.

## A note on legitimate use

Stok is meant for saving content you own, that's Creative-Commons or
otherwise licensed for reuse, or that you otherwise have the right to
download. Respect copyright law, and TikTok's and Instagram's Terms of
Service.

## Troubleshooting

- **"Couldn't reach TikTok" / "Couldn't read that link"** — check the
  link opens in a normal browser first; if it does and this still fails,
  TikTok or tikwm.com likely changed something on their end.
- **"Instagram is asking for a login..."** — see the Instagram section
  above; some posts need it, some don't.
- **A photo slideshow / carousel shows no single video download** —
  that's expected; those posts don't have one combined video file, only
  the individual items, downloadable one at a time from the grid.
- **Port already in use** — set `app.run(port=5001)` (or any free port)
  in `app.py`.
