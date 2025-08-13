# Battery Babu — CCTV-based lights/keys alert (resilient + verbose + day/night robust)
# deps: pip install opencv-python numpy requests python-dotenv

import os, cv2, time, json, logging, requests, numpy as np
from logging.handlers import RotatingFileHandler
from datetime import datetime
from urllib.parse import quote
from dotenv import load_dotenv

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

load_dotenv()

# ======= CONFIG (ENV) =======
DVR_USER    = os.getenv("DVR_USER", "")
DVR_PASS    = os.getenv("DVR_PASS", "")
DVR_IP      = os.getenv("DVR_IP", "")
DVR_PORT    = os.getenv("DVR_PORT", "554")
DVR_CH      = os.getenv("DVR_CHANNEL", "1")
DVR_ST      = os.getenv("DVR_SUBTYPE", "1")  # 0=main, 1=sub

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "")

ALERT_AFTER_SEC     = int(os.getenv("ALERT_AFTER_SEC", "120"))
COOLDOWN_SEC        = int(os.getenv("COOLDOWN_SEC", "900"))
FRAME_SAMPLE_EVERY  = int(os.getenv("FRAME_SAMPLE_EVERY", "5"))

# Absolute check (good at night)
ABS_THRESH          = int(os.getenv("ABS_THRESH", "115"))
DELTA_OVER_BASE     = int(os.getenv("DELTA_OVER_BASE", "30"))

# Relative check (good in daylight)
RING_PX             = int(os.getenv("RING_PX", "24"))   # ring thickness around ROI
V_QUANTILE          = float(os.getenv("V_QUANTILE", "0.90"))  # use bright core of lamp
MIN_RATIO           = float(os.getenv("MIN_RATIO", "1.35"))   # V90 / V_bg
MIN_DIFF            = float(os.getenv("MIN_DIFF", "35"))      # V90 - V_bg

SAVE_FRAME_ON_ALERT = os.getenv("SAVE_FRAME_ON_ALERT", "1") == "1"
CONFIG_FILE         = os.getenv("ROI_FILE", "lights_alert_roi.json")

STATUS_EVERY_SEC    = int(os.getenv("STATUS_EVERY_SEC", "5"))
STALL_TIMEOUT_SEC   = int(os.getenv("STALL_TIMEOUT_SEC", "15"))
RECONNECT_BASE_SEC  = int(os.getenv("RECONNECT_BASE_SEC", "3"))
RECONNECT_MAX_SEC   = int(os.getenv("RECONNECT_MAX_SEC", "30"))
LOG_FILE            = os.getenv("LOG_FILE", "battery_babu.log")
LOG_LEVEL           = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE_MAX_MB     = int(os.getenv("LOG_ROTATE_MB", "5"))
WINDOW_TITLE        = "Battery Babu — Select ROI over lamps (ENTER to confirm)"
# ============================

