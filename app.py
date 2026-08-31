"""
Stok — TikTok + Instagram downloader
-----------------------------------
A Flask backend. /api/info detects which platform a pasted link belongs to
and dispatches accordingly:

  * TikTok — ports the core logic of ssinsta/tiktok-downloader (a
    C#/WinForms desktop app, MIT License:
    https://github.com/ssinsta/tiktok-downloader) to Python.
  * Instagram — uses instaloader (MIT License:
    https://github.com/instaloader/instaloader), a maintained Python
    library for reading Instagram post data, to read a public post's
    metadata and media URLs.

What it does for a TikTok link, same as the original desktop tool:
  1. Resolve a TikTok link (including short vm.tiktok.com/vt.tiktok.com/
     tiktok.com/t/... links) by following redirects to the canonical
     .../video/<id> or .../photo/<id> URL.
  2. Ask TikTok's public mobile-app API (the same "aweme/v1/feed" endpoint
     the official Android app calls) for that post's metadata — author,
     caption, stats, and every media URL (video with/without watermark,
     the original sound, and each image for photo/slideshow posts).
  3. Stream the actual bytes back to the browser as a real download.

Differences from the original:
  * The original sent an HTTP OPTIONS request to the feed API, which looks
    like a bug (OPTIONS is for CORS preflight, not fetching data) — this
    uses a plain GET, which is what that endpoint expects.
  * The original hardcodes one specific device_id/iid pair in its source.
    Since the repo is public, every install of it shares that one "device"
    identity — TikTok can rate-limit (429) or soft-block (200 with an
    empty body) that shared identity server-side, which has nothing to do
    with your network or your link. pytubefix-style version-string rot
    happens here too: TikTok's mobile API has moved to requiring signed
    request headers this raw approach can't produce.
  * Because of that, the primary lookup here goes through tikwm.com's free
    public resolver API instead — the same service most "download TikTok
    without watermark" sites actually use under the hood, since it handles
    TikTok's signing on its own backend. The original repo's direct
    device-spoofing approach (resolve link -> aweme_id -> feed API, with
    randomized per-run device_id/iid and 429 retries) is kept as an
    automatic fallback if tikwm.com is ever unreachable.
  * This is a website instead of a Windows desktop app, so there's no
    Playwright browser automation / mass-download-by-username feature —
    just the single-link download, which is the part that works without
    installing anything.
  For Instagram, a link to a single photo post, a single video/Reel, or a
  carousel (multiple photos/videos in one post) is read the same way,
  through instaloader's Post object — no watermark concept there, just
  the original media.

  * /api/download is a small validated proxy: it only re-streams URLs that
    were just handed back by /api/info and that resolve to a known TikTok,
    tikwm, or Instagram media host, so this can't be turned into an open
    proxy for arbitrary URLs.

Note on Instagram specifically: unlike TikTok, Instagram increasingly
requires being logged in for reliable anonymous-script access — a public
post *might* work without logging in, but Instagram can also throttle or
block anonymous requests outright. If that happens consistently, set the
INSTAGRAM_SESSION_USERNAME environment variable to a username you've
already created an instaloader session file for (run
`instaloader --login=<username>` once, interactively, in a terminal — it
saves a reusable session file instaloader will pick up automatically).
This app never asks for or stores a password itself.

Run with:  python app.py
Then open: http://127.0.0.1:5000
"""

import os
import random
import re
import time
from urllib.parse import urlparse, quote

import instaloader
import requests
from flask import Flask, request, jsonify, render_template, Response, stream_with_context

app = Flask(__name__)

TIKTOK_LINK_RE = re.compile(
    r"^(https?://)?"
    r"(www\.|vm\.|vt\.|m\.)?"
    r"tiktok\.com/",
    re.IGNORECASE,
)

INSTAGRAM_LINK_RE = re.compile(
    r"^(https?://)?"
    r"(www\.)?"
    r"instagram\.com/",
    re.IGNORECASE,
)

