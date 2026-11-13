
import io
import time
import signal
import atexit
import threading
import logging

from pathlib import Path
from datetime import datetime

import cv2

from flask import (
    Flask,
    Response,
    jsonify,
    request,
    render_template_string,
    send_from_directory,
    abort
)

from picamera2 import Picamera2
from gpiozero import Button, OutputDevice


# =========================================================
# 1. APPLICATION CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
PHOTO_DIR = BASE_DIR / "photos"

PHOTO_DIR.mkdir(parents=True, exist_ok=True)

HOST = "0.0.0.0"
PORT = 5000

# GPIO BCM pin numbers
BUTTON_PIN = 17
RELAY_1_PIN = 23
RELAY_2_PIN = 24

# Most common relay boards are active LOW.
# True = active HIGH, False = active LOW.
RELAY_ACTIVE_HIGH = False

# Camera resolutions
PHOTO_SIZE = (2592, 1944)  # 5MP
VIDEO_SIZE = (640, 480)

# Stream settings
STREAM_INTERVAL = 0.15
JPEG_QUALITY = 70

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("PiCameraSystem")

# Prevent simultaneous camera operations
camera_lock = threading.Lock()

# Prevent simultaneous relay operations
relay_lock = threading.Lock()

# Shutdown protection
shutdown_lock = threading.Lock()
is_shutting_down = False


# =========================================================
# 2. RELAY INITIALIZATION
# =========================================================

logger.info("Initializing relay modules...")

relays = [
    OutputDevice(
        RELAY_1_PIN,
        active_high=RELAY_ACTIVE_HIGH,
        initial_value=False
    ),

    OutputDevice(
        RELAY_2_PIN,
        active_high=RELAY_ACTIVE_HIGH,
        initial_value=False
    )
]


def get_relay_status():

    with relay_lock:

        return {
            "relay1": relays[0].is_active,
            "relay2": relays[1].is_active
        }


def set_relay(channel, state):

    if channel not in (1, 2):
        raise ValueError("Invalid relay channel")

    if not isinstance(state, bool):
        raise ValueError("State must be boolean")

    if is_shutting_down:
        raise RuntimeError("Application is shutting down")

    with relay_lock:

        relay = relays[channel - 1]

        if state:
            relay.on()
        else:
            relay.off()

        return relay.is_active


# =========================================================
# 3. CAMERA INITIALIZATION
# =========================================================

logger.info("Initializing Raspberry Pi camera...")

camera = Picamera2()

# Low-resolution stream must use YUV420.
video_config = camera.create_video_configuration(

    main={
        "size": (1280, 960),
        "format": "RGB888"
    },

    lores={
        "size": VIDEO_SIZE,
        "format": "YUV420"
    },

    buffer_count=4,

    controls={
        "FrameRate": 10
    }
)

# Separate high-resolution still configuration.
# Picamera2 will encode photographs as JPEG.
still_config = camera.create_still_configuration(

    main={
        "size": PHOTO_SIZE,
        "format": "RGB888"
    },

    buffer_count=1
)

camera.configure(video_config)

camera.start()

time.sleep(2)

logger.info("Camera initialized successfully!")


# =========================================================
# 4. IMAGE CAPTURE FUNCTION
# =========================================================

def capture_photo(source="web"):

    if is_shutting_down:
        return {
            "success": False,
            "error": "Application is shutting down"
        }

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    filename = f"IMG_{timestamp}.jpg"

    filepath = PHOTO_DIR / filename

    try:

        logger.info("Capturing photograph from %s", source)

        with camera_lock:

            # Switch to full 5MP still mode.
            # Picamera2 restores the previous video mode afterwards.
            camera.switch_mode_and_capture_file(
                still_config,
                str(filepath),
                name="main"
            )

        if not filepath.exists() or filepath.stat().st_size == 0:
            raise RuntimeError("Image file was not created")

        logger.info("Photograph saved: %s", filepath)

        return {
            "success": True,
            "filename": filename,
            "url": f"/images/{filename}",
            "size": filepath.stat().st_size,
            "timestamp": timestamp
        }

    except Exception as error:

        logger.exception("Photograph capture failed")

        try:
            if filepath.exists():
                filepath.unlink()
        except OSError:
            pass

        return {
            "success": False,
            "error": str(error)
        }


