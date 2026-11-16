#!/usr/bin/env python3
"""
Raspberry Pi Zero W camera + dual relay + buzzer dashboard.

Hardware:
  OV5647 camera: CSI connector
  Capture button: BCM GPIO17 to GND (internal pull-up)
  Relay 1 IN: BCM GPIO23
  Relay 2 IN: BCM GPIO24
  Passive buzzer: BCM GPIO22 through a suitable transistor driver

Install:
  sudo apt update
  sudo apt install -y python3-picamera2 python3-flask python3-gpiozero python3-lgpio python3-opencv
Run with system Python:
  python3 /home/uv/temp/app.py
"""

import atexit
import logging
import queue
import signal
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
from flask import (
    Flask, Response, abort, jsonify, render_template_string,
    send_from_directory
)
from gpiozero import Button, OutputDevice, PWMOutputDevice
from picamera2 import Picamera2


# ============================================================
# CONFIGURATION — EDIT THESE VALUES FOR YOUR HARDWARE
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
PHOTO_DIR = BASE_DIR / "photos"
PHOTO_DIR.mkdir(parents=True, exist_ok=True)

HOST = "0.0.0.0"
PORT = 5000

BUTTON_PIN = 17
RELAY_1_PIN = 23
RELAY_2_PIN = 24
BUZZER_PIN = 22

# Most 5V relay modules are active LOW. Change to True only if
# your relay board is confirmed active-HIGH.
RELAY_ACTIVE_HIGH = False

# Each relay has its own maximum continuous ON time.
# These are example defaults: Relay 1 = 60 seconds, Relay 2 = 120 seconds.
# Change them to the required number of seconds.
RELAY_1_MAX_ON_SECONDS = 60
RELAY_2_MAX_ON_SECONDS = 120

# Use a passive buzzer for different pitches/tones.
# This code uses PWMOutputDevice and is intended for a passive buzzer.
# If using a magnetic buzzer or one that draws significant current,
# drive it through a transistor; do not draw its current directly from GPIO.
BUZZER_ENABLED = True
BUZZER_VOLUME = 0.35  # 0.0 to 1.0

PHOTO_SIZE = (2592, 1944)  # OV5647 5MP still image
VIDEO_SIZE = (640, 480)
VIDEO_FPS = 10
VIDEO_JPEG_QUALITY = 70
STREAM_INTERVAL = 0.10

# ============================================================
# LOGGING / APP / LOCKS
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("PiControl")

app = Flask(__name__)
camera_lock = threading.RLock()
relay_lock = threading.RLock()
shutdown_event = threading.Event()

# Relay state is tracked separately from the physical output object.
relay_deadlines = {1: None, 2: None}
relay_started_at = {1: None, 2: None}
relay_limits = {
    1: max(1, int(RELAY_1_MAX_ON_SECONDS)),
    2: max(1, int(RELAY_2_MAX_ON_SECONDS)),
}


# ============================================================
# BUZZER: DISTINCT NON-BLOCKING SOUND PATTERNS
# ============================================================

# Pattern is a list of (frequency_hz, duration_seconds).
# frequency 0 means silence. Patterns run in a worker thread so
# Flask requests and camera capture are not blocked.
sound_queue = queue.Queue(maxsize=20)

SOUNDS = {
    "startup": [
        (660, 0.10), (0, 0.04), (880, 0.12), (0, 0.04), (1100, 0.18)
    ],
    "photo_ok": [
        (1200, 0.07), (0, 0.04), (1600, 0.10)
    ],
    "photo_error": [
        (330, 0.22), (0, 0.08), (260, 0.30)
    ],
    "relay_on": [
        (900, 0.08), (0, 0.04), (1200, 0.08)
    ],
    "relay_off": [
        (700, 0.12)
    ],
    "auto_off": [
        (1500, 0.12), (0, 0.08),
        (1500, 0.12), (0, 0.08),
        (1000, 0.30)
    ],
    "button": [
        (1000, 0.05)
    ],
    "error": [
        (300, 0.15), (0, 0.05), (300, 0.15)
    ],
}

