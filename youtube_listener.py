"""
youtube_listener.py — Polls a YouTube live stream chat and forwards
member events to the overlay server via POST /subscribe.

Channel URL is loaded from config.json (written by app.py).
The live stream video ID is resolved automatically at startup.
Uses pytchat for polling (no OAuth required for public streams).
"""

import concurrent.futures
import json
import os
import re
import sys
import threading
import requests
import pytchat

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONFIG_FILE = "config.json"


def resolve_live_video_id(channel_url: str) -> str:
    """Resolves a channel URL to the video ID of its active live stream."""
    url = channel_url.strip().rstrip("/")
    if not url.startswith("http"):
        url = f"https://www.youtube.com/{url.lstrip('/')}"
    if "/live" not in url:
        url += "/live"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=10)

    # Case 1: YouTube redirected to the watch page
    vid = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", resp.url)
    if vid:
        return vid.group(1)

    # Case 2: Canonical link tag — points to the actual video on the /live page
    vid = re.search(
        r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})"',
        resp.text,
    )
    if vid:
        return vid.group(1)

    raise ValueError(
        f"No active live stream found for: {channel_url}\n"
        "Make sure the stream is live and the channel URL is correct."
    )


def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"'{CONFIG_FILE}' not found. Set your YouTube channel URL in the app."
        )
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    channel_url = cfg.get("youtube_channel_url", "").strip()
    if not channel_url:
        raise ValueError("Missing 'youtube_channel_url' in config.json — set it in the YouTube tab.")
    overlay_url = cfg.get("overlay_url") or "http://localhost:8765/subscribe"
    return channel_url, overlay_url


def post_alert(overlay_url, username, is_gift=False):
    try:
        r = requests.post(
            overlay_url,
            json={"username": username, "tier": "1000", "is_gift": is_gift, "source": "youtube"},
            timeout=2,
        )
        print(f"[youtube] Alert sent: {username} (gift={is_gift}) -> {r.status_code}")
    except Exception as e:
        print(f"[youtube] Failed to send alert: {e}")


def run_chat_loop(video_id, overlay_url, stop_event):
    print("[youtube] Connecting to live chat...")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(pytchat.create, video_id, interruptable=False)
            chat = future.result(timeout=15)
    except concurrent.futures.TimeoutError:
        print("[youtube] ERROR: Timed out connecting to live chat (15s).")
        print("[youtube] YouTube may be slow or blocking the request.")
        return
    except Exception as e:
        print(f"[youtube] ERROR creating live chat: {e}")
        return

    if not chat.is_alive():
        print(f"[youtube] ERROR: Could not connect to live chat for video {video_id}.")
        print("[youtube] The stream may not be live or live chat may be disabled.")
        return

    print("[youtube] Ready — listening for member events. Press Ctrl+C to stop.")
    while chat.is_alive() and not stop_event.is_set():
        try:
            for c in chat.get().sync_items():
                if c.type == "newSponsor":
                    print(f"[youtube] New member: {c.author.name}")
                    post_alert(overlay_url, c.author.name)
                elif c.type == "memberMilestone":
                    print(f"[youtube] Member milestone: {c.author.name}")
                    post_alert(overlay_url, c.author.name)
        except Exception as e:
            print(f"[youtube] Chat error: {e}")
        stop_event.wait(3)

    print("[youtube] Stopped.")


def main():
    channel_url, overlay_url = load_config()
    print(f"[youtube] Resolving live stream for: {channel_url}")
    video_id = resolve_live_video_id(channel_url)
    print(f"[youtube] Live stream found — video ID: {video_id}")

    stop_event = threading.Event()
    try:
        run_chat_loop(video_id, overlay_url, stop_event)
    except (KeyboardInterrupt, SystemExit):
        stop_event.set()


if __name__ == "__main__":
    main()