# =========================================================
# 5. PHYSICAL BUTTON
# =========================================================

logger.info("Initializing physical capture button...")

capture_button = Button(
    BUTTON_PIN,
    pull_up=True,
    bounce_time=0.4
)


def physical_button_pressed():

    logger.info("Physical button pressed")

    result = capture_photo(source="physical_button")

    if result["success"]:

        logger.info(
            "Physical capture successful: %s",
            result["filename"]
        )

    else:

        logger.error(
            "Physical capture failed: %s",
            result["error"]
        )


capture_button.when_pressed = physical_button_pressed


# =========================================================
# 6. LIVE VIDEO STREAM
# =========================================================

def generate_video():

    while not is_shutting_down:

        try:

            # Capture YUV420 frame
            with camera_lock:

                frame = camera.capture_array("lores")

            # Convert YUV420 (I420) to BGR.
            # OpenCV JPEG encoder expects BGR.
            bgr_frame = cv2.cvtColor(
                frame,
                cv2.COLOR_YUV2BGR_I420
            )

            # Encode JPEG
            success, encoded = cv2.imencode(
                ".jpg",
                bgr_frame,
                [
                    cv2.IMWRITE_JPEG_QUALITY,
                    JPEG_QUALITY
                ]
            )

            if not success:
                continue

            jpeg_data = encoded.tobytes()

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Cache-Control: no-cache\r\n\r\n"
                + jpeg_data +
                b"\r\n"
            )

            time.sleep(STREAM_INTERVAL)

        except GeneratorExit:
            break

        except Exception:

            logger.exception("Live stream error")

            time.sleep(1)


# =========================================================
# 7. WEBPAGE
# =========================================================

HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Raspberry Pi Control Center</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 18px;
    background: #101827;
    color: #f8fafc;
    font-family: Arial, sans-serif;
}

header, main {
    max-width: 1100px;
    margin: auto;
}

header {
    margin-bottom: 22px;
}

h1 {
    font-size: 25px;
}

h2 {
    font-size: 20px;
}

.muted {
    color: #94a3b8;
}

.card {
    background: #1e293b;
    padding: 18px;
    margin-bottom: 20px;
    border-radius: 14px;
    border: 1px solid #334155;
}

.top-status {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
}

.pill {
    background: #0f172a;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 13px;
}

.online {
    color: #4ade80;
}

.offline {
    color: #f87171;
}

.video {
    width: 100%;
    max-width: 800px;
    display: block;
    margin: 15px auto;
    background: #000;
    border-radius: 10px;
}

.primary {
    background: #2563eb;
    color: white;
}

button {
    padding: 13px 18px;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: 15px;
}

button:disabled {
    opacity: 0.5;
    cursor: wait;
}

.capture {
    width: 100%;
    margin-top: 12px;
}

.relay-grid {
    display: grid;
    grid-template-columns: repeat(
        auto-fit,
        minmax(220px, 1fr)
    );
    gap: 15px;
}

.relay {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 20px;
}

.relay-status {
    font-size: 17px;
    font-weight: bold;
    margin: 15px 0;
}

.on {
    color: #4ade80;
}

.off {
    color: #f87171;
}

.toggle {
    width: 100%;
    background: #475569;
    color: white;
}

.toggle.enabled {
    background: #16a34a;
}

.gallery {
    display: grid;
    grid-template-columns: repeat(
        auto-fill,
        minmax(150px, 1fr)
    );
    gap: 13px;
}

.photo {
    background: #0f172a;
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid #334155;
}

.photo img {
    display: block;
    width: 100%;
    aspect-ratio: 1 / 1;
    object-fit: cover;
}

.photo-info {
    padding: 10px;
}

.photo-name {
    font-size: 11px;
    color: #cbd5e1;
    overflow-wrap: anywhere;
}

.download {
    display: block;
    color: #93c5fd;
    font-size: 13px;
    margin-top: 8px;
}

.message {
    color: #4ade80;
    overflow-wrap: anywhere;
}