buzzer = None
if BUZZER_ENABLED:
    try:
        buzzer = PWMOutputDevice(
            BUZZER_PIN,
            active_high=True,
            initial_value=0,
            frequency=1000
        )
        log.info("Buzzer initialized on BCM GPIO%d", BUZZER_PIN)
    except Exception:
        log.exception("Could not initialize buzzer; continuing without sound")
        buzzer = None


def play_sound(name):
    """Queue a sound event; never block the caller."""
    if buzzer is None or name not in SOUNDS or shutdown_event.is_set():
        return
    try:
        sound_queue.put_nowait(name)
    except queue.Full:
        log.warning("Buzzer queue full; skipped sound %s", name)


def buzzer_worker():
    while not shutdown_event.is_set():
        try:
            name = sound_queue.get(timeout=0.25)
        except queue.Empty:
            continue

        try:
            for frequency, duration in SOUNDS.get(name, []):
                if shutdown_event.is_set():
                    break
                if frequency <= 0:
                    buzzer.off()
                else:
                    buzzer.frequency = frequency
                    buzzer.value = BUZZER_VOLUME
                time.sleep(duration)
            if buzzer:
                buzzer.off()
        except Exception:
            log.exception("Buzzer playback error")
            try:
                buzzer.off()
            except Exception:
                pass
        finally:
            sound_queue.task_done()


if buzzer is not None:
    threading.Thread(target=buzzer_worker, name="buzzer-worker", daemon=True).start()


# ============================================================
# RELAYS
# ============================================================

log.info("Initializing relays")

relays = {
    1: OutputDevice(
        RELAY_1_PIN,
        active_high=RELAY_ACTIVE_HIGH,
        initial_value=False
    ),
    2: OutputDevice(
        RELAY_2_PIN,
        active_high=RELAY_ACTIVE_HIGH,
        initial_value=False
    ),
}


def _relay_status_locked(channel):
    is_on = bool(relays[channel].is_active)
    deadline = relay_deadlines[channel]
    remaining = max(0, int(deadline - time.monotonic())) if is_on and deadline else 0
    return {
        "on": is_on,
        "remaining_seconds": remaining,
        "max_on_seconds": relay_limits[channel],
        "gpio": RELAY_1_PIN if channel == 1 else RELAY_2_PIN,
    }


def get_all_relay_status():
    with relay_lock:
        return {
            "relay1": _relay_status_locked(1),
            "relay2": _relay_status_locked(2),
        }


def set_relay(channel, turn_on, reason="manual"):
    if channel not in (1, 2):
        raise ValueError("Relay channel must be 1 or 2")
    if not isinstance(turn_on, bool):
        raise ValueError("Relay state must be true or false")
    if shutdown_event.is_set():
        raise RuntimeError("Application is shutting down")

    with relay_lock:
        was_on = bool(relays[channel].is_active)

        if turn_on:
            if not was_on:
                relays[channel].on()
                relay_started_at[channel] = time.monotonic()
                relay_deadlines[channel] = (
                    relay_started_at[channel] + relay_limits[channel]
                )
                log.info("Relay %d turned ON (%s), auto-off in %ds",
                         channel, reason, relay_limits[channel])
                play_sound("relay_on")
            else:
                # An explicit ON command while already ON restarts its timer.
                relay_started_at[channel] = time.monotonic()
                relay_deadlines[channel] = (
                    relay_started_at[channel] + relay_limits[channel]
                )
                log.info("Relay %d ON timer reset (%s)", channel, reason)
        else:
            if was_on:
                relays[channel].off()
                log.info("Relay %d turned OFF (%s)", channel, reason)
                if reason == "auto_timeout":
                    play_sound("auto_off")
                else:
                    play_sound("relay_off")
            relay_started_at[channel] = None
            relay_deadlines[channel] = None

        return _relay_status_locked(channel)