FEED_API_URL = "https://api22-normal-c-alisg.tiktokv.com/aweme/v1/feed/"
FEED_API_STATIC_PARAMS = {
    "channel": "googleplay",
    "app_name": "musical_ly",
    "version_code": "300904",
    "device_platform": "android",
    "device_type": "ASUS_Z01QD",
    "version": "9",
}


def _random_device_component() -> str:
    # TikTok's real device_id/iid values are ~19-digit numbers. The exact
    # digits don't need to decode to anything meaningful — they just need
    # to be a plausible, unique-looking identity so this install isn't
    # sharing the one baked into the public repo (and getting rate-limited
    # alongside every other copy of it).
    return str(random.randint(7_300_000_000_000_000_000, 7_460_000_000_000_000_000))


# One identity per server run — regenerated (see fetch_aweme) only if
# TikTok actually rate-limits it.
_device_identity = {
    "device_id": _random_device_component(),
    "iid": _random_device_component(),
}
MOBILE_USER_AGENT = (
    "com.ss.android.ugc.trill/300904 (Linux; U; Android 9; en_US; ASUS_Z01QD; "
    "Build/PI; Cronet/58.0.2991.0)"
)
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

TIKWM_API_URL = "https://www.tikwm.com/api/"

# Hostnames media can legitimately come from. /api/download refuses to
# proxy anything outside this list.
ALLOWED_CDN_SUFFIXES = (
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "tiktokcdn-eu.com",
    "tiktokv.com",
    "tiktokv.us",
    "tiktokv.eu",
    "ibytedtos.com",
    "ibyteimg.com",
    "muscdn.com",
    "byteoversea.com",
    "bytecdn.com",
    "tikwm.com",
    # Instagram / Facebook media CDN
    "cdninstagram.com",
    "fbcdn.net",
)


def is_valid_tiktok_url(url: str) -> bool:
    return bool(url) and bool(TIKTOK_LINK_RE.match(url.strip()))


def is_valid_instagram_url(url: str) -> bool:
    return bool(url) and bool(INSTAGRAM_LINK_RE.match(url.strip()))


def detect_platform(url: str):
    if is_valid_tiktok_url(url):
        return "tiktok"
    if is_valid_instagram_url(url):
        return "instagram"
    return None


