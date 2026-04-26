# Spung — Setup Guide

---

## 1. Dependencies

Requires **Python 3.10 or newer**.

```powershell
pip install -r requirements.txt
pip install PyQt6
```

The YOLO model (`yolov8s-world.pt`, ~87 MB) downloads automatically the first time you start the tracker.

---

## 2. Installation

```powershell
python app.py
```

Start services inside the app in this order:

1. **Hub server**
2. **YOLO tracker**
3. **Twitch listener**

---

## 3. NVIDIA CUDA (optional — NVIDIA GPUs only)

Moves YOLO inference off the CPU onto the GPU, dropping CPU usage to near 0%.

First verify your GPU is detected:
```powershell
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

If it prints `False`, reinstall PyTorch with CUDA support:
```powershell
pip uninstall torch torchvision -y
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

Then verify again — it should now print `True` and your GPU name. Enable **"Use NVIDIA CUDA GPU"** in the app's 🎯 Tracker tab.

> **AMD GPU:** CUDA is NVIDIA-only. Leave CUDA unchecked and set Inference FPS to 3–5 to reduce CPU load.

---

## 4. Twitch Credentials

You need three values: **Client ID**, **Client Secret**, and **Broadcaster ID**.

### Client ID and Client Secret
1. Go to **https://dev.twitch.tv/console** and sign in
2. Click **Register Your Application**
3. Fill in:
   - **Name**: anything (e.g. `SpungTrackerBot`)
   - **OAuth Redirect URLs**: `http://localhost:17563`
   - **Category**: Broadcaster Suite
4. Click **Create**, then **Manage** on your new app
5. Click **New Secret**
6. Copy both your **Client ID** and **Client Secret**

### Broadcaster ID
Your numeric Twitch user ID (not your username). Find it at:
**https://www.streamweasels.com/tools/convert-twitch-username-to-user-id/**

### Enter credentials in the app
Open the app → go to the **📡 Twitch** tab → paste all three values → click **Save Twitch credentials**.

> **Note:** The Twitch listener requires your channel to be a Twitch Affiliate or Partner to receive real subscriber events. You can test the full bubble pipeline without it using the **🧪 Test** tab in the app.

---

## 5. OBS Browser Source

1. In OBS, add a new **Browser Source** to your scene
2. Set:
   - **URL**: `http://localhost:8765/`
   - **Width**: `1920`
   - **Height**: `1080`
   - **Custom CSS**: `body { background: transparent !important; }`
   - Uncheck ☐ **"Shutdown source when not visible"**
3. Place the Browser Source **above** your camera/game layers so the bubble renders on top
4. Make sure the Hub server is running before OBS loads the source
