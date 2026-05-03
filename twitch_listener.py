"""
twitch_listener.py — Connects to Twitch EventSub and forwards
subscriber events to the overlay server via POST /subscribe.

Credentials are loaded from config.json (written by app.py).
Compatible with twitchAPI 4.5.0.

Requires Twitch Affiliate or Partner status to receive real subscriber events.
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
from twitchAPI.helper import first

CONFIG_FILE = "config.json"
SCOPES = [AuthScope.CHANNEL_READ_SUBSCRIPTIONS]


def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"'{CONFIG_FILE}' not found. Fill in your Twitch credentials in the app."
        )
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    missing = [k for k in ("client_id", "client_secret", "broadcaster_id") if not cfg.get(k)]
    if missing:
        raise ValueError(f"Missing fields in config.json: {', '.join(missing)}")
    overlay_url = cfg.get("overlay_url") or "http://localhost:8765/subscribe"
    return cfg["client_id"], cfg["client_secret"], cfg["broadcaster_id"], overlay_url


async def post_alert(overlay_url, username, tier="1000", is_gift=False):
    try:
        r = await asyncio.to_thread(
            requests.post, overlay_url,
            json={"username": username, "tier": tier, "is_gift": is_gift},
            timeout=2,
        )
        print(f"[twitch] Alert sent: {username} (tier={tier}, gift={is_gift}) → {r.status_code}")
    except Exception as e:
        print(f"[twitch] Failed to send alert: {e}")


async def main():
    client_id, client_secret, broadcaster_id, overlay_url = load_config()

    print("[twitch] Connecting to Twitch...")
    twitch = await Twitch(client_id, client_secret)

    auth = UserAuthenticator(twitch, SCOPES)
    token, refresh_token = await auth.authenticate()
    await twitch.set_user_authentication(token, SCOPES, refresh_token)

    # Get the authenticated user to confirm identity
    user = await first(twitch.get_users())
    print(f"[twitch] Authenticated as: {user.login} (id={user.id})")
    if user.id != broadcaster_id:
        raise ValueError(
            f"Authenticated user id ({user.id}) does not match broadcaster_id ({broadcaster_id}) in config.json"
        )

    async def on_subscribe(event: ChannelSubscribeEvent):
        print(f"[twitch] New sub: {event.event.user_name} (gift={event.event.is_gift})")
        if event.event.is_gift:
            return  # handled by on_gift_sub to avoid duplicate alerts
        await post_alert(overlay_url, event.event.user_name, event.event.tier, False)

    async def on_gift_sub(event: ChannelSubscriptionGiftEvent):
        gifter = event.event.user_name or "Anonymous"
        print(f"[twitch] Gift sub: {gifter} x{event.event.total}")
        await post_alert(overlay_url, f"{gifter} (x{event.event.total})", event.event.tier, True)

    async def on_resub(event: ChannelSubscriptionMessageEvent):
        print(f"[twitch] Resub: {event.event.user_name}")
        await post_alert(overlay_url, event.event.user_name, event.event.tier, False)

    eventsub = EventSubWebsocket(twitch)
    eventsub.start()

    await eventsub.listen_channel_subscribe(broadcaster_id, on_subscribe)
    await eventsub.listen_channel_subscription_gift(broadcaster_id, on_gift_sub)
    await eventsub.listen_channel_subscription_message(broadcaster_id, on_resub)

    print("[twitch] Ready — listening for subscriber events. Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await eventsub.stop()
        await twitch.close()
        print("[twitch] Stopped.")


if __name__ == "__main__":
    asyncio.run(main())