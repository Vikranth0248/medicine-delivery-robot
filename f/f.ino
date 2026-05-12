/*
 * Medbot ESP32 Motor Controller
 *
 * FORWARD: Drives forward continuously until ultrasonic distance <= 20 cm
 *          (human is in front), then stops.
 *          → DC motor (L293D) runs slowly for DISP_MOTOR_TIME ms
 *          → Servo sweeps slowly anticlockwise then clockwise
 * RIGHT:   Turns right for a fixed duration, then stops.
 *
 * Pin map:
 *   Drive Motor driver (L298N):
 *     IN1/IN2 -> Left  motor direction
 *     IN3/IN4 -> Right motor direction
 *     ENA     -> Left  motor PWM enable
 *     ENB     -> Right motor PWM enable
 *
 *   Dispenser Motor driver (L293D):
 *     IN1     -> GPIO 13
 *     IN2     -> GPIO 12
 *
 *   Servo motor:
 *     Signal  -> GPIO 19
 *
 *   Ultrasonic (HC-SR04):
 *     TRIG    -> GPIO 5
 *     ECHO    -> GPIO 18
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ── Drive Motor pins (L298N) ─────────────────────────────────────────────────
#define IN1 25
#define IN2 26
#define IN3 27
#define IN4 14
#define ENA 33
#define ENB 32

// ── Dispenser DC Motor pins (L293D) ──────────────────────────────────────────
#define DISP_IN1 13
#define DISP_IN2 12

// ── Servo pin ────────────────────────────────────────────────────────────────
const int servoPin      = 19;
const int pwmFreq       = 50;
const int pwmResolution = 16;

// ── Ultrasonic pins ──────────────────────────────────────────────────────────
#define TRIG_PIN 5
#define ECHO_PIN 18

// ── Config ───────────────────────────────────────────────────────────────────
const char*  SSID            = "SSID";
const char*  PASSWORD        = "PASSWORD";
const char*  SERVER_URL      = "http://IP:5000/request_command";
const int    HTTP_TIMEOUT_MS = 3000;
const float  STOP_DISTANCE   = 20.0;   // cm — stop when human is this close
const int    MOTOR_SPEED     = 200;    // Drive motor PWM 0-255

// ── Dispenser motor speed config ─────────────────────────────────────────────
// Lower PWM = slower rotation. Range 0-255.
// 80-100 is a good slow speed; go lower if still too fast.
const int    DISP_MOTOR_SPEED = 90;    // ← tune this to change L293D speed
const int    DISP_MOTOR_TIME  = 1000;  // ← how long it runs in ms

// ── Servo sweep config ───────────────────────────────────────────────────────
// stepSize  : smaller = smoother & slower (try 20–50)
// stepDelay : ms between each step     (try 20–60 ms)
const int    SERVO_STEP_SIZE  = 30;    // ← tune this to change sweep smoothness
const int    SERVO_STEP_DELAY = 40;    // ← tune this to change sweep speed (ms)


// ── Ultrasonic helper ────────────────────────────────────────────────────────
float getDistanceCm() {
    digitalWrite(TRIG_PIN, LOW);
    delayMicroseconds(2);
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(10);
    digitalWrite(TRIG_PIN, LOW);

    long duration = pulseIn(ECHO_PIN, HIGH, 30000UL);
    if (duration == 0) return -1.0;
    return (duration * 0.0343f) / 2.0f;
}


// ── Drive motor helpers ──────────────────────────────────────────────────────
void stopMotors() {
    analogWrite(ENA, 0);
    analogWrite(ENB, 0);
    digitalWrite(IN1, LOW); digitalWrite(IN2, LOW);
    digitalWrite(IN3, LOW); digitalWrite(IN4, LOW);
    Serial.println(">>> DRIVE MOTORS: STOPPED");
}

void setForward() {
    digitalWrite(IN1, LOW); digitalWrite(IN2, HIGH);
    digitalWrite(IN3, LOW); digitalWrite(IN4, HIGH);
    analogWrite(ENA, MOTOR_SPEED);
    analogWrite(ENB, MOTOR_SPEED);
}


// ── Dispenser DC motor (L293D) — slow speed via PWM ─────────────────────────
// Uses ledcAttach on a separate channel to control L293D speed via ENA pin.
// DISP_IN1/IN2 set direction; PWM on ENA controls how fast it spins.
#define DISP_ENA      15          // ← Connect L293D ENA to GPIO 15 for speed control
                                  //   If ENA is hard-wired HIGH on your board, remove
                                  //   the analogWrite lines and it will run at full speed.
const int DISP_PWM_FREQ = 1000;   // 1 kHz PWM for DC motor speed control
const int DISP_PWM_RES  = 8;      // 8-bit resolution → 0-255

void runDispenserMotor() {
    Serial.printf(">>> DISPENSER MOTOR: Running slow (speed=%d) for %d ms\n",
                  DISP_MOTOR_SPEED, DISP_MOTOR_TIME);

    ledcAttach(DISP_ENA, DISP_PWM_FREQ, DISP_PWM_RES);   // attach ENA for PWM
    ledcWrite(DISP_ENA, DISP_MOTOR_SPEED);                // set speed
    digitalWrite(DISP_IN1, HIGH);
    digitalWrite(DISP_IN2, LOW);

    delay(DISP_MOTOR_TIME);

    // Stop
    ledcWrite(DISP_ENA, 0);
    digitalWrite(DISP_IN1, LOW);
    digitalWrite(DISP_IN2, LOW);
    Serial.println(">>> DISPENSER MOTOR: Stopped");
}


// ── Servo sweep helper ───────────────────────────────────────────────────────
// Gradually moves servo from fromPWM to toPWM in small steps for slow motion.
// SERVO_STEP_SIZE  → smaller steps = smoother
// SERVO_STEP_DELAY → more ms per step = slower
void sweepServo(int fromPWM, int toPWM) {
    int step = (toPWM > fromPWM) ? SERVO_STEP_SIZE : -SERVO_STEP_SIZE;
    for (int pwm = fromPWM;
         (step > 0) ? (pwm <= toPWM) : (pwm >= toPWM);
         pwm += step)
    {
        ledcWrite(servoPin, pwm);
        delay(SERVO_STEP_DELAY);
    }
    ledcWrite(servoPin, toPWM);   // ensure exact final position
}

// ── Servo sequence ───────────────────────────────────────────────────────────
void runServo() {
    Serial.println(">>> SERVO: Slowly sweeping anticlockwise (~25°)");
    sweepServo(7282, 2548);   // start at ~155° → sweep to ~25°
    delay(500);               // pause at end position

    Serial.println(">>> SERVO: Slowly sweeping clockwise (~155°)");
    sweepServo(2548, 7282);   // start at ~25°  → sweep to ~155°
    delay(500);
}


// ── Dispense sequence: L293D motor → Servo ───────────────────────────────────
void dispense() {
    Serial.println(">>> DISPENSE SEQUENCE STARTED");
    runDispenserMotor();   // Step 1: slow DC motor
    runServo();            // Step 2: slow servo sweep
    Serial.println(">>> DISPENSE SEQUENCE COMPLETE");
}


// ── Forward — no timeout, stops only when human detected ─────────────────────
void moveForwardUntilHuman() {
    Serial.println(">>> DRIVE MOTORS: FORWARD (ultrasonic guided, no timeout)");
    setForward();

    while (true) {
        float dist = getDistanceCm();

        if (dist < 0) {
            Serial.println("    Ultrasonic: no echo (open space)");
        } else {
            Serial.printf("    Distance: %.1f cm\n", dist);
            if (dist <= STOP_DISTANCE) {
                Serial.println("    Human detected! Stopping drive motors.");
                break;
            }
        }
        delay(100);
    }

    stopMotors();
    dispense();   // destination reached → dispense
}


// ── Turn right ────────────────────────────────────────────────────────────────
void turnRight(unsigned long durationMs) {
    Serial.println(">>> DRIVE MOTORS: RIGHT TURN");
    digitalWrite(IN1, HIGH); digitalWrite(IN2, LOW);
    digitalWrite(IN3, LOW);  digitalWrite(IN4, HIGH);
    analogWrite(ENA, MOTOR_SPEED);
    analogWrite(ENB, MOTOR_SPEED);
    delay(durationMs);
    stopMotors();
}


// ── Setup ────────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);

    // Drive motor pins
    pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
    pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);
    pinMode(ENA, OUTPUT); pinMode(ENB, OUTPUT);
    stopMotors();

    // Dispenser DC motor pins
    pinMode(DISP_IN1, OUTPUT);
    pinMode(DISP_IN2, OUTPUT);
    pinMode(DISP_ENA, OUTPUT);
    digitalWrite(DISP_IN1, LOW);
    digitalWrite(DISP_IN2, LOW);
    digitalWrite(DISP_ENA, LOW);

    // Servo PWM setup
    ledcAttach(servoPin, pwmFreq, pwmResolution);

    // Ultrasonic pins
    pinMode(TRIG_PIN, OUTPUT);
    pinMode(ECHO_PIN, INPUT);

    // Wi-Fi
    Serial.print("Connecting to Wi-Fi");
    WiFi.begin(SSID, PASSWORD);
    unsigned long wifiStart = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (millis() - wifiStart > 15000UL) {
            Serial.println("\nWi-Fi timeout! Rebooting...");
            ESP.restart();
        }
        delay(500);
        Serial.print(".");
    }
    Serial.print("\nConnected. IP: ");
    Serial.println(WiFi.localIP());
}


// ── Main loop ────────────────────────────────────────────────────────────────
void loop() {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("Wi-Fi lost, reconnecting...");
        WiFi.reconnect();
        delay(2000);
        return;
    }

    HTTPClient http;
    http.begin(SERVER_URL);
    http.setTimeout(HTTP_TIMEOUT_MS);

    int httpCode = http.GET();

    if (httpCode == HTTP_CODE_OK) {
        String payload = http.getString();
        http.end();

        StaticJsonDocument<256> doc;
        DeserializationError err = deserializeJson(doc, payload);

        if (err) {
            Serial.printf("JSON error: %s\n", err.c_str());
        } else {
            const char* nav = doc["nav"] | "";

            if (strlen(nav) > 0) {
                Serial.printf(">>> CMD received: %s\n", nav);

                if (strcmp(nav, "FORWARD") == 0) {
                    moveForwardUntilHuman();
                }
                else if (strcmp(nav, "RIGHT") == 0) {
                    turnRight(1200);
                }
            }
        }
    } else {
        http.end();
        Serial.printf("HTTP error: %d\n", httpCode);
    }

    delay(500);
}