.error {
    color: #f87171;
}

.refresh {
    width: 100%;
    background: #334155;
    color: white;
    margin-bottom: 15px;
}

@media(max-width: 500px) {

    body {
        padding: 10px;
    }

    .card {
        padding: 13px;
    }

    h1 {
        font-size: 21px;
    }

}

</style>

</head>


<body>

<header>

    <h1>📷 Raspberry Pi Control Center</h1>

    <p class="muted">
        Camera, image gallery and dual relay controller
    </p>

    <div class="top-status">

        <div class="pill">
            Camera:
            <strong id="cameraStatus" class="offline">
                Checking...
            </strong>
        </div>

        <div class="pill">
            Total Photos:
            <strong id="photoCount">0</strong>
        </div>

    </div>

</header>


<main>


<!-- LIVE CAMERA -->

<section class="card">

    <h2>📹 Live Camera</h2>

    <p class="muted">
        OV5647 camera live preview
    </p>

    <img
        id="videoFeed"
        class="video"
        src="/video_feed"
        alt="Camera stream"
    >

    <button
        class="primary capture"
        id="captureButton"
        onclick="capturePhoto()"
    >
        📸 Capture Photograph
    </button>

    <p id="captureMessage" class="message"></p>

</section>


<!-- DUAL RELAY -->

<section class="card">

    <h2>⚡ Dual Relay Controller</h2>

    <p class="muted">
        Independent relay control
    </p>

    <div class="relay-grid">

        <div class="relay">

            <h3>Relay 1</h3>

            <p class="muted">GPIO 23</p>

            <p class="relay-status off" id="status1">
                ● OFF
            </p>

            <button
                class="toggle"
                id="relayBtn1"
                onclick="toggleRelay(1)"
            >
                Turn ON
            </button>

        </div>


        <div class="relay">

            <h3>Relay 2</h3>

            <p class="muted">GPIO 24</p>

            <p class="relay-status off" id="status2">
                ● OFF
            </p>

            <button
                class="toggle"
                id="relayBtn2"
                onclick="toggleRelay(2)"
            >
                Turn ON
            </button>

        </div>

    </div>

</section>


<!-- IMAGE GALLERY -->

<section class="card">

    <h2>🖼 Captured Images</h2>

    <p class="muted">
        Photographs stored on Raspberry Pi
    </p>

    <button
        class="refresh"
        onclick="loadImages()"
    >
        🔄 Refresh Gallery
    </button>

    <div class="gallery" id="gallery">
        Loading photographs...
    </div>

</section>


</main>


<script>


// =====================================================
// APPLICATION STATE
// =====================================================

let relayStates = {
    1: false,
    2: false
};

let relayBusy = {
    1: false,
    2: false
};


// =====================================================
// CAPTURE PHOTOGRAPH
// =====================================================

async function capturePhoto() {

    const button =
        document.getElementById("captureButton");

    const message =
        document.getElementById("captureMessage");

    button.disabled = true;

    message.className = "message";
    message.textContent = "Capturing 5MP photograph...";

    try {

        const response = await fetch(
            "/api/capture",
            {
                method: "POST"
            }
        );

        const result = await response.json();

        if (!response.ok || !result.success) {

            throw new Error(
                result.error || "Capture failed"
            );

        }

        message.textContent =
            "Saved: " + result.filename;

        loadImages();

    } catch (error) {

        message.className = "message error";

        message.textContent =
            "Error: " + error.message;

    } finally {

        button.disabled = false;

    }

}


// =====================================================
// UPDATE RELAY DISPLAY
// =====================================================

function updateRelayUI(channel, state) {

    relayStates[channel] = state;

    const status =
        document.getElementById("status" + channel);

    const button =
        document.getElementById("relayBtn" + channel);

    if (state === true) {

        status.textContent = "● ON";
        status.className = "relay-status on";

        button.textContent = "Turn OFF";
        button.classList.add("enabled");

    } else {

        status.textContent = "● OFF";
        status.className = "relay-status off";

        button.textContent = "Turn ON";
        button.classList.remove("enabled");

    }

}


// =====================================================
// GET LIVE HARDWARE STATUS
// =====================================================

