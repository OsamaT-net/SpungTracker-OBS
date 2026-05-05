"""
tracker.py — Spung tracker using YOLO-World (zero-shot detection).

Two modes:
  --mode oneshot      Find Spung once, push position, exit. Used by on-demand mode.
  --mode continuous   Run forever, push bbox every frame. Used for live preview.

In oneshot mode: if Spung is not found within --timeout seconds, exits silently.
The server will fall back to last known position automatically.
"""

import argparse
import sys
import cv2
import requests
import time
from ultralytics import YOLOWorld

OVERLAY_URL = "http://localhost:8765/bbox"

# Force UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

parser = argparse.ArgumentParser()
parser.add_argument("--camera",     type=int,   default=2)
parser.add_argument("--target",     type=str,   default="green frog plush toy")
parser.add_argument("--confidence", type=float, default=0.25)
parser.add_argument("--infer_fps",  type=int,   default=10)
parser.add_argument("--infer_size", type=int,   default=320)
parser.add_argument("--device",     type=str,   default="cpu",
                    help="cpu or cuda (NVIDIA only)")
parser.add_argument("--mode",       type=str,   default="oneshot",
                    choices=["continuous", "oneshot"])
parser.add_argument("--timeout",    type=float, default=3.0,
                    help="Oneshot: max seconds to search before giving up")
args = parser.parse_args()

print(f"[tracker] Loading YOLO-World (device={args.device}, mode={args.mode})...")
model = YOLOWorld("yolov8s-world.pt")
model.set_classes([args.target])

cap = cv2.VideoCapture(args.camera)
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)   # always grab the newest frame


def push_bbox(cx: float, cy: float, visible: bool):
    try:
        requests.post(
            OVERLAY_URL,
            json={"cx": cx, "cy": cy, "visible": visible},
            timeout=0.2,
        )
    except Exception:
        pass


def pick_best_box(results, fw: int, fh: int):
    """Return (cx_norm, cy_norm, raw_box) for highest-confidence detection, or None."""
    best_conf, best_box = 0.0, None
    for r in results:
        for box in r.boxes:
            c = float(box.conf[0])
            if c > best_conf:
                best_conf = c
                best_box  = box.xyxy[0].tolist()
    if best_box is None:
        return None
    x1, y1, x2, y2 = best_box
    cx = ((x1 + x2) / 2) / fw
    cy = y1 / fh  # top edge of box = speech bubble anchor
    return cx, cy, best_box


print(f"[tracker] Looking for: '{args.target}'")

infer_interval = 1.0 / args.infer_fps
last_infer     = 0.0
detection      = None
start_time     = time.time()

while True:
    ret, frame = cap.read()
    if not ret:
        time.sleep(0.05)
        continue

    fh, fw = frame.shape[:2]
    now    = time.time()

    if now - last_infer >= infer_interval:
        last_infer = now
        predict_kwargs = dict(conf=args.confidence, imgsz=args.infer_size, verbose=False)
        if args.device == "cuda":
            predict_kwargs["device"] = "cuda"

        results   = model.predict(frame, **predict_kwargs)
        detection = pick_best_box(results, fw, fh)

        if detection:
            cx, cy, _ = detection
            push_bbox(cx, cy, True)
            print(f"[tracker] Spung found at ({cx:.2f}, {cy:.2f})")
            if args.mode == "oneshot":
                print("[tracker] Oneshot complete — exiting.")
                break
        else:
            push_bbox(0.5, 0.3, False)
            if args.mode == "oneshot" and (now - start_time) > args.timeout:
                # Don't push anything — server will use last_known_bbox as fallback
                print(f"[tracker] Oneshot: Spung not found within {args.timeout}s — server will use last known position.")
                break

    # Continuous mode only: show live preview window
    if args.mode == "continuous":
        preview = frame.copy()
        if detection:
            x1, y1, x2, y2 = [int(v) for v in detection[2]]
            cv2.rectangle(preview, (x1, y1), (x2, y2), (0, 220, 80), 2)
            cv2.putText(preview, args.target, (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 80), 2)
        else:
            cv2.putText(preview, "No Spung detected", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 220), 2)
        cv2.imshow("Spung Tracker — press Q to quit", preview)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

cap.release()
cv2.destroyAllWindows()
print("[tracker] Stopped.")