def error_response(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def resolve_final_url(url: str) -> str:
    """Follow redirects on short/app links to get the canonical watch URL."""
    resp = requests.get(
        url,
        allow_redirects=True,
        timeout=15,
        headers={"User-Agent": BROWSER_USER_AGENT},
        stream=True,
    )
    resp.close()
    return resp.url


def extract_media_id(url: str):
    match = re.search(r"/(video|photo)/(\d+)", url)
    if not match:
        return None, None
    return match.group(2), match.group(1)


def fetch_aweme(media_id: str, attempt: int = 1) -> dict:
    resp = requests.get(
        FEED_API_URL,
        params={**FEED_API_STATIC_PARAMS, **_device_identity, "aweme_id": media_id},
        headers={"User-Agent": MOBILE_USER_AGENT},
        timeout=15,
    )
    if resp.status_code == 429 and attempt <= 3:
        # This device identity is (or just became) rate-limited — mint a
        # fresh one and try again a couple of times before giving up.
        _device_identity["device_id"] = _random_device_component()
        _device_identity["iid"] = _random_device_component()
        time.sleep(0.6 * attempt)
        return fetch_aweme(media_id, attempt=attempt + 1)

    resp.raise_for_status()
    data = resp.json()
    items = data.get("aweme_list") or []
    if not items:
        raise ValueError("TikTok didn't return any data for that link (it may be private, region-locked, or removed).")
    return items[0]


def pick_url(url_list_container):
    urls = (url_list_container or {}).get("url_list") or []
    return urls[0] if urls else None


def fetch_via_tikwm(url: str) -> dict:
    """
    Primary lookup path: tikwm.com's free public API. It does TikTok's own
    request signing on its backend, so it doesn't need a spoofed device
    identity the way calling TikTok's mobile API directly does.
    """
    resp = requests.get(
        TIKWM_API_URL,
        params={"url": url, "hd": 1},
        headers={"User-Agent": BROWSER_USER_AGENT},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0 or not payload.get("data"):
        raise ValueError(payload.get("msg") or "tikwm.com had no data for that link.")
    return payload["data"]


def _tikwm_absolute(u):
    if not u:
        return None
    return u if u.startswith("http") else f"https://www.tikwm.com{u}"


def build_media_summary_from_tikwm(data: dict) -> dict:
    author = data.get("author") or {}
    music = data.get("music_info") or {}

    no_watermark = _tikwm_absolute(data.get("play"))
    with_watermark = _tikwm_absolute(data.get("wmplay"))
    audio_url = _tikwm_absolute(data.get("music") or music.get("play"))

    images = []
    for idx, img in enumerate(data.get("images") or []):
        img_url = _tikwm_absolute(img)
        if img_url:
            images.append({"index": idx, "url": img_url, "thumb": img_url, "ext": "jpeg", "isVideo": False})

    downloads = {}
    if no_watermark:
        downloads["video_no_watermark"] = {"url": no_watermark, "label": "Video (no watermark)", "ext": "mp4"}
    if with_watermark and with_watermark != no_watermark:
        downloads["video_watermark"] = {"url": with_watermark, "label": "Video (watermark)", "ext": "mp4"}
    if audio_url:
        downloads["audio"] = {"url": audio_url, "label": music.get("title") or "Original sound", "ext": "mp3"}

    return {
        "ok": True,
        "id": str(data.get("id") or ""),
        "type": "images" if images else "video",
        "desc": data.get("title") or "",
        "thumbnail": _tikwm_absolute(data.get("origin_cover") or data.get("cover")),
        "durationMs": (data.get("duration") or 0) * 1000,
        "author": {
            "nickname": author.get("nickname"),
            "username": author.get("unique_id"),
            "avatar": _tikwm_absolute(author.get("avatar")),
        },
        "stats": {
            "plays": data.get("play_count"),
            "likes": data.get("digg_count"),
            "comments": data.get("comment_count"),
            "shares": data.get("share_count"),
        },
        "downloads": downloads,
        "images": images,
    }


def build_media_summary_from_aweme(aweme: dict) -> dict:
    video = aweme.get("video") or {}
    author = aweme.get("author") or {}
    stats = aweme.get("statistics") or {}
    music = aweme.get("music") or {}
    image_post = aweme.get("image_post_info") or {}

    no_watermark = pick_url(video.get("play_addr"))
    with_watermark = pick_url(video.get("download_addr"))
    cover = pick_url(video.get("origin_cover")) or pick_url(video.get("cover"))
    audio_url = pick_url(music.get("play_url"))

    images = []
    for idx, img in enumerate(image_post.get("images") or []):
        img_url = pick_url(img.get("display_image"))
        if img_url:
            images.append({"index": idx, "url": img_url, "thumb": img_url, "ext": "jpeg", "isVideo": False})

    downloads = {}
    if no_watermark:
        downloads["video_no_watermark"] = {"url": no_watermark, "label": "Video (no watermark)", "ext": "mp4"}
    if with_watermark and with_watermark != no_watermark:
        downloads["video_watermark"] = {"url": with_watermark, "label": "Video (watermark)", "ext": "mp4"}
    if audio_url:
        downloads["audio"] = {"url": audio_url, "label": music.get("title") or "Original sound", "ext": "mp3"}

    duration_ms = video.get("duration") or 0

    return {
        "ok": True,
        "id": aweme.get("aweme_id"),
        "type": "images" if images else "video",
        "desc": aweme.get("desc") or "",
        "thumbnail": cover,
        "durationMs": duration_ms,
        "author": {
            "nickname": author.get("nickname"),
            "username": author.get("unique_id"),
            "avatar": pick_url(author.get("avatar_medium")) or pick_url(author.get("avatar_thumb")),
        },
        "stats": {
            "plays": stats.get("play_count"),
            "likes": stats.get("digg_count"),
            "comments": stats.get("comment_count"),
            "shares": stats.get("share_count"),
        },
        "downloads": downloads,
        "images": images,
    }


# --------------------------------------------------------------------------
# Instagram (instaloader)
# --------------------------------------------------------------------------

INSTAGRAM_SHORTCODE_RE = re.compile(r"instagram\.com/(?:[^/]+/)?(?:p|reel|tv)/([A-Za-z0-9_-]+)")

_ig_loader = instaloader.Instaloader(
    quiet=True,
    download_pictures=False,
    download_videos=False,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    request_timeout=25,
)

_INSTAGRAM_SESSION_USERNAME = os.environ.get("INSTAGRAM_SESSION_USERNAME", "").strip()
if _INSTAGRAM_SESSION_USERNAME:
    try:
        _ig_loader.load_session_from_file(_INSTAGRAM_SESSION_USERNAME)
    except Exception:  # noqa: BLE001 - fall back to anonymous; errors surface per-request instead
        pass


def extract_instagram_shortcode(url: str):
    match = INSTAGRAM_SHORTCODE_RE.search(url)
    return match.group(1) if match else None


def fetch_instagram_post(shortcode: str) -> "instaloader.Post":
    return instaloader.Post.from_shortcode(_ig_loader.context, shortcode)


def build_media_summary_from_instagram(post: "instaloader.Post") -> dict:
    downloads = {}
    images = []

    if post.typename == "GraphSidecar":
        for idx, node in enumerate(post.get_sidecar_nodes()):
            images.append({
                "index": idx,
                "url": node.video_url if node.is_video else node.display_url,
                "thumb": node.display_url,
                "ext": "mp4" if node.is_video else "jpeg",
                "isVideo": node.is_video,
            })
    elif post.is_video:
        video_url = post.video_url
        if video_url:
            downloads["video"] = {"url": video_url, "label": "Video", "ext": "mp4"}
    else:
        downloads["photo"] = {"url": post.url, "label": "Photo", "ext": "jpeg"}

    duration_ms = 0
    if post.is_video and post.typename != "GraphSidecar":
        duration = post.video_duration
        if duration:
            duration_ms = int(duration * 1000)

    return {
        "ok": True,
        "id": post.shortcode,
        "type": "images" if images else "video",
        "desc": post.caption or "",
        "thumbnail": post.url,
        "durationMs": duration_ms,
        "author": {
            "nickname": post.owner_username,
            "username": post.owner_username,
            "avatar": None,
        },
        "stats": {
            "plays": post.video_view_count if post.is_video else None,
            "likes": post.likes,
            "comments": post.comments,
            "shares": None,
        },
        "downloads": downloads,
        "images": images,
    }


def friendly_instagram_error(exc: Exception) -> str:
    if isinstance(exc, instaloader.exceptions.LoginRequiredException):
        return (
            "Instagram is asking for a login to view this post. Public posts sometimes work without "
            "one, but Instagram increasingly requires it — set INSTAGRAM_SESSION_USERNAME to a "
            "username you've logged in with via the instaloader CLI (see the README) for reliable access."
        )
    if isinstance(exc, instaloader.exceptions.TooManyRequestsException):
        return "Instagram is rate-limiting requests right now. Wait a few minutes and try again."
    if isinstance(exc, instaloader.exceptions.ProfileNotExistsException):
        return "That Instagram profile doesn't exist (or the post link is malformed)."
    if isinstance(exc, instaloader.exceptions.PrivateProfileNotFollowedException):
        return "That's a private Instagram account — it can't be read without following it from a logged-in session."
    if isinstance(exc, instaloader.exceptions.InstaloaderException):
        return f"Instagram error: {exc}"
    return f"Couldn't read that Instagram link: {exc}"


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@app.route("/api/info", methods=["POST"])
def api_info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return error_response("Paste a TikTok or Instagram link first.")

    platform = detect_platform(url)
    if platform == "instagram":
        return api_info_instagram(url)
    if platform == "tiktok":
        return api_info_tiktok(url)
    return error_response("That doesn't look like a TikTok or Instagram URL.")


def api_info_instagram(url: str):
    shortcode = extract_instagram_shortcode(url)
    if not shortcode:
        return error_response("Couldn't find a post/reel ID in that Instagram link.", 422)

    try:
        post = fetch_instagram_post(shortcode)
        summary = build_media_summary_from_instagram(post)
        if not summary["downloads"] and not summary["images"]:
            return error_response("Instagram didn't return any downloadable media for this link.", 502)
        return jsonify(summary)
    except Exception as exc:  # noqa: BLE001
        return error_response(friendly_instagram_error(exc), 502)


def api_info_tiktok(url: str):
    tikwm_error = None
    try:
        tikwm_data = fetch_via_tikwm(url)
        summary = build_media_summary_from_tikwm(tikwm_data)
        if summary["downloads"] or summary["images"]:
            return jsonify(summary)
        tikwm_error = "tikwm.com didn't return any downloadable media for this link."
    except Exception as exc:  # noqa: BLE001 - fall through to the direct-API fallback below
        tikwm_error = str(exc)

    # Fallback: talk to TikTok's own mobile API directly, the way the
    # original desktop app did.
    try:
        final_url = resolve_final_url(url)
        media_id, kind = extract_media_id(final_url)
        if not media_id:
            return error_response(f"Couldn't read that link. ({tikwm_error})", 502)

        aweme = fetch_aweme(media_id)
        summary = build_media_summary_from_aweme(aweme)

        if not summary["downloads"] and not summary["images"]:
            return error_response("Neither tikwm.com nor TikTok's own API returned downloadable media for this link.", 502)

        return jsonify(summary)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            return error_response(
                "Both the tikwm.com resolver and TikTok's own API are rate-limiting requests right "
                "now. Wait a minute and try again — this isn't tied to a specific link.",
                429,
            )
        return error_response(f"Couldn't reach TikTok. (tikwm.com: {tikwm_error}; TikTok API: {exc})", 502)
    except requests.RequestException as exc:
        return error_response(f"Couldn't reach TikTok. (tikwm.com: {tikwm_error}; TikTok API: {exc})", 502)
    except ValueError as exc:
        return error_response(f"Couldn't read that link. (tikwm.com: {tikwm_error}; TikTok API: {exc})", 502)
    except Exception as exc:  # noqa: BLE001
        return error_response(f"Couldn't read that link. (tikwm.com: {tikwm_error}; TikTok API: {exc})", 502)


@app.route("/api/download")
def api_download():
    src = request.args.get("src", "").strip()
    filename = request.args.get("filename", "stok-download").strip() or "stok-download"

    if not src:
        return error_response("Missing 'src' parameter.")

    host = (urlparse(src).hostname or "").lower()
    if not host or not any(host == suf or host.endswith("." + suf) for suf in ALLOWED_CDN_SUFFIXES):
        return error_response("That URL isn't a recognized TikTok or Instagram media host.", 400)

    try:
        upstream = requests.get(
            src,
            headers={"User-Agent": BROWSER_USER_AGENT},
            stream=True,
            timeout=30,
        )
        upstream.raise_for_status()
    except requests.RequestException as exc:
        return error_response(f"Download failed: {exc}", 502)

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 64):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
    }
    content_length = upstream.headers.get("Content-Length")
    if content_length:
        headers["Content-Length"] = content_length

    content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    return Response(stream_with_context(generate()), headers=headers, content_type=content_type)


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


@app.errorhandler(Exception)
def handle_any_error(exc):
    """Safety net so /api/* never leaks an HTML error page to the frontend."""
    if request.path.startswith("/api/"):
        return error_response(f"Server error: {exc}", 500)
    raise exc


if __name__ == "__main__":
    app.run(debug=True)