async function updateStatus() {

    try {

        const response = await fetch(
            "/api/status",
            { cache: "no-store" }
        );

        if (!response.ok) {
            throw new Error("Server unavailable");
        }

        const data = await response.json();

        updateRelayUI(1, data.relay1);
        updateRelayUI(2, data.relay2);

        const cameraStatus =
            document.getElementById("cameraStatus");

        cameraStatus.textContent = "ONLINE";
        cameraStatus.className = "online";

    } catch (error) {

        const cameraStatus =
            document.getElementById("cameraStatus");

        cameraStatus.textContent = "OFFLINE";
        cameraStatus.className = "offline";

    }

}


// =====================================================
// RELAY CONTROL
// =====================================================

async function toggleRelay(channel) {

    if (relayBusy[channel]) {
        return;
    }

    relayBusy[channel] = true;

    const button =
        document.getElementById("relayBtn" + channel);

    button.disabled = true;

    const desiredState =
        !relayStates[channel];

    try {

        const response = await fetch(
            "/api/relay/" + channel,
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    on: desiredState
                })
            }
        );

        const result = await response.json();

        if (!response.ok || !result.success) {

            throw new Error(
                result.error || "Relay operation failed"
            );

        }

        updateRelayUI(
            channel,
            result.state
        );

    } catch (error) {

        alert("Relay error: " + error.message);

        updateStatus();

    } finally {

        relayBusy[channel] = false;

        button.disabled = false;

    }

}


// =====================================================
// LOAD IMAGE GALLERY
// =====================================================

async function loadImages() {

    const gallery =
        document.getElementById("gallery");

    try {

        const response = await fetch(
            "/api/images",
            { cache: "no-store" }
        );

        if (!response.ok) {
            throw new Error("Gallery unavailable");
        }

        const images = await response.json();

        gallery.replaceChildren();

        document.getElementById(
            "photoCount"
        ).textContent = images.length;

        if (images.length === 0) {

            gallery.textContent =
                "No photographs captured yet.";

            return;

        }

        images.forEach(function(image) {

            const card =
                document.createElement("div");

            card.className = "photo";

            const link =
                document.createElement("a");

            link.href = image.url;
            link.target = "_blank";
            link.rel = "noopener";

            const img =
                document.createElement("img");

            img.src =
                image.url + "?t=" + Date.now();

            img.alt = image.name;
            img.loading = "lazy";

            const info =
                document.createElement("div");

            info.className = "photo-info";

            const name =
                document.createElement("div");

            name.className = "photo-name";
            name.textContent = image.name;

            const download =
                document.createElement("a");

            download.href = image.download_url;
            download.textContent = "⬇ Download";
            download.className = "download";

            info.appendChild(name);
            info.appendChild(download);

            link.appendChild(img);

            card.appendChild(link);
            card.appendChild(info);

            gallery.appendChild(card);

        });

    } catch (error) {

        gallery.textContent =
            "Unable to load gallery.";

    }

}


// =====================================================
// AUTOMATIC UPDATES
// =====================================================

updateStatus();
loadImages();

// Refresh relay states every 2 seconds
setInterval(updateStatus, 2000);

// Refresh image gallery every 10 seconds
setInterval(loadImages, 10000);


</script>

</body>