def relay_timeout_worker():
    """Independently time out each relay using its own deadline."""
    while not shutdown_event.wait(0.2):
        expired = []
        now = time.monotonic()

        with relay_lock:
            for channel in (1, 2):
                deadline = relay_deadlines[channel]
                if relays[channel].is_active and deadline is not None and now >= deadline:
                    expired.append(channel)

        for channel in expired:
            try:
                set_relay(channel, False, reason="auto_timeout")
            except Exception:
                log.exception("Failed to auto-switch OFF relay %d", channel)


threading.Thread(
    target=relay_timeout_worker,
    name="relay-timeout-worker",
    daemon=True
).start()


# ============================================================
# CAMERA
# ============================================================

log.info("Initializing Raspberry Pi camera")
camera = Picamera2()

video_config = camera.create_video_configuration(
    main={"size": (1280, 960), "format": "RGB888"},
    lores={"size": VIDEO_SIZE, "format": "YUV420"},
    buffer_count=4,
    controls={"FrameRate": VIDEO_FPS},
)

still_config = camera.create_still_configuration(
    main={"size": PHOTO_SIZE, "format": "RGB888"},
    buffer_count=1,
)

camera.configure(video_config)
camera.start()
time.sleep(2)
log.info("Camera initialized")

# ============================================================
# PHOTO CAPTURE
# ============================================================

def capture_photo(source="web"):
    if shutdown_event.is_set():
        return {"success": False, "error": "Application is shutting down"}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"IMG_{timestamp}.jpg"
    filepath = PHOTO_DIR / filename

    try:
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        log.info("Photo capture requested by %s", source)

        with camera_lock:
            camera.switch_mode_and_capture_file(
                still_config,
                str(filepath),
                name="main",
            )

        if not filepath.is_file() or filepath.stat().st_size < 1000:
            raise RuntimeError("JPEG was not created or is unexpectedly small")

        log.info("Photo saved: %s (%d bytes)", filepath, filepath.stat().st_size)
        play_sound("photo_ok")
        return {
            "success": True,
            "filename": filename,
            "url": f"/images/{filename}",
            "size": filepath.stat().st_size,
            "timestamp": timestamp,
        }

    except Exception as exc:
        log.exception("Photo capture failed")
        try:
            filepath.unlink(missing_ok=True)
        except Exception:
            pass
        play_sound("photo_error")
        return {"success": False, "error": str(exc)}


def physical_button_pressed():
    if shutdown_event.is_set():
        return
    log.info("Physical capture button pressed")
    play_sound("button")
    result = capture_photo(source="physical_button")
    if not result["success"]:
        log.error("Physical photo capture failed: %s", result["error"])


capture_button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.4)
capture_button.when_pressed = physical_button_pressed


# ============================================================
# LIVE VIDEO
# ============================================================

def generate_video():
    while not shutdown_event.is_set():
        try:
            with camera_lock:
                frame = camera.capture_array("lores")

            # Picamera2 lores YUV420 is planar I420.
            bgr = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)
            ok, encoded = cv2.imencode(
                ".jpg", bgr,
                [cv2.IMWRITE_JPEG_QUALITY, VIDEO_JPEG_QUALITY]
            )
            if not ok:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Cache-Control: no-cache\r\n\r\n"
                + encoded.tobytes() + b"\r\n"
            )
            time.sleep(STREAM_INTERVAL)

        except GeneratorExit:
            break
        except Exception:
            log.exception("Video stream error")
            time.sleep(1)


# ============================================================
# WEB DASHBOARD
# ============================================================

