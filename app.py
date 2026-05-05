"""
app.py — Spung Tracker Desktop Control Panel
Run with: python app.py
"""

import sys
import json
import os
import time
import threading
import subprocess
import requests
import cv2

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox,
    QGroupBox, QComboBox, QTextEdit, QTabWidget, QFormLayout, QCheckBox,
    QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QImage, QPixmap, QFont

CONFIG_FILE = "config.json"
SERVER_URL  = "http://localhost:8765"


# ── Config helpers ────────────────────────────────────────────────────────────
def load_config():
    defaults = {
        "client_id":      "",
        "client_secret":  "",
        "broadcaster_id": "",
        "camera":         2,
        "target":         "green frog plush toy",
        "confidence":     0.25,
        "infer_fps":      10,   # safe default; config.json overrides
        "infer_size":     320,
        "use_cuda":       False,
        "tracker_mode":   "oneshot",
        "sub_template":   "{emoji} {username} subscribed! {tier}",
        "gift_template":  "{emoji} {username} gifted subs! {tier}",
        "resub_template": "{emoji} {username} resubbed! {tier}",
        "sub_emoji":      "🎉",
        "gift_emoji":     "🎁",
        "member_template": "{emoji} {username} became a member!",
        "member_emoji":    "🌟",
        "show_tier":      True,
        "duration_ms":    6000,
        "tracking_follows": False,
        "font_size":        28,
        "bubble_padding":   12,
        "sound_file":       "",
        "youtube_channel_url": "",
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            defaults.update(json.load(f))
    return defaults


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ── Status dot ────────────────────────────────────────────────────────────────
class StatusDot(QLabel):
    def __init__(self):
        super().__init__("●")
        self.setFont(QFont("Arial", 16))
        self.set_off()

    def set_on(self):  self.setStyleSheet("color: #22c55e;")
    def set_off(self): self.setStyleSheet("color: #444;")


# ── On-demand tracker worker ──────────────────────────────────────────────────
# Loads YOLO once, then sleeps. When trigger() is called it scans frames
# until the Spung is found (or timeout), pushes the bbox, then sleeps again.
# This eliminates the 15-second model load delay on every sub event.
class OnDemandTrackerWorker(QObject):
    log     = pyqtSignal(str)
    stopped = pyqtSignal()
    found   = pyqtSignal()   # emitted when Spung position is confirmed

    def __init__(self, cfg):
        super().__init__()
        self.cfg       = cfg
        self._running  = False
        self._trigger  = threading.Event()
        self._stop_evt = threading.Event()

    def trigger(self):
        """Called when a sub fires — wakes the tracker to find the Spung."""
        self._trigger.set()

    def run(self):
        self._running = True
        try:
            from ultralytics import YOLOWorld
            device = "cuda" if self.cfg["use_cuda"] else "cpu"
            self.log.emit(f"[tracker] Loading YOLO-World model (device={device})...")
            model = YOLOWorld("yolov8s-world.pt")
            model.set_classes([self.cfg["target"]])
            self.log.emit("[tracker] Model loaded — on-demand mode ready, waiting for sub event")

            cap = cv2.VideoCapture(self.cfg["camera"])
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            infer_interval = 1.0 / self.cfg["infer_fps"]
            timeout_s      = 5.0

            while not self._stop_evt.is_set():
                # Sleep until a sub fires
                self._trigger.wait()
                if self._stop_evt.is_set():
                    break
                self._trigger.clear()
                self.log.emit("[tracker] Sub fired — scanning for Spung...")

                # Reset server bbox so polling doesn't match stale state
                try:
                    requests.post(f"{SERVER_URL}/bbox",
                        json={"cx": 0.5, "cy": 0.3, "visible": False}, timeout=0.3)
                except Exception:
                    pass

                # Re-read tracking_follows each time so live changes take effect
                follow_mode   = self.cfg.get("tracking_follows", False)
                duration_s    = self.cfg.get("duration_ms", 6000) / 1000
                # Phase 1: find the Spung (up to timeout_s seconds)
                # Phase 2: if follow mode, keep tracking for the bubble duration
                find_deadline = time.time() + timeout_s
                found_pos     = False
                last_infer    = 0
                alert_sent    = False
                follow_until  = None   # set once Spung is found in follow mode

                while not self._stop_evt.is_set():
                    now = time.time()

                    # Stop condition
                    if alert_sent and not follow_mode:
                        break  # found, not following — done
                    if alert_sent and follow_mode and now > follow_until:
                        break  # follow duration expired
                    if not alert_sent and now > find_deadline:
                        break  # gave up finding Spung

                    ret, frame = cap.read()
                    if not ret:
                        time.sleep(0.05)
                        continue

                    if now - last_infer < infer_interval:
                        time.sleep(0.01)
                        continue
                    last_infer = now

                    fh, fw = frame.shape[:2]
                    kwargs = dict(conf=self.cfg["confidence"],
                                  imgsz=self.cfg["infer_size"], verbose=False)
                    if self.cfg["use_cuda"]:
                        kwargs["device"] = "cuda"

                    results    = model.predict(frame, **kwargs)
                    best_conf, best_box = 0, None
                    for r in results:
                        for box in r.boxes:
                            c = float(box.conf[0])
                            if c > best_conf:
                                best_conf = c
                                best_box  = box.xyxy[0].tolist()

                    if best_box:
                        x1, y1, x2, y2 = best_box
                        cx = ((x1 + x2) / 2) / fw
                        cy = y1 / fh
                        try:
                            requests.post(f"{SERVER_URL}/bbox",
                                json={"cx": cx, "cy": cy, "visible": True}, timeout=0.2)
                        except Exception:
                            pass

                        if not alert_sent:
                            self.log.emit(f"[tracker] Spung found at ({cx:.2f}, {cy:.2f})"
                                          + (" — following" if follow_mode else ""))
                            self.found.emit()
                            alert_sent   = True
                            found_pos    = True
                            follow_until = time.time() + duration_s
                    else:
                        # Spung not visible this frame — mark invisible but keep scanning in follow mode
                        try:
                            requests.post(f"{SERVER_URL}/bbox",
                                json={"cx": 0.5, "cy": 0.3, "visible": False}, timeout=0.1)
                        except Exception:
                            pass

                if not found_pos:
                    self.log.emit("[tracker] Spung not found — using last known position")
                    self.found.emit()  # still fire so the alert goes through

            cap.release()
        except Exception as e:
            self.log.emit(f"[tracker] ERROR: {e}")
        finally:
            self._running = False
            self.stopped.emit()

    def stop(self):
        self._stop_evt.set()
        self._trigger.set()  # unblock the wait()


# ── Continuous tracker worker ─────────────────────────────────────────────────
class ContinuousTrackerWorker(QObject):
    frame_ready = pyqtSignal(object, object)
    log         = pyqtSignal(str)
    stopped     = pyqtSignal()

    def __init__(self, cfg):
        super().__init__()
        self.cfg      = cfg
        self._running = False
        self._latest  = None
        self._lock    = threading.Lock()

    def _capture_loop(self, cap):
        while self._running:
            ret, frame = cap.read()
            if ret:
                with self._lock:
                    self._latest = frame

    def run(self):
        self._running = True
        try:
            from ultralytics import YOLOWorld
            device = "cuda" if self.cfg["use_cuda"] else "cpu"
            self.log.emit(f"[tracker] Loading YOLO-World (device={device})...")
            model = YOLOWorld("yolov8s-world.pt")
            model.set_classes([self.cfg["target"]])

            cap = cv2.VideoCapture(self.cfg["camera"])
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.log.emit(f"[tracker] Continuous mode started")

            threading.Thread(target=self._capture_loop, args=(cap,), daemon=True).start()

            interval  = 1.0 / self.cfg["infer_fps"]
            last_time = 0
            detection = None

            while self._running:
                now = time.time()
                if now - last_time < interval:
                    time.sleep(0.005)
                    continue

                with self._lock:
                    frame = self._latest
                if frame is None:
                    time.sleep(0.01)
                    continue

                last_time = now
                fh, fw = frame.shape[:2]
                kwargs = dict(conf=self.cfg["confidence"],
                              imgsz=self.cfg["infer_size"], verbose=False)
                if self.cfg["use_cuda"]:
                    kwargs["device"] = "cuda"
                results = model.predict(frame, **kwargs)

                best_conf, best_box = 0, None
                for r in results:
                    for box in r.boxes:
                        c = float(box.conf[0])
                        if c > best_conf:
                            best_conf = c
                            best_box  = box.xyxy[0].tolist()

                if best_box:
                    x1, y1, x2, y2 = best_box
                    cx = ((x1 + x2) / 2) / fw
                    cy = y1 / fh
                    detection = (cx, cy, best_box)
                    try:
                        requests.post(f"{SERVER_URL}/bbox",
                            json={"cx": cx, "cy": cy, "visible": True}, timeout=0.1)
                    except Exception:
                        pass
                else:
                    detection = None
                    try:
                        requests.post(f"{SERVER_URL}/bbox",
                            json={"cx": 0.5, "cy": 0.3, "visible": False}, timeout=0.1)
                    except Exception:
                        pass

                self.frame_ready.emit(frame.copy(), detection)

            cap.release()
        except Exception as e:
            self.log.emit(f"[tracker] ERROR: {e}")
        finally:
            self.stopped.emit()

    def stop(self):
        self._running = False


# ── Subprocess worker (server / twitch) ───────────────────────────────────────
class SubprocessWorker(QObject):
    log     = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, script, args=None):
        super().__init__()
        self.script   = script
        self.args     = args or []
        self._proc    = None
        self._running = False

    def run(self):
        self._running = True
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            self._proc = subprocess.Popen(
                [sys.executable, self.script] + self.args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding="utf-8", errors="replace",
                env=env,
            )
            for line in self._proc.stdout:
                if not self._running:
                    break
                self.log.emit(line.rstrip())
            self._proc.wait()
        except Exception as e:
            self.log.emit(f"[{self.script}] ERROR: {e}")
        finally:
            self.stopped.emit()

    def stop(self):
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass


