import cv2
import threading
import time
import json
import os
import datetime
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATA_FILE = "chamber_data.json"
# Global state for Navigation & Vision
system_active = False
current_nav = "IDLE"
target_chamber_id = None

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f: return json.load(f)
    return [{"id": i, "name": f"Chamber {i}", "units": 20, "dosages": ["", "", ""], "last_served": ""} for i in range(1, 9)]

def save_data(data):
    with open(DATA_FILE, 'w') as f: json.dump(data, f)

chambers = load_data()

# --- COMPUTER VISION THREAD ---
def vision_worker():
    global current_nav, system_active
    # Load face detection (default Haar Cascade)
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    
    # Use 0 for laptop cam, or "http://IP:PORT/video" for DroidCam
    cap = cv2.VideoCapture(0) 

    while True:
        if system_active:
            ret, frame = cap.read()
            if not ret: continue
            
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.1, 4)

            if len(faces) > 0:
                (x, y, w, h) = faces[0]
                center_x = x + (w // 2)
                width = frame.shape[1]

                if center_x < width // 3: current_nav = "LEFT"
                elif center_x > 2 * width // 3: current_nav = "RIGHT"
                else: current_nav = "FORWARD"
                
                # If face is large (close to camera), signal to stop and dispense
                if w > (width // 2): current_nav = "REACHED"
            else:
                current_nav = "SEARCHING"
        else:
            current_nav = "IDLE"
        time.sleep(0.1)

threading.Thread(target=vision_worker, daemon=True).start()

# --- FLASK ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_chambers')
def get_chambers():
    return jsonify(chambers)

@app.route('/update_chamber', methods=['POST'])
def update_chamber():
    global chambers
    data = request.json
    for ch in chambers:
        if ch['id'] == data['id']:
            ch['name'] = data['name']; ch['units'] = data['units']; ch['dosages'] = data['dosages']
    save_data(chambers)
    return jsonify({"status": "success"})

@app.route('/request_command')
def request_command():
    """ ESP32 polls this every 500ms """
    global system_active, current_nav, target_chamber_id, chambers
    
    now = datetime.datetime.now().strftime("%H:%M")
    
    # Check if it's time to start a session
    for ch in chambers:
        if now in ch['dosages'] and ch['last_served'] != now:
            system_active = True
            target_chamber_id = ch['id']
            ch['last_served'] = now
            ch['units'] -= 1
            save_data(chambers)

    response = {
        "nav": current_nav,
        "active": system_active,
        "chamber": target_chamber_id if current_nav == "REACHED" else None
    }
    
    # If medicine was dispensed, reset system
    if current_nav == "REACHED":
        system_active = False
        target_chamber_id = None
        
    return jsonify(response)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)