HTML_PAGE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Raspberry Pi Control Center</title>
<style>
*{box-sizing:border-box}
body{margin:0;padding:16px;background:#101827;color:#f8fafc;font-family:Arial,sans-serif}
header,main{max-width:1100px;margin:auto}
h1{font-size:24px}h2{font-size:19px}
.muted{color:#94a3b8}
.card{background:#1e293b;padding:17px;margin:0 0 18px;border-radius:14px;border:1px solid #334155}
.pills{display:flex;gap:9px;flex-wrap:wrap}
.pill{background:#0f172a;padding:9px 12px;border-radius:8px;font-size:13px}
.good{color:#4ade80}.bad{color:#f87171}
.video{width:100%;max-width:800px;display:block;margin:14px auto;background:#000;border-radius:10px}
button{padding:12px 16px;border:0;border-radius:8px;cursor:pointer;font-size:14px;color:white}
button:disabled{opacity:.55;cursor:wait}
.primary{background:#2563eb}.capture{width:100%}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:13px}
.relay{background:#0f172a;border:1px solid #334155;border-radius:11px;padding:16px}
.state{font-weight:bold;font-size:17px;margin:12px 0}
.toggle{width:100%;background:#475569}.toggle.on{background:#15803d}
.timer{color:#cbd5e1;font-size:13px;min-height:18px}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:12px}
.photo{background:#0f172a;border-radius:9px;overflow:hidden}
.photo img{width:100%;aspect-ratio:1;object-fit:cover;display:block}
.photo-info{padding:9px;font-size:11px;overflow-wrap:anywhere}
a{color:#93c5fd}.download{display:block;margin-top:7px}
.message{overflow-wrap:anywhere;color:#4ade80}.error{color:#f87171}
.full{width:100%;background:#334155;margin-bottom:12px}
@media(max-width:500px){body{padding:9px}.card{padding:13px}h1{font-size:21px}}
</style>
</head>
<body>
<header>
<h1>📷 Raspberry Pi Control Center</h1>
<p class="muted">OV5647 camera · Photo gallery · Dual relay · Buzzer</p>
<div class="pills">
<div class="pill">Camera: <b id="cameraStatus" class="bad">CHECKING</b></div>
<div class="pill">Photos: <b id="photoCount">0</b></div>
<div class="pill">Buzzer: <b id="buzzerStatus">CHECKING</b></div>
</div>
</header>
<main>
<section class="card">
<h2>📹 Live camera</h2>
<img class="video" src="/video_feed" alt="Live camera feed">
<button class="primary capture" id="captureButton" onclick="capturePhoto()">📸 Capture 5MP photograph</button>
<p id="captureMessage" class="message"></p>
</section>

<section class="card">
<h2>⚡ Dual relay controller</h2>
<p class="muted">Each relay has an independent automatic OFF timer.</p>
<div class="grid">
<div class="relay">
<h3>Relay 1 · GPIO 23</h3>
<div id="status1" class="state bad">● OFF</div>
<div id="timer1" class="timer">Auto OFF limit: —</div>
<button id="relayBtn1" class="toggle" onclick="toggleRelay(1)">Turn ON</button>
</div>
<div class="relay">
<h3>Relay 2 · GPIO 24</h3>
<div id="status2" class="state bad">● OFF</div>
<div id="timer2" class="timer">Auto OFF limit: —</div>
<button id="relayBtn2" class="toggle" onclick="toggleRelay(2)">Turn ON</button>
</div>
</div>
<p class="muted">The auto-OFF countdown starts when a relay turns ON. Turning it ON again resets that relay's timer.</p>
</section>

<section class="card">
<h2>🖼 Captured photographs</h2>
<button class="full" onclick="loadImages()">↻ Refresh gallery</button>
<div class="gallery" id="gallery">Loading photographs…</div>
</section>
</main>

<script>
let relayStates={1:false,2:false};
let relayBusy={1:false,2:false};

async function capturePhoto(){
 const btn=document.getElementById('captureButton');
 const msg=document.getElementById('captureMessage');
 btn.disabled=true; msg.className='message'; msg.textContent='Capturing photograph…';
 try{
  const r=await fetch('/api/capture',{method:'POST'});
  const d=await r.json();
  if(!r.ok||!d.success) throw new Error(d.error||'Capture failed');
  msg.textContent='Saved: '+d.filename;
  await loadImages();
 }catch(e){msg.className='message error';msg.textContent='Capture error: '+e.message;}
 finally{btn.disabled=false;}
}

function updateRelayUI(ch,item){
 relayStates[ch]=item.on;
 const s=document.getElementById('status'+ch);
 const b=document.getElementById('relayBtn'+ch);
 const t=document.getElementById('timer'+ch);
 s.textContent=item.on?'● ON':'● OFF';
 s.className='state '+(item.on?'good':'bad');
 b.textContent=item.on?'Turn OFF':'Turn ON';
 b.classList.toggle('on',item.on);
 t.textContent=item.on
  ? 'Auto OFF in '+item.remaining_seconds+'s (limit '+item.max_on_seconds+'s)'
  : 'Auto OFF limit: '+item.max_on_seconds+'s';
}

async function updateStatus(){
 try{
  const r=await fetch('/api/status',{cache:'no-store'});
  if(!r.ok)throw new Error('Status unavailable');
  const d=await r.json();
  updateRelayUI(1,d.relay1); updateRelayUI(2,d.relay2);
  const c=document.getElementById('cameraStatus');
  c.textContent='ONLINE';c.className='good';
  document.getElementById('buzzerStatus').textContent=d.buzzer?'READY':'DISABLED';
 }catch(e){
  const c=document.getElementById('cameraStatus');
  c.textContent='OFFLINE';c.className='bad';
 }
}

async function toggleRelay(ch){
 if(relayBusy[ch])return;
 relayBusy[ch]=true;
 const b=document.getElementById('relayBtn'+ch);b.disabled=true;
 try{
  const r=await fetch('/api/relay/'+ch,{
   method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({on:!relayStates[ch]})
  });
  const d=await r.json();
  if(!r.ok||!d.success)throw new Error(d.error||'Relay operation failed');
  updateRelayUI(ch,d.state);
 }catch(e){alert('Relay error: '+e.message);await updateStatus();}
 finally{relayBusy[ch]=false;b.disabled=false;}
}

async function loadImages(){
 const box=document.getElementById('gallery');
 try{
  const r=await fetch('/api/images',{cache:'no-store'});
  if(!r.ok)throw new Error('Gallery unavailable');
  const images=await r.json();
  document.getElementById('photoCount').textContent=images.length;
  box.replaceChildren();
  if(!images.length){box.textContent='No photographs captured yet.';return;}
  for(const item of images){
   const card=document.createElement('div');card.className='photo';
   const link=document.createElement('a');link.href=item.url;link.target='_blank';link.rel='noopener';
   const img=document.createElement('img');img.src=item.url+'?v='+encodeURIComponent(item.modified);
   img.alt=item.name;img.loading='lazy';link.appendChild(img);
   const info=document.createElement('div');info.className='photo-info';
   const name=document.createElement('div');name.textContent=item.name;
   const dl=document.createElement('a');dl.className='download';dl.href=item.download_url;dl.textContent='⬇ Download';
   info.append(name,dl);card.append(link,info);box.appendChild(card);
  }
 }catch(e){box.textContent='Unable to load gallery: '+e.message;}
}

updateStatus();loadImages();
setInterval(updateStatus,1000);
setInterval(loadImages,10000);
</script>
</body>
</html>
"""


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route("/")
def home():
    return render_template_string(HTML_PAGE)


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_video(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"}
    )


@app.route("/api/capture", methods=["POST"])
def api_capture():
    result = capture_photo("web")
    return jsonify(result), (200 if result["success"] else 500)


@app.route("/api/status")
def api_status():
    return jsonify({
        **get_all_relay_status(),
        "camera": "online",
        "buzzer": buzzer is not None,
    })


@app.route("/api/relay/<int:channel>", methods=["POST"])
def api_relay(channel):
    if channel not in (1, 2):
        return jsonify({"success": False, "error": "Invalid relay channel"}), 400

    data = __import__("flask").request.get_json(silent=True) or {}
    state = data.get("on")
    if not isinstance(state, bool):
        return jsonify({"success": False, "error": "Expected JSON boolean field 'on'"}), 400

    try:
        status = set_relay(channel, state, reason="web")
        return jsonify({"success": True, "channel": channel, "state": status})
    except Exception as exc:
        log.exception("Relay command failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/images")
def api_images():
    images = []
    for path in sorted(
        PHOTO_DIR.glob("IMG_*.jpg"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    ):
        stat = path.stat()
        images.append({
            "name": path.name,
            "url": f"/images/{path.name}",
            "download_url": f"/download/{path.name}",
            "modified": int(stat.st_mtime),
            "size": stat.st_size,
        })
    return jsonify(images)


def valid_photo_name(filename):
    return (
        Path(filename).name == filename
        and filename.startswith("IMG_")
        and filename.lower().endswith(".jpg")
    )


@app.route("/images/<path:filename>")
def serve_image(filename):
    if not valid_photo_name(filename):
        abort(404)
    path = PHOTO_DIR / filename
    if not path.is_file():
        abort(404)
    return send_from_directory(PHOTO_DIR, filename, mimetype="image/jpeg")


@app.route("/download/<path:filename>")
def download_image(filename):
    if not valid_photo_name(filename):
        abort(404)
    path = PHOTO_DIR / filename
    if not path.is_file():
        abort(404)
    return send_from_directory(
        PHOTO_DIR, filename, as_attachment=True, mimetype="image/jpeg"
    )


# ============================================================
# CLEAN SHUTDOWN
# ============================================================

_cleanup_lock = threading.Lock()
_cleaned = False


def cleanup():
    global _cleaned
    with _cleanup_lock:
        if _cleaned:
            return
        _cleaned = True

    log.info("Shutting down camera and GPIO")
    shutdown_event.set()

    # Set relay outputs OFF before releasing GPIO.
    for channel in (1, 2):
        try:
            relays[channel].off()
        except Exception:
            log.exception("Could not switch relay %d OFF", channel)

    try:
        capture_button.close()
    except Exception:
        pass

    for relay in relays.values():
        try:
            relay.close()
        except Exception:
            pass

    if buzzer is not None:
        try:
            buzzer.off()
            buzzer.close()
        except Exception:
            pass

    try:
        with camera_lock:
            camera.stop()
            camera.close()
    except Exception:
        log.exception("Camera cleanup failed")

    log.info("Shutdown complete")


def handle_signal(signum, frame):
    cleanup()
    raise SystemExit(0)


atexit.register(cleanup)
signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    log.info("==========================================")
    log.info(" RASPBERRY PI CAMERA + RELAY + BUZZER")
    log.info("==========================================")
    log.info("Dashboard: http://0.0.0.0:%d", PORT)
    log.info("Photos: %s", PHOTO_DIR)
    log.info("Button: BCM GPIO%d", BUTTON_PIN)
    log.info("Relay 1: BCM GPIO%d, max ON %ds",
             RELAY_1_PIN, relay_limits[1])
    log.info("Relay 2: BCM GPIO%d, max ON %ds",
             RELAY_2_PIN, relay_limits[2])
    log.info("Buzzer: %s on BCM GPIO%d",
             "enabled" if buzzer is not None else "disabled", BUZZER_PIN)

    play_sound("startup")

    # Use one Flask process only; GPIO/camera must not be initialized twice.
    app.run(
        host=HOST,
        port=PORT,
        threaded=True,
        debug=False,
        use_reloader=False
    )