def setup_logging():
    lvl = getattr(logging, LOG_LEVEL, logging.INFO)
    logging.basicConfig(level=lvl, format="%(asctime)s | %(levelname)s | %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(LOG_FILE, maxBytes=LOG_FILE_MAX_MB*1_000_000, backupCount=3)
    fh.setLevel(lvl)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logging.getLogger().addHandler(fh)
setup_logging()

def build_rtsp():
    return f"rtsp://{DVR_USER}:{quote(DVR_PASS)}@{DVR_IP}:{DVR_PORT}/cam/realmonitor?channel={DVR_CH}&subtype={DVR_ST}"

def send_telegram(msg, image=None):
    if not BOT_TOKEN or not CHAT_ID:
        logging.debug("Telegram not configured; skip: %s", msg); return
    try:
        if image is not None:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            files = {"photo": ("frame.jpg", image, "image/jpeg")}
            data  = {"chat_id": CHAT_ID, "caption": msg}
            requests.post(url, data=data, files=files, timeout=10)
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.get(url, params={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        logging.info("Telegram sent.")
    except Exception as e:
        logging.warning("Telegram error: %s", e)

def pick_roi(frame):
    r = cv2.selectROIs(WINDOW_TITLE, frame, showCrosshair=True)
    cv2.destroyWindow(WINDOW_TITLE)
    rois = [tuple(map(int, rect)) for rect in r]
    logging.info("ROI selected: %s", rois)
    return rois

def validate_rois(shape, rois):
    H, W = shape[:2]; out=[]
    for (x,y,w,h) in rois:
        if w<=1 or h<=1: continue
        x, y = max(0,x), max(0,y)
        x2, y2 = min(W, x+w), min(H, y+h)
        out.append((int(x),int(y),int(x2-x),int(y2-y)))
    return out

def load_or_create_roi(cap):
    if os.path.exists(CONFIG_FILE):
        try:
            cfg  = json.load(open(CONFIG_FILE,"r"))
            rois = cfg.get("rois",[])
            ok, frame = cap.read()
            if not ok: raise RuntimeError("No frame to validate ROI")
            rois = validate_rois(frame.shape, rois)
            if rois: 
                logging.info("Loaded ROI from %s: %s", CONFIG_FILE, rois)
                return rois
            else:
                logging.warning("ROI file invalid; reselecting.")
        except Exception as e:
            logging.warning("ROI load failed (%s). Reselecting.", e)
    ok, frame = cap.read()
    if not ok: raise RuntimeError("Cannot read frame to select ROI")
    rois = validate_rois(frame.shape, pick_roi(frame))
    if not rois: raise RuntimeError("Empty ROI; select at least one box.")
    try: json.dump({"rois": rois}, open(CONFIG_FILE,"w"))
    except Exception as e: logging.warning("Could not save ROI: %s", e)
    return rois

def ring_mask(shape, roi, ring_px=20):
    H,W = shape[:2]
    x,y,w,h = roi
    x1,y1,x2,y2 = x,y,x+w,y+h
    # outer rect with padding
    xo1, yo1 = max(0, x1-ring_px), max(0, y1-ring_px)
    xo2, yo2 = min(W, x2+ring_px), min(H, y2+ring_px)
    mask = np.zeros((H,W), np.uint8)
    mask[yo1:yo2, xo1:xo2] = 1
    mask[y1:y2, x1:x2] = 0   # remove inner ROI → ring
    return mask

def roi_stats(frame, rois):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    V = hsv[:,:,2]
    qs = []
    for (x,y,w,h) in rois:
        roi = V[y:y+h, x:x+w]
        if roi.size:
            qs.append(float(np.quantile(roi, V_QUANTILE)))
    v90 = float(np.mean(qs)) if qs else 0.0
    return v90, V  # also return full V for bg calc

def bg_from_rings(V, shape, rois, ring_px=20):
    masks = [ring_mask(shape, r, ring_px) for r in rois]
    if not masks: return 0.0
    m = np.clip(np.sum(masks, axis=0), 0, 1).astype(bool)
    vals = V[m]
    return float(np.median(vals)) if vals.size else 0.0

def open_stream(url):
    logging.info("Opening RTSP: %s", url.replace(DVR_PASS, "***") if DVR_PASS else url)
    cap = cv2.VideoCapture(url)
    try: cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception: pass
    return cap

def monitor():
    rtsp = build_rtsp()
    backoff = RECONNECT_BASE_SEC
    last_status = time.monotonic()
    last_good   = time.monotonic()
    last_alert  = 0.0
    lights_on_accum = 0.0
    base_dark = None
    frame_idx = 0

    while True:
        try:
            cap = open_stream(rtsp)
            if not cap.isOpened():
                logging.warning("RTSP not opened; retrying in %ss", backoff)
                time.sleep(backoff); backoff = min(RECONNECT_MAX_SEC, max(RECONNECT_BASE_SEC, backoff*2))
                continue

            try:
                rois = load_or_create_roi(cap)
            except Exception as e:
                logging.error("ROI setup error: %s", e); time.sleep(3); continue

            logging.info("Monitoring… alert_after=%ss, cooldown=%ss", ALERT_AFTER_SEC, COOLDOWN_SEC)
            backoff = RECONNECT_BASE_SEC
            last_status = last_good = time.monotonic()
            frame_idx = 0

            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    if time.monotonic() - last_good > STALL_TIMEOUT_SEC:
                        logging.warning("Stream stalled > %ss. Reconnecting…", STALL_TIMEOUT_SEC); break
                    time.sleep(0.05); continue

                last_good = time.monotonic()
                frame_idx += 1
                if frame_idx % FRAME_SAMPLE_EVERY:
                    if time.monotonic() - last_status >= STATUS_EVERY_SEC:
                        logging.info("Heartbeat: processed=%d, waiting for sample…", frame_idx)
                        last_status = time.monotonic()
                    continue

                try:
                    v90, V = roi_stats(frame, rois)                  # bright core in ROI
                    v_bg    = bg_from_rings(V, frame.shape, rois, RING_PX)  # local background

                    # Ambient baseline follows background (not the lamp ROI)
                    if base_dark is None: base_dark = v_bg
                    if v90 < max(ABS_THRESH-10, 0):  # only adjust baseline when lamp likely off
                        base_dark = 0.95*(base_dark or v_bg) + 0.05*v_bg

                    dyn_abs_thr = max(ABS_THRESH, (base_dark or 0) + DELTA_OVER_BASE)

                    # Conditions
                    abs_ok = v90 >= dyn_abs_thr
                    ratio  = (v90 / max(v_bg, 1.0))
                    diff   = (v90 - v_bg)
                    rel_ok = (ratio >= MIN_RATIO) or (diff >= MIN_DIFF)

                    lights_on = abs_ok or rel_ok

                    # fps/dt
                    fps = cap.get(cv2.CAP_PROP_FPS); fps = fps if fps and fps>0 else 10
                    dt  = FRAME_SAMPLE_EVERY / fps
                    lights_on_accum = lights_on_accum + dt if lights_on else 0.0

                    # Status line
                    if time.monotonic() - last_status >= STATUS_EVERY_SEC:
                        state = "ON " if lights_on else "off"
                        logging.info(
                            "State=%s | V90=%.0f bg=%.0f | ratio=%.2f>=%.2f diff=%.0f>=%.0f | abs_thr=%.0f | accum=%.1fs | fps=%.1f | frames=%d",
                            state, v90, v_bg, ratio, MIN_RATIO, diff, MIN_DIFF, dyn_abs_thr,
                            lights_on_accum, fps, frame_idx
                        )
                        last_status = time.monotonic()

                    # Alert
                    now = time.time()
                    if lights_on_accum >= ALERT_AFTER_SEC and now - last_alert >= COOLDOWN_SEC:
                        ts  = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
                        # msg = (f"⚡ Battery Babu: Lights look ON for more than {int(lights_on_accum)}s "
                        #        f"(V90≈{v90:.0f}, bg≈{v_bg:.0f}, ratio={ratio:.2f}, diff={diff:.0f}) @ {ts}")
                        msg = (f"⚡ Battery Babu: Lights look ON for more than {int(lights_on_accum)}s @ {ts}")
                        if SAVE_FRAME_ON_ALERT:
                            try:
                                ok2, jpg = cv2.imencode(".jpg", frame)
                                send_telegram(msg, image=jpg.tobytes() if ok2 else None)
                            except Exception as e:
                                logging.warning("Snapshot encode/send error: %s", e); send_telegram(msg)
                        else:
                            send_telegram(msg)
                        logging.info("Alert sent; cooldown %ss.", COOLDOWN_SEC)
                        last_alert = now
                        lights_on_accum = 0.0

                    time.sleep(0.01)

                except Exception as e:
                    logging.error("Processing error: %s", e); time.sleep(0.2); continue

            try: cap.release()
            except Exception: pass
            logging.info("Reconnecting in %ss…", backoff)
            time.sleep(backoff)
            backoff = min(RECONNECT_MAX_SEC, max(RECONNECT_BASE_SEC, backoff*2))

        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt: exiting."); 
            try: cap.release()
            except Exception: pass
            break
        except Exception as e:
            logging.error("Top-level error: %s", e)
            time.sleep(backoff)
            backoff = min(RECONNECT_MAX_SEC, max(RECONNECT_BASE_SEC, backoff*2))

def main():
    logging.info("Boot: DVR=%s@%s ch=%s st=%s | alert_after=%ss | cooldown=%ss",
                 DVR_USER or "<user>", DVR_IP or "<ip>", DVR_CH, DVR_ST, ALERT_AFTER_SEC, COOLDOWN_SEC)
    if not (DVR_USER and DVR_PASS and DVR_IP):
        logging.warning("Missing DVR_USER/DVR_PASS/DVR_IP in .env")
    if not (BOT_TOKEN and CHAT_ID):
        logging.warning("Telegram not configured; alerts will NOT be sent.")
    monitor()

if __name__ == "__main__":
    main()
