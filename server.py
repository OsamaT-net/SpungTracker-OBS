"""
server.py — Overlay hub server
- Receives bbox updates from tracker.py via POST /bbox
- Receives subscriber alerts from Twitch via POST /subscribe
- Receives message template updates from app.py via POST /message-config
- Serves the OBS Browser Source HTML at GET /
- Pushes real-time data to OBS over WebSocket at ws://localhost:8765/ws
"""

import asyncio
import sys
import json
import time
from pathlib import Path
from typing import Set
from contextlib import asynccontextmanager

# Force UTF-8 so emojis don't crash on Windows cp1252 terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import uvicorn


# ── State ─────────────────────────────────────────────────────────────────────
connected_clients: Set[WebSocket] = set()
latest_bbox     = {"cx": 0.5, "cy": 0.3, "visible": False}
last_known_bbox = {"cx": 0.5, "cy": 0.3}  # last position Spung was actually seen
alert_queue: list = []

# ── Message templates (overridable via POST /message-config) ──────────────────
message_config = {
    "sub_template":  "{emoji} {username} subscribed! {tier}",
    "gift_template": "{emoji} {username} gifted subs! {tier}",
    "resub_template":"{emoji} {username} resubbed! {tier}",
    "sub_emoji":     "🎉",
    "gift_emoji":    "🎁",
    "member_template": "{emoji} {username} became a member!",
    "member_emoji":    "🌟",
    "show_tier":     True,
    "duration_ms":      6000,
    "tracking_follows": False,
    "font_size":        28,
    "bubble_padding":   12,
    "sound_file":       "",
}


def build_message(template: str, emoji: str, username: str, tier_label: str) -> str:
    return template.format(
        emoji=emoji,
        username=username,
        tier=tier_label,
    ).strip()


# ── Models ────────────────────────────────────────────────────────────────────
class BBoxUpdate(BaseModel):
    cx: float
    cy: float
    visible: bool

class SubscribeAlert(BaseModel):
    username: str
    tier: str = "1000"
    is_gift: bool = False
    source: str = "twitch"

class MessageConfig(BaseModel):
    sub_template:  str  = "{emoji} {username} subscribed! {tier}"
    gift_template: str  = "{emoji} {username} gifted subs! {tier}"
    resub_template:str  = "{emoji} {username} resubbed! {tier}"
    sub_emoji:     str  = "🎉"
    gift_emoji:    str  = "🎁"
    member_template: str = "{emoji} {username} became a member!"
    member_emoji:    str = "🌟"
    show_tier:        bool = True
    duration_ms:      int  = 6000
    tracking_follows: bool = False
    font_size:        int  = 28
    bubble_padding:   int  = 12
    sound_file:       str  = ""


# ── Broadcast helper ──────────────────────────────────────────────────────────
async def broadcast(payload: dict):
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)


# ── Background push loop ──────────────────────────────────────────────────────
async def push_loop():
    """Sends bbox + pending alerts to all OBS Browser Source clients at 20 fps."""
    while True:
        await asyncio.sleep(0.05)
        if not connected_clients:
            continue
        payload = {
            "bbox":   latest_bbox.copy(),
            "alerts": alert_queue.copy(),
        }
        alert_queue.clear()
        await broadcast(payload)


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(push_loop())
    yield


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/bbox")
async def receive_bbox(update: BBoxUpdate):
    """Called by tracker.py on every frame (continuous) or on detection (oneshot)."""
    global latest_bbox, last_known_bbox
    latest_bbox = update.model_dump()
    if update.visible:
        last_known_bbox = {"cx": update.cx, "cy": update.cy}
    return {"ok": True}


@app.get("/bbox-status")
async def bbox_status():
    """Returns whether the Spung is currently visible — polled by app.py in oneshot mode."""
    return {"visible": latest_bbox.get("visible", False),
            "cx": latest_bbox.get("cx", 0.5),
            "cy": latest_bbox.get("cy", 0.3)}


@app.post("/message-config")
async def update_message_config(cfg: MessageConfig):
    """Called by app.py when the user updates message settings."""
    global message_config
    message_config.update(cfg.model_dump())
    print("[server] Message config updated")
    return {"ok": True}



@app.get("/message-config")
async def get_message_config():
    """Returns current message config so app.py can load it on startup."""
    return message_config


@app.post("/subscribe")
async def receive_subscribe(alert: SubscribeAlert):
    """Called by twitch_listener.py when a subscriber event fires."""
    tier_label = {"1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3"}.get(alert.tier, "")
    if not message_config["show_tier"]:
        tier_label = ""

    if alert.source == "youtube":
        msg = build_message(
            message_config["member_template"],
            message_config["member_emoji"],
            alert.username,
            "",
        )
    elif alert.is_gift:
        msg = build_message(
            message_config["gift_template"],
            message_config["gift_emoji"],
            alert.username,
            tier_label,
        )
    else:
        msg = build_message(
            message_config["sub_template"],
            message_config["sub_emoji"],
            alert.username,
            tier_label,
        )

    # Use current position if visible, otherwise fall back to last known position.
    # Only defaults to center if Spung has never been seen this session.
    if latest_bbox.get("visible"):
        snapped_bbox = latest_bbox.copy()
    else:
        snapped_bbox = {**last_known_bbox, "visible": False}
        print(
            f"[server] Spung not visible — using last known position "
            f"({last_known_bbox['cx']:.2f}, {last_known_bbox['cy']:.2f})"
        )

    alert_queue.append({
        "message":      msg,
        "username":     alert.username,
        "duration":     message_config["duration_ms"],
        "ts":           time.time(),
        "snapped_bbox": snapped_bbox,
        "follow":       message_config.get("tracking_follows", False),
        "font_size":    message_config.get("font_size", 28),
        "padding":      message_config.get("bubble_padding", 12),
        "play_sound":   bool(message_config.get("sound_file", "")),
    })
    print(f"[server] Alert queued: {msg}")
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """OBS Browser Source connects here."""
    await ws.accept()
    connected_clients.add(ws)
    print(f"[server] OBS client connected. Total: {len(connected_clients)}")
    try:
        while True:
            await ws.receive_text()  # keep alive; we push, not pull
    except WebSocketDisconnect:
        connected_clients.discard(ws)
        print(f"[server] OBS client disconnected. Total: {len(connected_clients)}")


@app.get("/sound")
async def serve_sound():
    """Serves the uploaded sound file to the overlay."""
    sound_path_str = message_config.get("sound_file", "")
    if not sound_path_str:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No sound file configured")
    sound_path = Path(sound_path_str)
    if not sound_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Sound file not found")
    return FileResponse(str(sound_path), media_type="audio/mpeg")


@app.get("/")
async def serve_overlay():
    """Serves the HTML overlay to OBS Browser Source."""
    html_path = Path(__file__).parent / "static" / "overlay.html"
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma":        "no-cache",
            "Expires":       "0",
        }
    )


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=False)