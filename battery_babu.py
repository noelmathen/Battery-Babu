# Battery Babu - CCTV-based lights/keys alert

import cv2, time, json, os, requests, numpy as np
from datetime import datetime
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()  # loads .env from current folder

# ====== CONFIG FROM ENV ======
DVR_USER   = os.getenv("DVR_USER", "")
DVR_PASS   = os.getenv("DVR_PASS", "")
DVR_IP     = os.getenv("DVR_IP", "")
DVR_PORT   = os.getenv("DVR_PORT", "554")
DVR_CH     = os.getenv("DVR_CHANNEL", "1")
DVR_ST     = os.getenv("DVR_SUBTYPE", "1")  # 0=main, 1=sub

# Build RTSP safely (URL-encode password)
RTSP_URL = f"rtsp://{DVR_USER}:{quote(DVR_PASS)}@{DVR_IP}:{DVR_PORT}/cam/realmonitor?channel={DVR_CH}&subtype={DVR_ST}"

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")

ALERT_AFTER_SEC    = int(os.getenv("ALERT_AFTER_SEC", "120"))   # time lights must stay ON
COOLDOWN_SEC       = int(os.getenv("COOLDOWN_SEC", "900"))      # gap between alerts
FRAME_SAMPLE_EVERY = int(os.getenv("FRAME_SAMPLE_EVERY", "5"))  # process every Nth frame
ABS_THRESH         = int(os.getenv("ABS_THRESH", "115"))        # base V-channel threshold (0..255)
DELTA_OVER_BASE    = int(os.getenv("DELTA_OVER_BASE", "30"))    # extra over ambient
SAVE_FRAME_ON_ALERT= os.getenv("SAVE_FRAME_ON_ALERT", "1") == "1"
CONFIG_FILE        = os.getenv("ROI_FILE", "lights_alert_roi.json")
WINDOW_TITLE       = "Battery Babu — Select ROI over lamps (ENTER to confirm)"
# =============================================

def send_telegram(msg, image=None):
    if not BOT_TOKEN or not CHAT_ID: 
        print("Telegram not configured; skipping message:", msg)
        return
    try:
        if image is not None:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            files = {"photo": ("frame.jpg", image, "image/jpeg")}
            data = {"chat_id": CHAT_ID, "caption": msg}
            requests.post(url, data=data, files=files, timeout=8)
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.get(url, params={"chat_id": CHAT_ID, "text": msg}, timeout=8)
    except Exception as e:
        print("Telegram error:", e)

def pick_roi(frame):
    r = cv2.selectROIs(WINDOW_TITLE, frame, showCrosshair=True)
    cv2.destroyWindow(WINDOW_TITLE)
    rois = [tuple(map(int, rect)) for rect in r]  # (x,y,w,h)
    return rois

def load_or_create_roi(cap):
    if os.path.exists(CONFIG_FILE):
        try:
            return json.load(open(CONFIG_FILE, "r"))["rois"]
        except Exception:
            pass
    # capture one frame for ROI selection
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError("Cannot read frame to select ROI")
    rois = pick_roi(frame)
    json.dump({"rois": rois}, open(CONFIG_FILE, "w"))
    return rois

def roi_mean_v(frame, rois):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    V = hsv[:,:,2]
    vals = []
    for (x,y,w,h) in rois:
        roi = V[y:y+h, x:x+w]
        if roi.size:
            vals.append(float(np.mean(roi)))
    return np.mean(vals) if vals else 0.0

def open_stream(url):
    cap = cv2.VideoCapture(url)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap

def main():
    last_alert = 0.0
    lights_on_accum = 0.0
    base_dark = None
    frame_idx = 0

    while True:
        cap = open_stream(RTSP_URL)
        if not cap.isOpened():
            print("Waiting for RTSP stream...")
            time.sleep(5); continue

        try:
            rois = load_or_create_roi(cap)
            if not rois:
                print("No ROI selected; exiting.")
                return
        except Exception as e:
            print("ROI error:", e); time.sleep(3); continue

        while True:
            ok, frame = cap.read()
            if not ok:
                print("Stream drop → reconnect shortly...")
                break

            frame_idx += 1
            if frame_idx % FRAME_SAMPLE_EVERY:
                continue

            vmean = roi_mean_v(frame, rois)

            # Track ambient darkness; only update when not bright
            if base_dark is None:
                base_dark = vmean
            if vmean < max(ABS_THRESH - 10, 0):
                base_dark = 0.95*base_dark + 0.05*vmean

            dyn_thresh = max(ABS_THRESH, (base_dark or 0) + DELTA_OVER_BASE)
            lights_on = vmean >= dyn_thresh

            # Estimate dt from stream FPS; default to 10 FPS if unknown
            fps = cap.get(cv2.CAP_PROP_FPS)
            fps = fps if fps and fps > 0 else 10
            dt = FRAME_SAMPLE_EVERY / fps
            lights_on_accum = lights_on_accum + dt if lights_on else 0.0

            now = time.time()
            if lights_on_accum >= ALERT_AFTER_SEC and now - last_alert >= COOLDOWN_SEC:
                ts = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
                msg = f"⚡ Battery Babu: Lights look ON for {int(lights_on_accum)}s (V≈{vmean:.0f} ≥ {dyn_thresh:.0f}) @ {ts}"
                if SAVE_FRAME_ON_ALERT:
                    ok2, jpg = cv2.imencode(".jpg", frame)
                    send_telegram(msg, image=jpg.tobytes() if ok2 else None)
                else:
                    send_telegram(msg)
                last_alert = now
                lights_on_accum = 0.0

            time.sleep(0.01)  # tiny breather

        cap.release()
        time.sleep(2)

if __name__ == "__main__":
    main()