# ── Main window ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🐸 SPUNG Tracker Control Panel")
        self.setMinimumSize(1100, 750)

        self.cfg = load_config()

        self._server_worker  = None
        self._server_thread  = None
        self._tracker_worker = None
        self._tracker_thread = None
        self._twitch_worker  = None
        self._twitch_thread  = None
        self._yt_worker      = None
        self._yt_thread      = None

        self._tracker_active = False

        self._pending_frame     = None
        self._pending_detection = None
        self._frame_lock        = threading.Lock()

        self._build_ui()
        self._apply_theme()

        self._preview_timer = QTimer()
        self._preview_timer.timeout.connect(self._flush_preview)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        tabs = QTabWidget()
        tabs.setFixedWidth(360)

        # ── Tab 1: Tracker ──
        tracker_tab = QWidget()
        tl = QFormLayout(tracker_tab)
        tl.setSpacing(10)
        tl.setContentsMargins(12, 12, 12, 12)

        self.camera_spin = QSpinBox()
        self.camera_spin.setRange(0, 10)
        self.camera_spin.setValue(self.cfg["camera"])
        tl.addRow("Camera index", self.camera_spin)

        self.target_edit = QLineEdit(self.cfg["target"])
        tl.addRow("Target class", self.target_edit)

        self.conf_spin = QDoubleSpinBox()
        self.conf_spin.setRange(0.05, 0.95)
        self.conf_spin.setSingleStep(0.05)
        self.conf_spin.setValue(self.cfg["confidence"])
        tl.addRow("Confidence", self.conf_spin)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 30)
        self.fps_spin.setValue(self.cfg["infer_fps"])
        tl.addRow("Inference FPS", self.fps_spin)

        self.size_combo = QComboBox()
        for s in ["256", "320", "416", "640", "1280"]:
            self.size_combo.addItem(s)
        self.size_combo.setCurrentText(str(self.cfg["infer_size"]))
        tl.addRow("Inference size", self.size_combo)

        self.cuda_check = QCheckBox("Use NVIDIA CUDA GPU")
        self.cuda_check.setChecked(self.cfg["use_cuda"])
        tl.addRow("", self.cuda_check)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("On-demand (saves CPU — model stays loaded)", "oneshot")
        self.mode_combo.addItem("Continuous (always tracking)", "continuous")
        self.mode_combo.setCurrentIndex(
            0 if self.cfg.get("tracker_mode", "oneshot") == "oneshot" else 1)
        tl.addRow("Tracker mode", self.mode_combo)

        save_btn = QPushButton("Save settings")
        save_btn.clicked.connect(self._save_settings)
        tl.addRow("", save_btn)

        tabs.addTab(tracker_tab, "Tracker")

        # ── Tab 2: Twitch ──
        twitch_tab = QWidget()
        tw = QFormLayout(twitch_tab)
        tw.setSpacing(10)
        tw.setContentsMargins(12, 12, 12, 12)

        tw.addRow(QLabel("Get these from dev.twitch.tv/console"))

        self.client_id_edit = QLineEdit(self.cfg.get("client_id", ""))
        self.client_id_edit.setPlaceholderText("Paste your Client ID")
        tw.addRow("Client ID", self.client_id_edit)

        self.client_secret_edit = QLineEdit(self.cfg.get("client_secret", ""))
        self.client_secret_edit.setPlaceholderText("Paste your Client Secret")
        self.client_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        tw.addRow("Client Secret", self.client_secret_edit)

        self.broadcaster_id_edit = QLineEdit(self.cfg.get("broadcaster_id", ""))
        self.broadcaster_id_edit.setPlaceholderText("Numeric broadcaster ID")
        tw.addRow("Broadcaster ID", self.broadcaster_id_edit)

        id_link = QLabel('<a href="https://www.streamweasels.com/tools/convert-twitch-username-to-user-id/" style="color:#a78bfa;">Find your numeric ID here</a>')
        id_link.setOpenExternalLinks(True)
        tw.addRow("", id_link)

        save_twitch_btn = QPushButton("Save Twitch credentials")
        save_twitch_btn.clicked.connect(self._save_settings)
        tw.addRow("", save_twitch_btn)

        tabs.addTab(twitch_tab, "Twitch")

        # ── Tab 3: YouTube ──
        yt_tab = QWidget()
        yl = QFormLayout(yt_tab)
        yl.setSpacing(10)
        yl.setContentsMargins(12, 12, 12, 12)

        yl.addRow(QLabel("Paste your YouTube channel URL.\nThe live stream is detected automatically."))

        self.yt_channel_url_edit = QLineEdit(self.cfg.get("youtube_channel_url", ""))
        self.yt_channel_url_edit.setPlaceholderText("e.g. https://www.youtube.com/@YourChannel")
        yl.addRow("Channel URL", self.yt_channel_url_edit)

        save_yt_btn = QPushButton("Save YouTube settings")
        save_yt_btn.clicked.connect(self._save_settings)
        yl.addRow("", save_yt_btn)

        tabs.addTab(yt_tab, "YouTube")

        # ── Tab 4: Message ──
        msg_tab = QWidget()
        ml = QFormLayout(msg_tab)
        ml.setSpacing(10)
        ml.setContentsMargins(12, 12, 12, 12)

        ml.addRow(QLabel("Placeholders: {emoji}  {username}  {tier}"))

        self.sub_template_edit = QLineEdit(self.cfg.get("sub_template", "{emoji} {username} subscribed! {tier}"))
        ml.addRow("Sub message", self.sub_template_edit)

        self.gift_template_edit = QLineEdit(self.cfg.get("gift_template", "{emoji} {username} gifted subs! {tier}"))
        ml.addRow("Gift message", self.gift_template_edit)

        self.resub_template_edit = QLineEdit(self.cfg.get("resub_template", "{emoji} {username} resubbed! {tier}"))
        ml.addRow("Resub message", self.resub_template_edit)

        self.sub_emoji_edit = QLineEdit(self.cfg.get("sub_emoji", "🎉"))
        ml.addRow("Sub emoji", self.sub_emoji_edit)

        self.gift_emoji_edit = QLineEdit(self.cfg.get("gift_emoji", "🎁"))
        ml.addRow("Gift emoji", self.gift_emoji_edit)

        ml.addRow(QLabel("── YouTube ──────────────────"))

        self.member_template_edit = QLineEdit(self.cfg.get("member_template", "{emoji} {username} became a member!"))
        ml.addRow("Member message", self.member_template_edit)

        self.member_emoji_edit = QLineEdit(self.cfg.get("member_emoji", "🌟"))
        ml.addRow("Member emoji", self.member_emoji_edit)

        self.show_tier_check = QCheckBox("Show tier label (Tier 1 / Tier 2 / Tier 3)")
        self.show_tier_check.setChecked(self.cfg.get("show_tier", True))
        ml.addRow("", self.show_tier_check)

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1000, 30000)
        self.duration_spin.setSingleStep(500)
        self.duration_spin.setSuffix(" ms")
        self.duration_spin.setValue(self.cfg.get("duration_ms", 6000))
        ml.addRow("Display duration", self.duration_spin)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(10, 80)
        self.font_size_spin.setSuffix(" px")
        self.font_size_spin.setValue(self.cfg.get("font_size", 28))
        ml.addRow("Font size", self.font_size_spin)

        self.bubble_padding_spin = QSpinBox()
        self.bubble_padding_spin.setRange(4, 48)
        self.bubble_padding_spin.setSuffix(" px")
        self.bubble_padding_spin.setValue(self.cfg.get("bubble_padding", 12))
        ml.addRow("Bubble padding", self.bubble_padding_spin)

        self.tracking_follows_check = QCheckBox("Bubble follows Spung while it's visible")
        self.tracking_follows_check.setChecked(self.cfg.get("tracking_follows", False))
        self.tracking_follows_check.stateChanged.connect(self._save_and_push_message_config)
        ml.addRow("Follow Spung", self.tracking_follows_check)

        # Sound file picker
        sound_row = QHBoxLayout()
        self.sound_path_edit = QLineEdit(self.cfg.get("sound_file", ""))
        self.sound_path_edit.setPlaceholderText("No sound file selected")
        self.sound_path_edit.setReadOnly(True)
        sound_browse_btn = QPushButton("Browse...")
        sound_browse_btn.clicked.connect(self._browse_sound_file)
        sound_clear_btn = QPushButton("Clear")
        sound_clear_btn.clicked.connect(self._clear_sound_file)
        sound_row.addWidget(self.sound_path_edit)
        sound_row.addWidget(sound_browse_btn)
        sound_row.addWidget(sound_clear_btn)
        ml.addRow("Sound file", sound_row)

        save_msg_btn = QPushButton("Save & apply message settings")
        save_msg_btn.clicked.connect(self._save_and_push_message_config)
        ml.addRow("", save_msg_btn)

        tabs.addTab(msg_tab, "Message")

        # ── Tab 5: Test ──
        test_tab = QWidget()
        tel = QVBoxLayout(test_tab)
        tel.setContentsMargins(12, 12, 12, 12)
        tel.setSpacing(10)
        tel.addWidget(QLabel("Fire a fake subscriber alert\nto test the speech bubble overlay."))
        tel.addWidget(QLabel("Subscriber name:"))
        self.test_name = QLineEdit("TestViewer")
        tel.addWidget(self.test_name)
        fire_btn = QPushButton("Fire test alert")
        fire_btn.setStyleSheet("font-size: 14px; padding: 10px; background: #1d4ed8;")
        fire_btn.clicked.connect(self._fire_test_alert)
        tel.addWidget(fire_btn)
        tel.addStretch()

        tabs.addTab(test_tab, "Test")

        root.addWidget(tabs)

        # ── Right panel ──
        right = QVBoxLayout()
        right.setSpacing(10)

        svc_group = QGroupBox("Services")
        svc_layout = QVBoxLayout(svc_group)
        svc_layout.setSpacing(8)

        def make_svc_row(label, start_cb, stop_cb):
            row = QHBoxLayout()
            dot = StatusDot()
            lbl = QLabel(label)
            lbl.setFixedWidth(160)
            start = QPushButton("▶ Start")
            stop  = QPushButton("■ Stop")
            stop.setEnabled(False)
            start.clicked.connect(lambda: start_cb(start, stop, dot))
            stop.clicked.connect(lambda:  stop_cb(start, stop, dot))
            row.addWidget(dot)
            row.addWidget(lbl)
            row.addWidget(start)
            row.addWidget(stop)
            return row, dot, start, stop

        row, self.server_dot, self.server_start, self.server_stop = \
            make_svc_row("Hub server", self._start_server, self._stop_server)
        svc_layout.addLayout(row)

        row, self.tracker_dot, self.tracker_start, self.tracker_stop = \
            make_svc_row("YOLO tracker", self._start_tracker, self._stop_tracker)
        svc_layout.addLayout(row)

        row, self.twitch_dot, self.twitch_start, self.twitch_stop = \
            make_svc_row("Twitch listener", self._start_twitch, self._stop_twitch)
        svc_layout.addLayout(row)

        row, self.yt_dot, self.yt_start, self.yt_stop = \
            make_svc_row("YouTube listener", self._start_youtube, self._stop_youtube)
        svc_layout.addLayout(row)

        right.addWidget(svc_group)

        preview_group = QGroupBox("Camera preview")
        pg = QVBoxLayout(preview_group)
        self.preview_label = QLabel("Tracker not running")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(320)
        self.preview_label.setStyleSheet("background:#0a0a0a; color:#444; border-radius:6px;")
        pg.addWidget(self.preview_label)
        right.addWidget(preview_group, 2)

        log_group = QGroupBox("Log")
        lg = QVBoxLayout(log_group)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(QFont("Consolas", 9))
        self.log_box.setFixedHeight(160)
        lg.addWidget(self.log_box)
        right.addWidget(log_group)

        root.addLayout(right, 1)

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget  { background:#1a1a1a; color:#e0e0e0; }
            QTabWidget::pane      { border:1px solid #333; border-radius:6px; }
            QTabBar::tab          { background:#2a2a2a; color:#aaa; padding:8px 16px;
                                    border-radius:4px; margin-right:2px; }
            QTabBar::tab:selected { background:#3b3b3b; color:#fff; }
            QGroupBox             { border:1px solid #333; border-radius:8px;
                                    margin-top:8px; padding:8px; color:#aaa; font-weight:bold; }
            QGroupBox::title      { subcontrol-origin:margin; left:8px; }
            QPushButton           { background:#2d2d2d; border:1px solid #444;
                                    border-radius:6px; padding:6px 14px; color:#e0e0e0; }
            QPushButton:hover     { background:#3a3a3a; }
            QPushButton:disabled  { color:#555; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background:#2d2d2d; border:1px solid #444;
                border-radius:4px; padding:4px 8px; color:#e0e0e0; }
            QTextEdit             { background:#111; border:1px solid #333;
                                    border-radius:4px; color:#22c55e; }
            QLabel                { color:#ccc; }
            QCheckBox             { color:#ccc; }
        """)

    # ── Settings ──────────────────────────────────────────────────────────────
    def _save_settings(self):
        self.cfg.update({
            "client_id":      self.client_id_edit.text().strip(),
            "client_secret":  self.client_secret_edit.text().strip(),
            "broadcaster_id": self.broadcaster_id_edit.text().strip(),
            "camera":         self.camera_spin.value(),
            "target":         self.target_edit.text().strip(),
            "confidence":     self.conf_spin.value(),
            "infer_fps":      self.fps_spin.value(),
            "infer_size":     int(self.size_combo.currentText()),
            "use_cuda":       self.cuda_check.isChecked(),
            "tracker_mode":   self.mode_combo.currentData(),
            "sub_template":   self.sub_template_edit.text().strip(),
            "gift_template":  self.gift_template_edit.text().strip(),
            "resub_template": self.resub_template_edit.text().strip(),
            "sub_emoji":      self.sub_emoji_edit.text().strip(),
            "gift_emoji":     self.gift_emoji_edit.text().strip(),
            "member_template": self.member_template_edit.text().strip(),
            "member_emoji":    self.member_emoji_edit.text().strip(),
            "show_tier":      self.show_tier_check.isChecked(),
            "duration_ms":    self.duration_spin.value(),
            "tracking_follows": self.tracking_follows_check.isChecked(),
            "font_size":        self.font_size_spin.value(),
            "bubble_padding":   self.bubble_padding_spin.value(),
            "sound_file":       self.sound_path_edit.text().strip(),
            "youtube_channel_url": self.yt_channel_url_edit.text().strip(),
        })
        save_config(self.cfg)
        self._log("[app] Settings saved")

    def _log(self, msg):
        self.log_box.append(msg)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum())

    # ── Server ────────────────────────────────────────────────────────────────
    def _start_server(self, start_btn, stop_btn, dot):
        self._server_worker = SubprocessWorker("server.py")
        self._server_thread = QThread()
        self._server_worker.moveToThread(self._server_thread)
        self._server_thread.started.connect(self._server_worker.run)
        self._server_worker.log.connect(self._log)
        self._server_worker.stopped.connect(lambda: dot.set_off())
        self._server_thread.start()
        start_btn.setEnabled(False)
        stop_btn.setEnabled(True)
        dot.set_on()
        self._log("[server] Starting...")

    def _stop_server(self, start_btn, stop_btn, dot):
        if self._server_worker: self._server_worker.stop()
        if self._server_thread: self._server_thread.quit(); self._server_thread.wait()
        start_btn.setEnabled(True)
        stop_btn.setEnabled(False)
        dot.set_off()
        self._log("[server] Stopped")

    # ── Tracker ───────────────────────────────────────────────────────────────
    def _start_tracker(self, start_btn, stop_btn, dot):
        self._save_settings()
        mode = self.cfg.get("tracker_mode", "oneshot")

        if mode == "oneshot":
            # Load model once, sleep between events
            self._tracker_worker = OnDemandTrackerWorker(self.cfg.copy())
            self._tracker_thread = QThread()
            self._tracker_worker.moveToThread(self._tracker_thread)
            self._tracker_thread.started.connect(self._tracker_worker.run)
            self._tracker_worker.log.connect(self._log)
            self._tracker_worker.stopped.connect(lambda: dot.set_off())
            self._tracker_thread.start()
            self.preview_label.setText(
                "On-demand mode\nModel loading... ready once log says 'waiting for sub event'")
        else:
            # Continuous — always running with preview
            self._tracker_worker = ContinuousTrackerWorker(self.cfg.copy())
            self._tracker_thread = QThread()
            self._tracker_worker.moveToThread(self._tracker_thread)
            self._tracker_thread.started.connect(self._tracker_worker.run)
            self._tracker_worker.frame_ready.connect(self._on_frame)
            self._tracker_worker.log.connect(self._log)
            self._tracker_worker.stopped.connect(lambda: dot.set_off())
            self._tracker_thread.start()
            self._preview_timer.start(33)

        self._tracker_active = True
        start_btn.setEnabled(False)
        stop_btn.setEnabled(True)
        dot.set_on()
        # Push current message config so server has tracking_follows up to date
        self._save_and_push_message_config()

    def _stop_tracker(self, start_btn, stop_btn, dot):
        self._tracker_active = False
        if self._tracker_worker:
            self._tracker_worker.stop()
            self._tracker_worker = None
        if self._tracker_thread:
            self._tracker_thread.quit()
            self._tracker_thread.wait()
            self._tracker_thread = None
        self._preview_timer.stop()
        self.preview_label.setText("Tracker not running")
        start_btn.setEnabled(True)
        stop_btn.setEnabled(False)
        dot.set_off()
        self._log("[tracker] Stopped")

    def _on_frame(self, frame, detection):
        with self._frame_lock:
            self._pending_frame     = frame
            self._pending_detection = detection

    def _flush_preview(self):
        with self._frame_lock:
            frame     = self._pending_frame
            detection = self._pending_detection
        if frame is None:
            return
        if detection:
            x1, y1, x2, y2 = [int(v) for v in detection[2]]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 80), 2)
            cv2.putText(frame, "Spung", (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 80), 2)
        else:
            cv2.putText(frame, "No Spung detected", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 60, 200), 2)
        rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        img  = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
        pix  = QPixmap.fromImage(img)
        lw, lh = self.preview_label.width(), self.preview_label.height()
        self.preview_label.setPixmap(
            pix.scaled(lw, lh, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation))

    # ── Twitch ────────────────────────────────────────────────────────────────
    def _start_twitch(self, start_btn, stop_btn, dot):
        self._save_settings()
        if not self.cfg.get("client_id") or \
           not self.cfg.get("client_secret") or \
           not self.cfg.get("broadcaster_id"):
            self._log("[twitch] ERROR: Fill in Twitch credentials in the Twitch tab first!")
            return
        self._twitch_worker = SubprocessWorker("twitch_listener.py")
        self._twitch_thread = QThread()
        self._twitch_worker.moveToThread(self._twitch_thread)
        self._twitch_thread.started.connect(self._twitch_worker.run)
        self._twitch_worker.log.connect(self._log)
        self._twitch_worker.stopped.connect(lambda: dot.set_off())
        self._twitch_thread.start()
        start_btn.setEnabled(False)
        stop_btn.setEnabled(True)
        dot.set_on()
        self._log("[twitch] Starting...")

    def _stop_twitch(self, start_btn, stop_btn, dot):
        if self._twitch_worker: self._twitch_worker.stop()
        if self._twitch_thread: self._twitch_thread.quit(); self._twitch_thread.wait()
        start_btn.setEnabled(True)
        stop_btn.setEnabled(False)
        dot.set_off()
        self._log("[twitch] Stopped")

    # ── YouTube ───────────────────────────────────────────────────────────────
    def _start_youtube(self, start_btn, stop_btn, dot):
        self._save_settings()
        if not self.cfg.get("youtube_channel_url"):
            self._log("[youtube] ERROR: Fill in the YouTube Video ID in the YouTube tab first!")
            return
        self._yt_worker = SubprocessWorker("youtube_listener.py")
        self._yt_thread = QThread()
        self._yt_worker.moveToThread(self._yt_thread)
        self._yt_thread.started.connect(self._yt_worker.run)
        self._yt_worker.log.connect(self._log)
        self._yt_worker.stopped.connect(lambda: dot.set_off())
        self._yt_thread.start()
        start_btn.setEnabled(False)
        stop_btn.setEnabled(True)
        dot.set_on()
        self._log("[youtube] Starting...")

    def _stop_youtube(self, start_btn, stop_btn, dot):
        if self._yt_worker: self._yt_worker.stop()
        if self._yt_thread: self._yt_thread.quit(); self._yt_thread.wait()
        start_btn.setEnabled(True)
        stop_btn.setEnabled(False)
        dot.set_off()
        self._log("[youtube] Stopped")

    # ── Message config ────────────────────────────────────────────────────────
    def _save_and_push_message_config(self, *_):
        self._save_settings()
        try:
            r = requests.post(f"{SERVER_URL}/message-config", json={
                "sub_template":    self.cfg["sub_template"],
                "gift_template":   self.cfg["gift_template"],
                "resub_template":  self.cfg["resub_template"],
                "sub_emoji":       self.cfg["sub_emoji"],
                "gift_emoji":      self.cfg["gift_emoji"],
                "member_template": self.cfg.get("member_template", "{emoji} {username} became a member!"),
                "member_emoji":    self.cfg.get("member_emoji", "🌟"),
                "show_tier":       self.cfg["show_tier"],
                "duration_ms":     self.cfg["duration_ms"],
                "tracking_follows": self.cfg.get("tracking_follows", False),
                "font_size":        self.cfg.get("font_size", 28),
                "bubble_padding":   self.cfg.get("bubble_padding", 12),
                "sound_file":       self.cfg.get("sound_file", ""),
            }, timeout=2)
            if r.status_code == 200:
                self._log("[msg] Message config applied")
            else:
                self._log(f"[msg] Server returned {r.status_code} — is the server running?")
        except Exception as e:
            self._log(f"[msg] Could not reach server — start Hub server first! ({e})")

    # ── Sound file picker ────────────────────────────────────────────────────
    def _browse_sound_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select sound file", "",
            "Audio files (*.mp3 *.wav *.ogg *.flac);;All files (*)"
        )
        if path:
            self.sound_path_edit.setText(path)
            self._save_and_push_message_config()

    def _clear_sound_file(self):
        self.sound_path_edit.setText("")
        self._save_and_push_message_config()

    # ── Test / sub alert ──────────────────────────────────────────────────────
    def _send_subscribe_post(self, name):
        try:
            r = requests.post(f"{SERVER_URL}/subscribe", json={
                "username": name, "tier": "1000", "is_gift": False,
            }, timeout=2)
            if r.status_code == 200:
                self._log(f"[test] Alert fired: {name}")
            else:
                self._log(f"[test] Server returned {r.status_code} — is server running?")
        except Exception as e:
            self._log(f"[test] Failed — start Hub server first! ({e})")

    def _fire_test_alert(self):
        name = self.test_name.text().strip() or "TestViewer"
        mode = self.cfg.get("tracker_mode", "oneshot")

        if mode == "oneshot" and self._tracker_active and \
                isinstance(self._tracker_worker, OnDemandTrackerWorker):
            # Trigger the always-loaded model to scan, then fire the alert once found
            self._log("[tracker] Waking tracker to locate Spung...")

            def wait_for_found():
                # Connect found signal once
                found_event = threading.Event()

                def on_found():
                    found_event.set()

                self._tracker_worker.found.connect(on_found)
                self._tracker_worker.trigger()
                found_event.wait(timeout=8.0)
                try:
                    self._tracker_worker.found.disconnect(on_found)
                except Exception:
                    pass
                self._send_subscribe_post(name)

            threading.Thread(target=wait_for_found, daemon=True).start()
        else:
            self._send_subscribe_post(name)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        self._save_settings()
        if self._tracker_worker: self._tracker_worker.stop()
        if self._twitch_worker:  self._twitch_worker.stop()
        if self._yt_worker:      self._yt_worker.stop()
        if self._server_worker:  self._server_worker.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())