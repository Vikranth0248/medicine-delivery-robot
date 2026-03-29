#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* ssid = "Airtel_rake_4261";
const char* password = "air52466";
const char* serverUrl = "http://192.168.1.60:5000/request_command"; // Use your Laptop IP

void setup() {
  Serial.begin(115200);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  Serial.println("System Ready");
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(serverUrl);
    int code = http.GET();

    if (code == 200) {
      StaticJsonDocument<256> doc;
      deserializeJson(doc, http.getString());
      
      const char* nav = doc["nav"];
      bool active = doc["active"];
      
      if (active) {
        Serial.print("NAV COMMAND: ");
        Serial.println(nav);
        
        if (strcmp(nav, "REACHED") == 0) {
          int chamber = doc["chamber"];
          Serial.print("--- ARRIVED! DISPENSING CHAMBER: ");
          Serial.println(chamber);
          // Add motor code here to dispense chamber
          delay(5000); 
        }
      }
    }
    http.end();
  }
  delay(500); // Poll every 0.5 seconds
}