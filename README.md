# SPUNG Tracker — Setup Guide

---
## 1. Installation

Using git:
```powershell
git clone https://github.com/OsamaT-net/SpungTracker-Twitch.git
```
Direct Download:
1. Click <>Code
2. Download ZIP
3. Extract

---

## 2. Dependencies

Requires **Python 3.10 or newer**. (Python 3.14 Reccomended)

```powershell
pip install -r requirements.txt
pip install PyQt6
```

---

## 3. Usage

```powershell
python app.py
```

Start services inside the app in this order:

1. **Hub server**
2. **YOLO tracker**
3. **Twitch listener**  
4. **YouTube listener**

---

## 4. NVIDIA CUDA (NVIDIA GPUs only) RECCOMENDED

YOLO is extremely CPU intensive, this offloads the load to the gpu and runs it efficiently

```powershell
pip3 install torch torchvision --index-url https://download.pytorch.org/whl/cu130
```

---

## 5. Twitch Credentials

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
**https://streamscharts.com/tools/convert-username**

### Enter credentials in the app
Open the app → go to the **Twitch** tab → paste all three values → click **Save Twitch credentials**.

> **Note:** The Twitch listener requires your channel to be a Twitch Affiliate or Partner to receive real subscriber events. You can test the full bubble pipeline without it using the **Test** tab in the app.

---

## 6. YouTube

The YouTube listener triggers the bubble whenever someone **purchases a new membership** on your live stream. No API key or OAuth setup required.

### Setup
1. Open the app → go to the **YouTube** tab
2. Paste your channel URL (e.g. `https://www.youtube.com/@YourChannel`) → click **Save YouTube settings**
3. Start the **YouTube listener** service from the Services panel

The listener will automatically detect your active live stream when it starts. If no live stream is found, an error will appear in the log.

> **Note:** The YouTube listener can run alongside the Twitch listener simultaneously — both can be active at the same time.

---

## 7. OBS Browser Source

1. In OBS, add a new **Browser Source** to your scene
2. Set the Width and Height to the same values as camera. 
2. Set:
   - **URL**: `http://localhost:8765/`
   - **Custom CSS**: `body { background: transparent !important; }`
   - Uncheck ☐ **"Shutdown source when not visible"**
3. Place the Browser Source **above** your camera/game layers so the bubble renders on top
4. Make sure the Hub server is running before OBS loads the source
5. (Optional) For audio, right click the OBS browser source → Properties → check Control audio via OBS
