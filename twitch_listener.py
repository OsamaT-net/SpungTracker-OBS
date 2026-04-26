"""
twitch_listener.py — Connects to Twitch EventSub and forwards
subscriber events to the overlay server via POST /subscribe.
git
Credentials are loaded from config.json (written by app.py).
Compatible with twitchAPI >= 4.0.
"""

import asyncio
import json
import os
import requests
from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.type import AuthScope
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.object.eventsub import (
    ChannelSubscribeEvent,
    ChannelSubscriptionGiftEvent,
    ChannelSubscriptionMessageEvent,
)

OVERLAY_URL = "http://localhost:8765/subscribe"
CONFIG_FILE = "config.json"

SCOPES = [AuthScope.CHANNEL_READ_SUBSCRIPTIONS]


def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"'{CONFIG_FILE}' not found. Fill in your Twitch credentials in the app "
            "or create config.json manually."
        )
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    missing = [k for k in ("client_id", "client_secret", "broadcaster_id") if not cfg.get(k)]
    if missing:
        raise ValueError(f"Missing fields in config.json: {', '.join(missing)}")
    return cfg["client_id"], cfg["client_secret"], cfg["broadcaster_id"]


def post_alert(username: str, tier: str = "1000", is_gift: bool = False):
    try:
        requests.post(OVERLAY_URL, json={
            "username": username,
            "tier":     tier,
            "is_gift":  is_gift,
        }, timeout=2)
        print(f"[twitch] Alert sent: {username} (tier {tier}, gift={is_gift})")
    except Exception as e:
        print(f"[twitch] Failed to send alert: {e}")


async def on_subscribe(event: ChannelSubscribeEvent):
    post_alert(event.event.user_name, event.event.tier, event.event.is_gift)


async def on_gift_sub(event: ChannelSubscriptionGiftEvent):
    gifter = event.event.user_name or "Anonymous"
    post_alert(f"{gifter} (x{event.event.total})", event.event.tier, True)


async def on_resub(event: ChannelSubscriptionMessageEvent):
    post_alert(event.event.user_name, event.event.tier, False)


async def main():
    client_id, client_secret, broadcaster_id = load_config()

    print("[twitch] Connecting to Twitch...")
    twitch = await Twitch(client_id, client_secret)

    auth = UserAuthenticator(twitch, SCOPES)
    token, refresh_token = await auth.authenticate()
    await twitch.set_user_authentication(token, SCOPES, refresh_token)

    # eventsub = EventSubWebsocket(twitch)
    eventsub = EventSubWebsocket(twitch, connection_url="ws://127.0.0.1:8080/ws")
    eventsub.start()

    await eventsub.listen_channel_subscribe(broadcaster_id, on_subscribe)
    await eventsub.listen_channel_subscription_gift(broadcaster_id, on_gift_sub)
    await eventsub.listen_channel_subscription_message(broadcaster_id, on_resub)

    print("[twitch] Listening for subscriber events. Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await eventsub.stop()
        await twitch.close()
        print("[twitch] Stopped.")


if __name__ == "__main__":
    asyncio.run(main())