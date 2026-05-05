"""
youtube_listener.py — Polls a YouTube live stream chat and forwards
member events to the overlay server via POST /subscribe.

Channel URL is loaded from config.json (written by app.py).
The live stream video ID is resolved automatically at startup.
Uses pytchat for polling (no OAuth required for public streams).
"""

import asyncio
import json
import os
import re
import requests
import pytchat

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

    # Case 2: Rendered live page — find video ID in page source
    vid = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
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


async def post_alert(overlay_url, username, is_gift=False):
    try:
        r = await asyncio.to_thread(
            requests.post, overlay_url,
            json={"username": username, "tier": "1000", "is_gift": is_gift, "source": "youtube"},
            timeout=2,
        )
        print(f"[youtube] Alert sent: {username} (gift={is_gift}) → {r.status_code}")
    except Exception as e:
        print(f"[youtube] Failed to send alert: {e}")


async def main():
    channel_url, overlay_url = load_config()
    print(f"[youtube] Resolving live stream for: {channel_url}")
    video_id = resolve_live_video_id(channel_url)
    print(f"[youtube] Live stream found — video ID: {video_id}")

    async def handle_chat(chatdata):
        async for c in chatdata.async_items():
            if c.type == "newSponsor":
                print(f"[youtube] New member: {c.author.name}")
                await post_alert(overlay_url, c.author.name, is_gift=False)
            elif c.type == "memberMilestone":
                print(f"[youtube] Member milestone: {c.author.name}")
                await post_alert(overlay_url, c.author.name, is_gift=False)

    livechat = pytchat.LiveChatAsync(video_id, callback=handle_chat)

    print("[youtube] Ready — listening for member events. Press Ctrl+C to stop.")
    try:
        while livechat.is_alive():
            await asyncio.sleep(3)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        livechat.terminate()
        print("[youtube] Stopped.")


if __name__ == "__main__":
    asyncio.run(main())