</html>
"""


# =========================================================
# 8. HOME PAGE
# =========================================================

@app.route("/")
def home():

    return render_template_string(HTML_PAGE)


# =========================================================
# 9. LIVE VIDEO ENDPOINT
# =========================================================

@app.route("/video_feed")
def video_feed():

    response = Response(
        generate_video(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )

    response.headers["Cache-Control"] = "no-store"

    return response


# =========================================================
# 10. CAPTURE PHOTO ENDPOINT
# =========================================================

@app.route("/api/capture", methods=["POST"])
def api_capture():

    result = capture_photo(source="web")

    status = 200 if result["success"] else 500

    return jsonify(result), status


# =========================================================
# 11. RELAY STATUS ENDPOINT
# =========================================================

@app.route("/api/status")
def api_status():

    return jsonify({
        "camera": "online",
        **get_relay_status()
    })


# =========================================================
# 12. RELAY CONTROL ENDPOINT
# =========================================================

@app.route("/api/relay/<int:channel>", methods=["POST"])
def api_relay(channel):

    if channel not in (1, 2):

        return jsonify({
            "success": False,
            "error": "Invalid relay channel"
        }), 400

    data = request.get_json(silent=True) or {}

    state = data.get("on")

    if not isinstance(state, bool):

        return jsonify({
            "success": False,
            "error": "Expected boolean ON/OFF value"
        }), 400

    try:

        actual_state = set_relay(channel, state)

        return jsonify({
            "success": True,
            "channel": channel,
            "state": actual_state
        })

    except Exception as error:

        logger.exception("Relay control error")

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# =========================================================
# 13. GET IMAGE GALLERY
# =========================================================

@app.route("/api/images")
def api_images():

    files = sorted(
        PHOTO_DIR.glob("IMG_*.jpg"),
        key=lambda file: file.stat().st_mtime,
        reverse=True
    )

    result = []

    for file in files:

        result.append({

            "name": file.name,

            "url": f"/images/{file.name}",

            "download_url": f"/download/{file.name}"

        })

    return jsonify(result)


# =========================================================
# 14. OPEN IMAGE
# =========================================================

@app.route("/images/<path:filename>")
def serve_image(filename):

    if (
        Path(filename).name != filename
        or not filename.startswith("IMG_")
        or not filename.lower().endswith(".jpg")
    ):
        abort(404)

    filepath = PHOTO_DIR / filename

    if not filepath.is_file():
        abort(404)

    response = send_from_directory(
        PHOTO_DIR,
        filename,
        mimetype="image/jpeg"
    )

    response.headers["Cache-Control"] = "no-store"

    return response


# =========================================================
# 15. DOWNLOAD IMAGE
# =========================================================

@app.route("/download/<path:filename>")
def download_image(filename):

    if (
        Path(filename).name != filename
        or not filename.startswith("IMG_")
        or not filename.lower().endswith(".jpg")
    ):
        abort(404)

    filepath = PHOTO_DIR / filename

    if not filepath.is_file():
        abort(404)

    return send_from_directory(
        PHOTO_DIR,
        filename,
        as_attachment=True,
        mimetype="image/jpeg"
    )


# =========================================================
# 16. APPLICATION SHUTDOWN
# =========================================================

def cleanup():

    global is_shutting_down

    with shutdown_lock:

        if is_shutting_down:
            return

        is_shutting_down = True

    logger.info("Starting hardware shutdown...")

    try:
        capture_button.close()
    except Exception:
        logger.exception("Button cleanup failed")

    # Turn both relays OFF
    for relay in relays:

        try:
            relay.off()
        except Exception:
            logger.exception("Relay OFF failed")

    for relay in relays:

        try:
            relay.close()
        except Exception:
            logger.exception("Relay cleanup failed")

    try:
        with camera_lock:
            camera.stop()
            camera.close()
    except Exception:
        logger.exception("Camera cleanup failed")

    logger.info("Hardware shutdown completed.")


def handle_exit(signum, frame):

    cleanup()

    raise SystemExit(0)


atexit.register(cleanup)

signal.signal(signal.SIGTERM, handle_exit)
signal.signal(signal.SIGINT, handle_exit)


# =========================================================
# 17. START APPLICATION
# =========================================================

if __name__ == "__main__":

    logger.info("======================================")
    logger.info(" RASPBERRY PI CAMERA CONTROL SYSTEM")
    logger.info("======================================")

    logger.info("Web server: http://0.0.0.0:%s", PORT)

    logger.info("Photo directory: %s", PHOTO_DIR)

    logger.info("Camera button: GPIO %s", BUTTON_PIN)

    logger.info("Relay 1: GPIO %s", RELAY_1_PIN)

    logger.info("Relay 2: GPIO %s", RELAY_2_PIN)

    logger.info("Camera resolution: %s", PHOTO_SIZE)

    logger.info("Video resolution: %s", VIDEO_SIZE)

    app.run(
        host=HOST,
        port=PORT,
        threaded=True,
        debug=False,
        use_reloader=False
    )
