
import io
import time
import atexit
import signal
import threading
from pathlib import Path
from datetime import datetime

from flask import (
    Flask, Response, jsonify, request,
    render_template_string, send_from_directory,
    abort
)

from picamera2 import Picamera2
from gpiozero import Button, OutputDevice
from PIL import Image


# =====================================================
# CONFIGURATION
# =====================================================

APP_HOST = "0.0.0.0"
APP_PORT = 5000

BASE_DIR = Path.home() / "camera_system"
PHOTO_DIR = BASE_DIR / "photos"

PHOTO_DIR.mkdir(parents=True, exist_ok=True)

# GPIO pins (BCM numbering)
BUTTON_PIN = 17
RELAY_1_PIN = 23
RELAY_2_PIN = 24

# Most 5V relay boards are active LOW.
# Change to True if your module is active HIGH.
RELAY_ACTIVE_HIGH = False

# Camera resolution
PHOTO_SIZE = (2592, 1944)
VIDEO_SIZE = (640, 480)

# Stream interval (seconds)
STREAM_INTERVAL = 0.15

capture_lock = threading.Lock()

app = Flask(__name__)


# =====================================================
# RELAY INITIALIZATION
# =====================================================

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


def relay_status():

    return {
        "relay1": relays[0].is_active,
        "relay2": relays[1].is_active
    }


# =====================================================
# CAMERA INITIALIZATION
# =====================================================

print("Initializing Raspberry Pi camera...")

camera = Picamera2()

camera_config = camera.create_video_configuration(
    main={
        "size": PHOTO_SIZE,
        "format": "RGB888"
    },
    lores={
        "size": VIDEO_SIZE,
        "format": "RGB888"
    },
    buffer_count=4,
    controls={
        "FrameRate": 10
    }
)

camera.configure(camera_config)
camera.start()

time.sleep(2)

print("Camera initialized successfully!")


# =====================================================
# IMAGE CAPTURE FUNCTION
# =====================================================

def capture_photo():

    try:

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S_%f"
        )

        filename = f"IMG_{timestamp}.jpg"

        filepath = PHOTO_DIR / filename

        print("Capturing photograph...")

        with capture_lock:

            frame = camera.capture_array("main")

            # Convert camera BGR byte ordering to RGB
            frame = frame[:, :, ::-1]

            image = Image.fromarray(frame)

            image.save(
                filepath,
                format="JPEG",
                quality=92
            )

        print("Image saved:", filepath)

        return {
            "success": True,
            "filename": filename,
            "url": f"/images/{filename}"
        }

    except Exception as error:

        print("Camera error:", error)

        return {
            "success": False,
            "error": str(error)
        }


# =====================================================
# PHYSICAL GPIO BUTTON
# =====================================================

capture_button = Button(
    BUTTON_PIN,
    pull_up=True,
    bounce_time=0.3
)


def physical_button_pressed():

    print("Physical capture button pressed!")

    result = capture_photo()

    if result["success"]:
        print("Physical capture completed.")


capture_button.when_pressed = physical_button_pressed


# =====================================================
# LIVE CAMERA STREAM
# =====================================================

def generate_video():

    while True:

        try:

            with capture_lock:

                frame = camera.capture_array("lores")

            # Convert BGR to RGB for Pillow
            frame = frame[:, :, ::-1]

            image = Image.fromarray(frame)

            buffer = io.BytesIO()

            image.save(
                buffer,
                format="JPEG",
                quality=65
            )

            jpeg = buffer.getvalue()

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + jpeg +
                b"\r\n"
            )

            time.sleep(STREAM_INTERVAL)

        except GeneratorExit:
            break

        except Exception as error:

            print("Video stream error:", error)

            time.sleep(1)


# =====================================================
# WEBPAGE
# =====================================================

HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Raspberry Pi Camera Control</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 20px;
    background: #101827;
    color: #f8fafc;
    font-family: Arial, sans-serif;
}

header {
    max-width: 1100px;
    margin: auto;
    margin-bottom: 25px;
}

header h1 {
    margin-bottom: 5px;
}

.muted {
    color: #94a3b8;
}

.container {
    max-width: 1100px;
    margin: auto;
}

.card {
    background: #1e293b;
    padding: 20px;
    margin-bottom: 20px;
    border-radius: 14px;
    border: 1px solid #334155;
}

.video {
    width: 100%;
    max-width: 800px;
    display: block;
    margin: auto;
    border-radius: 10px;
    background: #000;
}

.capture-btn {
    background: #2563eb;
    color: white;
    border: none;
    padding: 14px 25px;
    font-size: 16px;
    border-radius: 9px;
    cursor: pointer;
    margin-top: 15px;
}

.capture-btn:active {
    transform: scale(0.97);
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
    padding: 20px;
    border-radius: 12px;
    border: 1px solid #334155;
}

