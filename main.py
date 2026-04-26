from ultralytics import YOLO
import cv2

model = YOLO("yolo11n.pt")  # or yolo26n.pt — lightweight models work fine for real-time

cap = cv2.VideoCapture(0)  # or your capture card index

while True:
    ret, frame = cap.read()
    results = model.track(frame, persist=True, tracker="bytetrack.yaml", stream=False)
    
    for r in results:
        for box in r.boxes:
            track_id = int(box.id) if box.id is not None else -1
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cx, cy = (x1 + x2) / 2, y1  # top-center of bounding box
            # → broadcast {track_id, cx, cy} via WebSocket (see step 2)