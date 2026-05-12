import cv2
import threading
import queue
import time
import json
import os
import datetime
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATA_FILE = "chamber_data.json"

# --- GLOBAL STATE ---
state_lock        = threading.Lock()
pending_command   = ""
target_chamber_id = None
vision_active     = False

# Queue used to pass frames from the vision thread to the main thread for display.
# On Windows, cv2.imshow MUST be called from the main thread only.
# The vision thread puts frames here; the main thread calls imshow in a loop.
frame_queue = queue.Queue(maxsize=2)   # maxsize=2 prevents memory build-up


# ─────────────────────────────────────────────
#  Persistence helpers
# ─────────────────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print(f"[load_data ERROR] {e}")
    defaults = [
        {"id": i, "name": f"Medicine {i}", "units": 10,
         "dosages": ["", "", ""], "last_served": ""}
        for i in range(1, 9)
    ]
    with open(DATA_FILE, 'w') as f:
        json.dump(defaults, f, indent=4)
    return defaults


def save_data(data):
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[save_data ERROR] {e}")


# ─────────────────────────────────────────────
#  Vision worker  (background daemon thread)
#  - Does detection only, NO imshow/waitKey here
#  - Annotated frames are pushed to frame_queue
#    so the main thread can display them safely
# ─────────────────────────────────────────────
def vision_worker():
    global pending_command, vision_active

    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    )

    while True:
        with state_lock:
            active = vision_active
        if not active:
            time.sleep(0.2)
            continue

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[CV] ERROR: Cannot open camera. Retrying in 2s.")
            time.sleep(2)
            with state_lock:
                vision_active = False
            continue

        print("[CV] Scan started - 10 seconds.")
        face_found  = False
        deadline    = time.time() + 10.0
        frame_count = 0

        while time.time() < deadline:
            ret, frame = cap.read()
            if not ret:
                print("[CV] WARNING: Frame read failed.")
                break

            frame_count += 1
            gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(
                gray, scaleFactor=1.3, minNeighbors=5
            )

            remaining = max(0.0, deadline - time.time())

            # --- Annotate frame for display ---
            display_frame = frame.copy()
            if len(faces) > 0:
                face_found = True
                for (x, y, w, h) in faces:
                    cv2.rectangle(display_frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
                cv2.putText(display_frame, f"FACE DETECTED  {remaining:.1f}s",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            else:
                cv2.putText(display_frame, f"SCANNING...  {remaining:.1f}s",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            # Push annotated frame to main thread for display (non-blocking)
            try:
                frame_queue.put_nowait(display_frame)
            except queue.Full:
                pass   # Main thread is busy; drop this frame, no big deal

            if face_found:
                print(f"[CV] Face detected! frames={frame_count}, "
                      f"remaining={remaining:.1f}s")
                break

            if frame_count % 30 == 0:
                print(f"[CV] Searching... {remaining:.1f}s left | frames: {frame_count}")

        cap.release()

        # Signal main thread to close the window
        frame_queue.put(None)

        cmd = "FORWARD" if face_found else "RIGHT"
        print(f"[CV] Scan done. frames={frame_count}. Result: {cmd}")

        with state_lock:
            pending_command = cmd
            vision_active   = False


threading.Thread(target=vision_worker, daemon=True).start()


# ─────────────────────────────────────────────
#  Flask routes
# ─────────────────────────────────────────────
@app.route('/request_command')
def request_command():
    global pending_command, target_chamber_id, vision_active
    try:
        chambers = load_data()
        now = datetime.datetime.now().strftime("%H:%M")

        with state_lock:
            currently_active = vision_active
            current_cmd      = pending_command

        if not currently_active and current_cmd == "":
            for ch in chambers:
                if now in ch['dosages'] and ch['last_served'] != now:
                    print(f"[SCHEDULE] Dose due - chamber {ch['id']} at {now}")
                    with state_lock:
                        target_chamber_id = ch['id']
                        vision_active     = True
                    ch['last_served'] = now
                    save_data(chambers)
                    break

        with state_lock:
            cmd = pending_command
            cid = target_chamber_id
            if cmd in ("FORWARD", "RIGHT"):
                pending_command = ""
                if cmd == "RIGHT":
                    vision_active = True
                    print("[SERVER] RIGHT sent -> arming next scan.")
                else:
                    print(f"[SERVER] FORWARD sent -> chamber {cid}.")
                return jsonify({"nav": cmd, "chamber": cid})

        return jsonify({"nav": ""})

    except Exception as e:
        print(f"[request_command ERROR] {e}")
        return jsonify({"nav": "", "error": str(e)}), 500


@app.route('/')
def index():
    template_path = os.path.join(app.root_path, 'templates', 'index.html')
    flat_path     = os.path.join(app.root_path, 'index.html')
    if os.path.exists(template_path):
        return render_template('index.html')
    if os.path.exists(flat_path):
        os.makedirs(os.path.join(app.root_path, 'templates'), exist_ok=True)
        import shutil
        shutil.copy(flat_path, template_path)
        return render_template('index.html')
    chambers = load_data()
    rows = "".join(
        f"<tr><td>{c['id']}</td><td>{c['name']}</td>"
        f"<td>{c['units']}</td><td>{', '.join(filter(None, c['dosages']))}</td></tr>"
        for c in chambers
    )
    return f"""<!DOCTYPE html><html><head><title>Medbot</title>
<style>body{{font-family:sans-serif;padding:2rem}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:8px}}th{{background:#4a90d9;color:#fff}}</style>
</head><body><h1>Medbot Dashboard</h1>
<p style="background:#fff3cd;padding:1rem">templates/index.html not found.</p>
<table><tr><th>ID</th><th>Name</th><th>Units</th><th>Dosage Times</th></tr>
{rows}</table></body></html>"""


@app.route('/get_chambers')
def get_chambers():
    try:
        return jsonify(load_data())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/update_chamber', methods=['POST'])
def update_chamber():
    try:
        payload  = request.get_json(force=True)
        chambers = load_data()
        for ch in chambers:
            if ch['id'] == payload.get('id'):
                ch['name']    = payload.get('name',    ch['name'])
                ch['units']   = payload.get('units',   ch['units'])
                ch['dosages'] = payload.get('dosages', ch['dosages'])
                break
        save_data(chambers)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
#  Main thread display loop
#  Flask is run in a background thread so that
#  this (main) thread owns the cv2 window.
# ─────────────────────────────────────────────
def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)


if __name__ == '__main__':
    print("=" * 55)
    print(f"  Working dir : {os.getcwd()}")
    print(f"  Data file   : {os.path.abspath(DATA_FILE)}")
    tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'index.html')
    print(f"  Template    : {'EXISTS' if os.path.exists(tpl) else 'MISSING'}  ({tpl})")
    print("=" * 55)

    # Start Flask in a background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("[MAIN] Flask running on http://0.0.0.0:5000")
    print("[MAIN] Camera window will appear when a scan is triggered.")
    print("[MAIN] Press Ctrl+C to quit.\n")

    window_open = False

    # Main thread owns the cv2 window — safe on Windows
    while True:
        try:
            frame = frame_queue.get(timeout=0.05)   # Wait up to 50 ms for a frame
        except queue.Empty:
            if window_open:
                cv2.waitKey(1)   # Keep window responsive even with no new frames
            continue

        if frame is None:
            # Vision worker signals scan is done — close the window
            if window_open:
                cv2.destroyAllWindows()
                window_open = False
                print("[MAIN] Camera window closed.")
        else:
            cv2.imshow("Medbot Vision", frame)
            cv2.waitKey(1)
            window_open = True