.status {
    font-weight: bold;
    margin: 12px 0;
}

.on {
    color: #4ade80;
}

.off {
    color: #f87171;
}

.toggle {
    border: none;
    padding: 12px 22px;
    border-radius: 8px;
    color: white;
    background: #475569;
    cursor: pointer;
    width: 100%;
    font-size: 15px;
}

.toggle.enabled {
    background: #16a34a;
}

.gallery {
    display: grid;
    grid-template-columns: repeat(
        auto-fill,
        minmax(160px, 1fr)
    );
    gap: 14px;
}

.photo {
    background: #0f172a;
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid #334155;
}

.photo img {
    width: 100%;
    height: 150px;
    object-fit: cover;
    display: block;
}

.photo p {
    padding: 8px;
    font-size: 11px;
    overflow-wrap: anywhere;
    color: #cbd5e1;
}

.top-status {
    display: flex;
    flex-wrap: wrap;
    gap: 15px;
}

.pill {
    background: #0f172a;
    padding: 10px 15px;
    border-radius: 8px;
    font-size: 13px;
}

a {
    color: #93c5fd;
}

</style>

</head>

<body>

<header>

    <h1>📷 Raspberry Pi Control Center</h1>

    <p class="muted">
        Camera monitoring, image storage and dual relay control
    </p>

    <div class="top-status">

        <div class="pill">
            Camera:
            <span id="cameraStatus" class="on">Connecting...</span>
        </div>

        <div class="pill">
            Total Photos:
            <span id="photoCount">0</span>
        </div>

    </div>

</header>


<div class="container">


    <!-- LIVE CAMERA -->

    <section class="card">

        <h2>Live Camera</h2>

        <p class="muted">
            Live camera preview
        </p>

        <img
            class="video"
            src="/video_feed"
            alt="Live Camera"
        >

        <button
            class="capture-btn"
            onclick="capturePhoto()"
        >
            📸 Capture Photograph
        </button>

        <p id="captureMessage" class="muted"></p>

    </section>


    <!-- RELAY CONTROLS -->

    <section class="card">

        <h2>Dual Relay Controller</h2>

        <p class="muted">
            Independent GPIO relay controls
        </p>

        <div class="relay-grid">


            <div class="relay">

                <h3>Relay 1</h3>

                <p class="muted">
                    GPIO 23
                </p>

                <p class="status" id="status1">
                    Checking...
                </p>

                <button
                    class="toggle"
                    id="relayBtn1"
                    onclick="toggleRelay(1)"
                >
                    Loading...
                </button>

            </div>


            <div class="relay">

                <h3>Relay 2</h3>

                <p class="muted">
                    GPIO 24
                </p>

                <p class="status" id="status2">
                    Checking...
                </p>

                <button
                    class="toggle"
                    id="relayBtn2"
                    onclick="toggleRelay(2)"
                >
                    Loading...
                </button>

            </div>

        </div>

    </section>


    <!-- IMAGE GALLERY -->

    <section class="card">

        <h2>Captured Images</h2>

        <p class="muted">
            All photographs saved on Raspberry Pi
        </p>

        <button
            class="toggle"
            onclick="loadImages()"
        >
            🔄 Refresh Gallery
        </button>

        <br><br>

        <div class="gallery" id="gallery">

            Loading images...

        </div>

    </section>


</div>


<script>

let relayStates = {
    1: false,
    2: false
};


// ----------------------------------
// CAPTURE IMAGE
// ----------------------------------

async function capturePhoto() {

    const message =
        document.getElementById("captureMessage");

    message.textContent = "Capturing image...";

    try {

        const response = await fetch("/api/capture", {
            method: "POST"
        });

        const result = await response.json();

        if (result.success) {

            message.textContent =
                "Image saved successfully: " +
                result.filename;

            loadImages();

        } else {

            message.textContent =
                "Capture failed: " + result.error;

        }

    } catch (error) {

        message.textContent =
            "Server connection error";

    }

}


// ----------------------------------
// RELAY STATUS
// ----------------------------------

function updateRelayUI(channel, state) {

    relayStates[channel] = state;

    const status =
        document.getElementById("status" + channel);

    const button =
        document.getElementById("relayBtn" + channel);

    if (state) {

        status.textContent = "● ON";
        status.className = "status on";

        button.textContent = "Turn OFF";
        button.classList.add("enabled");

    } else {

        status.textContent = "● OFF";
        status.className = "status off";

        button.textContent = "Turn ON";
        button.classList.remove("enabled");

    }

}


// ----------------------------------
// FETCH STATUS
// ----------------------------------

async function updateStatus() {

    try {

        const response =
            await fetch("/api/status");

        const data = await response.json();

        updateRelayUI(1, data.relay1);
        updateRelayUI(2, data.relay2);

        document.getElementById(
            "cameraStatus"
        ).textContent = "Online";

    } catch (error) {

        document.getElementById(
            "cameraStatus"
        ).textContent = "Offline";

    }

}


