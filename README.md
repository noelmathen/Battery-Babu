# Battery Babu

**Camera‑based alert that pings your phone if your Royal Enfield’s lights are left ON after parking(i.e, you forgot to turn the keys off).**
No wiring, no hardware mods — it uses your home DVR/IPC RTSP feed + a tiny Python script.

---

## ✨ What it does

- Watches a small region on your CCTV (headlamp / pilot lamps).
- If that region stays bright for a set time (e.g., 120 s), it **alerts you on Telegram** with a snapshot.
- Works day & night. Handles stream drops, reconnects, and keeps a rotating log.
- All secrets live in `.env` (ignored by Git).

---

## 🧠 How it works (high level)

```
Bike lights ON → Camera sees glow → DVR exposes RTSP stream
 → Battery Babu reads frames → checks brightness vs local background
 → If ON too long → Telegram bot sends alert → You turn key OFF
```

- Night: absolute brightness check is sufficient.
- Day: uses **relative contrast** (lamp core vs a ring/background around it) so sunlight or reflections don’t fool it.

---

## 📦 Project layout

```
Battery Babu/
├─ battery_babu.py          # main script
├─ .env                     # secrets & tunables (not committed)
├─ lights_alert_roi.json    # saved ROI boxes (auto-created)
├─ battery_babu.log         # rolling logs (auto-created)
├─ requirements.txt
└─ .gitignore
```

---

## ✅ Prerequisites

- Python 3.9+ (tested on Windows; works on Linux/Raspberry Pi too)
- A DVR/camera that exposes **RTSP** (e.g., CP Plus, Dahua OEM, etc.)
- A Telegram account (free) for alerts
- Optional: VLC to test your RTSP link

---

## 🔧 Install

```bash
# (optional) create venv
python -m venv babu_env
# Windows
babu_env\Scripts\activate
# Linux/macOS
# source babu_env/bin/activate

pip install -r requirements.txt
```

If you don’t have `requirements.txt` yet, the minimum is:

```
opencv-python
numpy
requests
python-dotenv
```

---

## 🔐 Configure `.env`

Create a file named **.env** in the project root with your secrets and settings:

```ini
# DVR / RTSP
DVR_USER=your_dvr_username
DVR_PASS=your_dvr_password
DVR_IP=192.168.1.xxx
DVR_PORT=554
DVR_CHANNEL=1
DVR_SUBTYPE=1           # 0 = main (HD), 1 = sub (lighter)

# Telegram (optional but recommended)
TELEGRAM_BOT_TOKEN=123456:ABC...   # from @BotFather
TELEGRAM_CHAT_ID=123456789         # your chat id

# Alert timing
ALERT_AFTER_SEC=120    # how long lights must stay ON before alert
COOLDOWN_SEC=900       # suppress duplicate alerts for this long

# Performance & logging
FRAME_SAMPLE_EVERY=5
LOG_FILE=battery_babu.log
LOG_LEVEL=INFO         # or DEBUG for more detail
LOG_ROTATE_MB=5
STATUS_EVERY_SEC=5
STALL_TIMEOUT_SEC=15
RECONNECT_BASE_SEC=3
RECONNECT_MAX_SEC=30

# Day/Night detection (tuning)
ABS_THRESH=115         # base absolute brightness threshold
DELTA_OVER_BASE=30     # baseline + margin (night help)

# Relative (daylight) settings
RING_PX=24             # ring thickness around ROI for background
V_QUANTILE=0.90        # use the bright core of lamp (0-1)
MIN_RATIO=1.35         # (lamp_core / background) minimum ratio
MIN_DIFF=35            # OR absolute brightness difference

# Output
SAVE_FRAME_ON_ALERT=1
ROI_FILE=lights_alert_roi.json
```

### Where do I get the RTSP link?

Most CP Plus/Dahua-style DVRs use:

```
rtsp://USER:PASS@DVR_IP:554/cam/realmonitor?channel=1&subtype=1
```

- Test in **VLC** → _Media_ → _Open Network Stream_ → paste your URL.
- If it plays, paste the same info into the `.env` fields above.

### Telegram bot setup (1 minute)

1. In Telegram, talk to **@BotFather** → `/newbot` → copy the **BOT_TOKEN**.
2. Start a chat with your new bot and send any message.
3. Visit: `https://api.telegram.org/bot<token>/getUpdates` → copy your **chat id**.
4. Put both into `.env`.