// ----------------------------------
// TOGGLE RELAY
// ----------------------------------

async function toggleRelay(channel) {

    const desiredState = !relayStates[channel];

    const button =
        document.getElementById("relayBtn" + channel);

    button.disabled = true;

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

        if (result.success) {

            updateRelayUI(channel, result.state);

        } else {

            alert(result.error || "Relay operation failed");

        }

    } catch (error) {

        alert("Unable to connect to Raspberry Pi");

    }

    button.disabled = false;

}


// ----------------------------------
// IMAGE GALLERY
// ----------------------------------

async function loadImages() {

    const gallery =
        document.getElementById("gallery");

    try {

        const response =
            await fetch("/api/images");

        const images = await response.json();

        gallery.replaceChildren();

        document.getElementById(
            "photoCount"
        ).textContent = images.length;

        if (images.length === 0) {

            gallery.textContent = "No photographs yet.";

            return;

        }

        images.forEach(function(image) {

            const card = document.createElement("div");
            card.className = "photo";

            const link = document.createElement("a");

            link.href = image.url;
            link.target = "_blank";

            const img = document.createElement("img");

            img.src = image.url;
            img.alt = image.name;
            img.loading = "lazy";

            const label = document.createElement("p");

            label.textContent = image.name;

            link.appendChild(img);
            card.appendChild(link);
            card.appendChild(label);

            gallery.appendChild(card);

        });

    } catch (error) {

        gallery.textContent =
            "Unable to load photographs.";

    }

}


// ----------------------------------
// AUTOMATIC UPDATES
// ----------------------------------

updateStatus();
loadImages();

setInterval(updateStatus, 2000);
setInterval(loadImages, 10000);

</script>

</body>

</html>
"""


# =====================================================
# FLASK ROUTES
# =====================================================

@app.route("/")
def home():

    return render_template_string(HTML_PAGE)


# ----------------------------------
# VIDEO STREAM
# ----------------------------------

@app.route("/video_feed")
def video_feed():

    return Response(
        generate_video(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


# ----------------------------------
# CAPTURE PHOTO FROM WEBPAGE
# ----------------------------------

@app.route("/api/capture", methods=["POST"])
def api_capture():

    result = capture_photo()

    status_code = 200 if result["success"] else 500

    return jsonify(result), status_code


# ----------------------------------
# GET RELAY STATUS
# ----------------------------------

@app.route("/api/status")
def api_status():

    return jsonify({
        "camera": "online",
        **relay_status()
    })


# ----------------------------------
# CHANGE RELAY STATE
# ----------------------------------

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
            "error": "A boolean 'on' value is required"
        }), 400

    relay = relays[channel - 1]

    if state:
        relay.on()
    else:
        relay.off()

    return jsonify({
        "success": True,
        "channel": channel,
        "state": relay.is_active
    })


# ----------------------------------
# GET ALL SAVED IMAGES
# ----------------------------------

@app.route("/api/images")
def api_images():

    files = sorted(
        PHOTO_DIR.glob("IMG_*.jpg"),
        key=lambda file: file.stat().st_mtime,
        reverse=True
    )

    return jsonify([
        {
            "name": file.name,
            "url": f"/images/{file.name}"
        }
        for file in files
    ])


# ----------------------------------
# OPEN IMAGE
# ----------------------------------

@app.route("/images/<path:filename>")
def serve_image(filename):

    if (
        Path(filename).name != filename
        or not filename.startswith("IMG_")
        or not filename.endswith(".jpg")
    ):
        abort(404)

    return send_from_directory(
        PHOTO_DIR,
        filename
    )


# =====================================================
# CLEANUP
# =====================================================

def cleanup():

    print("Shutting down hardware...")

    try:
        capture_button.close()
    except Exception:
        pass

    for relay in relays:

        try:
            relay.off()
            relay.close()
        except Exception:
            pass

    try:
        camera.stop()
        camera.close()
    except Exception:
        pass

    print("Hardware safely shut down.")


atexit.register(cleanup)


def handle_exit(signum, frame):

    cleanup()
    raise SystemExit(0)


signal.signal(signal.SIGTERM, handle_exit)
signal.signal(signal.SIGINT, handle_exit)


# =====================================================
# START WEB SERVER
# =====================================================

if __name__ == "__main__":

    print("-----------------------------------")
    print("RASPBERRY PI CAMERA CONTROL SYSTEM")
    print("-----------------------------------")

    print("Website: http://0.0.0.0:5000")
    print("Photo directory:", PHOTO_DIR)
    print("Camera button: GPIO17")
    print("Relay 1: GPIO23")
    print("Relay 2: GPIO24")

    app.run(
        host=APP_HOST,
        port=APP_PORT,
        threaded=True,
        debug=False,
        use_reloader=False
    )