> Don’t commit `.env` — it’s in `.gitignore` by default.

---

## ▶️ Run

```bash
python battery_babu.py
```

- **First run**: a window opens → **draw one or more boxes** around the lamp(s) → press **ENTER**.The ROI is saved to `lights_alert_roi.json`.
- **Subsequent runs**: headless monitoring starts immediately.

You’ll see logs like:

```
2025-08-13 10:23:11 | INFO | Monitoring... alert_after=120s, cooldown=900s
2025-08-13 10:23:16 | INFO | State=off | V90=47 bg=43 | ratio=1.10>=1.35 diff=4>=35 | abs_thr=128 | accum=0.0s | fps=10.0 | frames=125
```

When the key is left ON long enough you’ll get a Telegram message with a snapshot.

---

## 🧪 Quick test

To verify quickly, in `.env` temporarily set:

```
ALERT_AFTER_SEC=5
COOLDOWN_SEC=10
```

Turn the key ON; you should get an alert within ~5–10 seconds. Revert to normal values afterwards.

---

## 🎛️ Tuning guide

**Daylight not triggering?**

- Make the ROI **tighter** around the **brightest part** of the headlamp lens.
- Try these friendlier daylight values in `.env`:
  ```ini
  V_QUANTILE=0.95
  RING_PX=28
  MIN_RATIO=1.20
  MIN_DIFF=20
  ```
- If needed, step `MIN_RATIO` down to `1.15` (last resort `1.10`).

**Too many alerts?**

- Increase `ALERT_AFTER_SEC` (e.g., 180).
- Increase `COOLDOWN_SEC`.
- Raise `ABS_THRESH` or `DELTA_OVER_BASE` slightly.

**CPU usage high?**

- Increase `FRAME_SAMPLE_EVERY` (e.g., 10).
- Use `DVR_SUBTYPE=1` (substream) instead of main stream.

**Camera moved / resolution changed?**

- Delete `lights_alert_roi.json` and run again to reselect ROI.

---

## 🗂 Logs

- Console + rotating file: `battery_babu.log`
- Change verbosity with `LOG_LEVEL=DEBUG`.
- “Heartbeat” means the script is alive and skipping non-sampled frames to keep RTSP fresh.
- Typical status line:

```
State=ON | V90=176 bg=120 | ratio=1.47>=1.20 diff=56>=20 | abs_thr=140 | accum=32.0s | fps=12.5 | frames=900
```

- Meanings:
  - `V90`: bright core inside ROI (90th percentile).
  - `bg`: local background from a ring around the ROI.
  - `ratio`/`diff`: relative checks (daylight).
  - `abs_thr`: absolute threshold (night).
  - `accum`: how long lights have been judged ON.

---

## 🚀 Autostart on Windows (Task Scheduler)

1. Open **Task Scheduler** → _Create Task_.
2. **General**: Name “Battery Babu”, check _Run whether user is logged on or not_.
3. **Triggers**: _At log on_.
4. **Actions**:
   - Program/script: path to `pythonw.exe` inside your venve.g., `C:\path\Battery Babu\babu_env\Scripts\pythonw.exe`
   - Arguments: `battery_babu.py`
   - Start in: project folder (e.g., `C:\path\Battery Babu`)
5. **Conditions**: uncheck “Start the task only if the computer is on AC power” (optional).
6. **OK** → enter your Windows password.

---

## 🛟 Troubleshooting

- **No video:** test RTSP in VLC; confirm IP, username/password, `channel`, and `subtype`.
- **Unicode error in terminal:** Windows console can choke on Unicode. Use ASCII-only logs (already default) or run with `PYTHONIOENCODING=utf-8`.
- **No Telegram alerts:** verify bot token & chat id; check internet; try sending a simple text first.
- **False positives at dawn/dusk:** slightly raise `MIN_RATIO`/`MIN_DIFF` and/or `ABS_THRESH`.
- **Script crashed?** It’s designed to auto‑recover. Check `battery_babu.log` for the reason; the next cycle will reconnect with backoff.

---

## 🔒 Security notes

- Change DVR default password; keep `.env` private (already git‑ignored).
- Keep the DVR on your LAN; don’t port‑forward RTSP to the internet unless you know what you’re doing.

---

## 🏷️ Name & credit

**Battery Babu** — idea & implementation guidance by you + your CCTV.
Enjoy a scold‑free life 😄
