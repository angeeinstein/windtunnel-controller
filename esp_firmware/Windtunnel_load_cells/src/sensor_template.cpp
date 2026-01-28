// ESP32 Wind Tunnel Network Sensor - TEMPLATE
// Copy this file and customize for your specific sensor
// Follow the pattern from hx711_main.cpp or sdp811_main.cpp

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <ESPmDNS.h>
// Add your sensor library here
// #include <YourSensorLibrary.h>

// Forward declarations
void setupHTTPAPI();
void sendAnnouncement();
void sendSensorData(float value);
void sendMultiSensorData(float val1, float val2, float val3);
float readSensor1();
float readSensor2();
float readSensor3();

// ===== WIFI CONFIGURATION =====
const char* ssid = "YourWiFiSSID";       // CHANGE THIS!
const char* password = "YourPassword";    // CHANGE THIS!

// ===== SENSOR CONFIGURATION =====
const bool MULTI_VALUE_MODE = true;  // true = multiple values, false = single value

// Sensor info
String sensorID = "esp32_sensor_1";
String sensorType = "your_sensor_type";  // CHANGE THIS (e.g., "temperature", "pressure", etc.)
const char* firmwareVersion = "1.0.0";

// Target configuration (where to send data)
String targetIP = "";
int targetPort = 5000;
int sensorRate = 1000;  // milliseconds between readings
bool sendingData = false;

// Discovery configuration
const int discoveryPort = 5555;
unsigned long lastAnnouncement = 0;
const unsigned long announcementInterval = 3000;

// Sensor reading timing
unsigned long lastReading = 0;

// Network objects
WiFiUDP udpData;
WiFiUDP udpDiscovery;
AsyncWebServer server(80);
Preferences preferences;

// ===== YOUR SENSOR OBJECTS HERE =====
// Example: YourSensor sensor;
bool sensorConnected = false;

void setup() {
  Serial.begin(115200);
  
  // ===== LOAD CONFIGURATION FROM FLASH =====
  preferences.begin("sensor-config", true);
  if (preferences.isKey("sensor_id")) {
    sensorID = preferences.getString("sensor_id", sensorID);
  }
  if (preferences.isKey("target_ip")) {
    targetIP = preferences.getString("target_ip", "");
  }
  if (preferences.isKey("target_port")) {
    targetPort = preferences.getInt("target_port", 5000);
  }
  if (preferences.isKey("sensor_rate")) {
    sensorRate = preferences.getInt("sensor_rate", 1000);
  }
  if (preferences.isKey("sending_data")) {
    sendingData = preferences.getBool("sending_data", false);
  }
  preferences.end();
  
  // ===== VALIDATE CONFIGURATION =====
  bool configChanged = false;
  if (targetIP.length() > 0) {
    targetIP.trim();
    if (targetIP.length() == 0) {
      sendingData = false;
      configChanged = true;
    }
  }
  if (sendingData && (targetIP.length() == 0 || targetIP == ":")) {
    sendingData = false;
    configChanged = true;
  }
  
  // Fix corrupted sensor ID
  if (sensorID == ":" || sensorID == ": " || sensorID.length() == 0) {
    sensorID = "esp32_sensor_" + WiFi.macAddress().substring(12);
    sensorID.replace(":", "");
    configChanged = true;
    Serial.println("WARNING: Reset corrupted sensor ID to: " + sensorID);
  }
  
  if (configChanged) {
    preferences.begin("sensor-config", false);
    preferences.putBool("sending_data", sendingData);
    preferences.putString("sensor_id", sensorID);
    preferences.end();
  }
  
  Serial.println("Configuration loaded:");
  Serial.println("  Sensor ID: " + sensorID);
  Serial.println("  Target IP: " + (targetIP.length() > 0 ? targetIP : "NOT SET"));
  Serial.println("  Target Port: " + String(targetPort));
  Serial.println("  Sending Data: " + String(sendingData ? "YES" : "NO"));
  
  // ===== CONNECT TO WIFI =====
  Serial.println("Connecting to WiFi...");
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("ESP-YourSensor");  // TODO: Change to your sensor type (e.g., "ESP-BME280")
  WiFi.setSleep(false);
  WiFi.begin(ssid, password);
  
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  Serial.println("\nWiFi connected!");
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());
  Serial.print("MAC address: ");
  Serial.println(WiFi.macAddress());
  Serial.print("Hostname: ");
  Serial.println(WiFi.getHostname());
  
  // Start mDNS (allows access via ESP-YourSensor.local)
  if (MDNS.begin("ESP-YourSensor")) {  // TODO: Change to match hostname above
    Serial.println("mDNS responder started: ESP-YourSensor.local");
    MDNS.addService("http", "tcp", 80);
  }
  Serial.print("Gateway: ");
  Serial.println(WiFi.gatewayIP());
  Serial.print("Subnet: ");
  Serial.println(WiFi.subnetMask());
  Serial.print("RSSI: ");
  Serial.print(WiFi.RSSI());
  Serial.println(" dBm");
  
  delay(1000);
  
  // ===== START NETWORK SERVICES =====
  udpDiscovery.begin(discoveryPort);
  setupHTTPAPI();
  server.begin();
  
  Serial.println("========================================");
  Serial.println("HTTP server started");
  Serial.println("  Listening on: http://" + WiFi.localIP().toString() + ":80");
  Serial.println("  Endpoints:");
  Serial.println("    GET  /status");
  Serial.println("    POST /config");
  Serial.println("    POST /start");
  Serial.println("    POST /stop");
  Serial.println("========================================");
  
  // ===== INITIALIZE YOUR SENSOR HERE =====
  Serial.println("Initializing sensor...");
  
  // TODO: Add your sensor initialization code
  // Example:
  // sensor.begin();
  // if (sensor.isConnected()) {
  //   sensorConnected = true;
  //   Serial.println("  Sensor: CONNECTED");
  // } else {
  //   sensorConnected = false;
  //   Serial.println("  Sensor: NOT FOUND - using test data");
  // }
  
  sensorConnected = false;  // Change this based on your sensor detection
  Serial.println("  Sensor: NOT CONNECTED - using test data");
  
  Serial.println("========================================");
}

void loop() {
  // Broadcast discovery announcement
  if (millis() - lastAnnouncement > announcementInterval) {
    sendAnnouncement();
    lastAnnouncement = millis();
  }
  
  // Send sensor data
  if (sendingData && targetIP.length() > 0 && targetPort > 0 && millis() - lastReading > sensorRate) {
    String trimmedIP = targetIP;
    trimmedIP.trim();
    if (trimmedIP.length() > 0) {
      if (MULTI_VALUE_MODE) {
        float val1 = readSensor1();
        float val2 = readSensor2();
        float val3 = readSensor3();
        sendMultiSensorData(val1, val2, val3);
      } else {
        float value = readSensor1();
        sendSensorData(value);
      }
    }
    
    lastReading = millis();
  }
  
  delay(1);  // Minimal delay for high-speed sampling (up to 200Hz)
}

// ===== HTTP API SETUP =====
void setupHTTPAPI() {
  server.on("/status", HTTP_GET, [](AsyncWebServerRequest *request) {
    Serial.println("Received GET /status");
    String json = "{";
    json += "\"status\":\"success\",";
    json += "\"sensor_id\":\"" + sensorID + "\",";
    json += "\"sensor_type\":\"" + sensorType + "\",";
    json += "\"firmware\":\"" + String(firmwareVersion) + "\",";
    json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
    json += "\"mac\":\"" + WiFi.macAddress() + "\",";
    json += "\"target_ip\":\"" + targetIP + "\",";
    json += "\"target_port\":" + String(targetPort) + ",";
    json += "\"sensor_rate\":" + String(sensorRate) + ",";
    json += "\"sending_data\":" + String(sendingData ? "true" : "false");
    json += "}";
    request->send(200, "application/json", json);
  });
  
  server.on("/config", HTTP_POST, [](AsyncWebServerRequest *request) {
    Serial.println("Received POST /config");
  }, NULL,
    [](AsyncWebServerRequest *request, uint8_t *data, size_t len, size_t index, size_t total) {
      String body = "";
      for (size_t i = 0; i < len; i++) {
        body += (char)data[i];
      }
      
      Serial.println("========================================");
      Serial.println("Configuration request received:");
      Serial.println("Raw JSON body:");
      Serial.println(body);
      Serial.println("========================================");
      
      String oldIP = targetIP;
      String oldID = sensorID;
      int oldPort = targetPort;
      int oldRate = sensorRate;
      
      // Remove whitespace
      body.replace(" ", "");
      body.replace("\n", "");
      body.replace("\r", "");
      body.replace("\t", "");
      
      if (body.indexOf("target_ip") > 0) {
        int start = body.indexOf("\"target_ip\":\"") + 13;
        int end = body.indexOf("\"", start);
        if (end > start) {
          targetIP = body.substring(start, end);
          targetIP.trim();
        }
      }
      if (body.indexOf("target_port") > 0) {
        int start = body.indexOf("\"target_port\":") + 14;
        int end = body.indexOf(",", start);
        if (end == -1) end = body.indexOf("}", start);
        targetPort = body.substring(start, end).toInt();
      }
      if (body.indexOf("sensor_rate") > 0) {
        int start = body.indexOf("\"sensor_rate\":") + 14;
        int end = body.indexOf(",", start);
        if (end == -1) end = body.indexOf("}", start);
        sensorRate = body.substring(start, end).toInt();
      }
      if (body.indexOf("sensor_id") > 0) {
        int start = body.indexOf("\"sensor_id\":\"") + 13;
        int end = body.indexOf("\"", start);
        if (end > start) {
          sensorID = body.substring(start, end);
          sensorID.trim();
        }
      }
      
      bool configValid = true;
      String errorMsg = "";
      
      if (targetIP.length() == 0 || targetIP == ":") {
        errorMsg = "Invalid target IP";
        configValid = false;
        targetIP = oldIP;
      }
      if (targetPort <= 0 || targetPort > 65535) {
        errorMsg = "Invalid port (must be 1-65535)";
        configValid = false;
        targetPort = oldPort;
      }
      if (sensorRate < 5) {
        errorMsg = "Invalid rate (must be >= 5ms for 200Hz max)";
        configValid = false;
        sensorRate = oldRate;
      }
      if (body.indexOf("sensor_id") > 0 && (sensorID.length() == 0 || sensorID == ":" || sensorID == ": ")) {
        errorMsg = "Invalid sensor ID";
        configValid = false;
        sensorID = oldID;
      }
      
      if (!configValid) {
        Serial.println("CONFIGURATION REJECTED: " + errorMsg);
        request->send(400, "application/json", "{\"status\":\"error\",\"message\":\"" + errorMsg + "\"}");
        return;
      }
      
      preferences.begin("sensor-config", false);
      preferences.putString("sensor_id", sensorID);
      preferences.putString("target_ip", targetIP);
      preferences.putInt("target_port", targetPort);
      preferences.putInt("sensor_rate", sensorRate);
      preferences.end();
      
      Serial.println("CONFIGURATION ACCEPTED:");
      Serial.println("  Sensor ID:   " + sensorID + (sensorID != oldID ? " (changed)" : ""));
      Serial.println("  Target IP:   " + targetIP + (targetIP != oldIP ? " (changed)" : ""));
      Serial.println("  Target Port: " + String(targetPort) + (targetPort != oldPort ? " (changed)" : ""));
      Serial.println("  Sensor Rate: " + String(sensorRate) + "ms" + (sensorRate != oldRate ? " (changed)" : ""));
      Serial.println("========================================");
      
      request->send(200, "application/json", "{\"status\":\"success\",\"message\":\"Configuration updated\"}");
    });
  
  server.on("/start", HTTP_POST, [](AsyncWebServerRequest *request) {
    if (targetIP.length() == 0 || targetIP == ":") {
      Serial.println("Cannot start: No valid target IP configured");
      request->send(400, "application/json", "{\"status\":\"error\",\"message\":\"No valid target IP configured\"}");
      return;
    }
    
    sendingData = true;
    preferences.begin("sensor-config", false);
    preferences.putBool("sending_data", true);
    preferences.end();
    
    Serial.println("========================================");
    Serial.println("DATA TRANSMISSION STARTED");
    Serial.println("  Sending to: " + targetIP + ":" + String(targetPort));
    Serial.println("  Rate: " + String(sensorRate) + "ms");
    Serial.println("========================================");
    
    request->send(200, "application/json", "{\"status\":\"success\",\"message\":\"Data transmission started\"}");
  });
  
  server.on("/stop", HTTP_POST, [](AsyncWebServerRequest *request) {
    sendingData = false;
    preferences.begin("sensor-config", false);
    preferences.putBool("sending_data", false);
    preferences.end();
    
    Serial.println("========================================");
    Serial.println("DATA TRANSMISSION STOPPED");
    Serial.println("========================================");
    
    request->send(200, "application/json", "{\"status\":\"success\",\"message\":\"Data transmission stopped\"}");
  });
}

// ===== NETWORK FUNCTIONS =====
void sendAnnouncement() {
  String json = "{";
  json += "\"type\":\"announcement\",";
  json += "\"sensor_id\":\"" + sensorID + "\",";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"mac\":\"" + WiFi.macAddress() + "\",";
  json += "\"sensor_type\":\"" + sensorType + "\",";
  json += "\"firmware\":\"" + String(firmwareVersion) + "\",";
  
  if (MULTI_VALUE_MODE) {
    json += "\"multi_value\":true,";
    json += "\"sensor_keys\":[\"value1\",\"value2\",\"value3\"]";  // CHANGE THESE names
  } else {
    json += "\"multi_value\":false,";
    json += "\"sensor_keys\":[\"value\"]";
  }
  
  json += "}";
  
  if (udpDiscovery.beginPacket("255.255.255.255", discoveryPort)) {
    udpDiscovery.print(json);
    if (udpDiscovery.endPacket()) {
      Serial.println("Announcement sent");
    }
  }
}

void sendSensorData(float value) {
  String json = "{";
  json += "\"id\":\"" + sensorID + "\",";
  json += "\"value\":" + String(value, 2);
  json += "}";
  
  if (udpData.beginPacket(targetIP.c_str(), targetPort)) {
    udpData.print(json);
    udpData.endPacket();
    Serial.println("Sent: " + json);
  } else {
    Serial.println("Failed to send data - check target IP: " + targetIP);
  }
}

void sendMultiSensorData(float val1, float val2, float val3) {
  String json = "{";
  json += "\"id\":\"" + sensorID + "\",";
  json += "\"values\":{";
  json += "\"value1\":" + String(val1, 2) + ",";  // CHANGE "value1" to your sensor name
  json += "\"value2\":" + String(val2, 2) + ",";  // CHANGE "value2" to your sensor name
  json += "\"value3\":" + String(val3, 2);        // CHANGE "value3" to your sensor name
  json += "}}";
  
  if (udpData.beginPacket(targetIP.c_str(), targetPort)) {
    udpData.print(json);
    udpData.endPacket();
    Serial.println("Sent multi: " + json);
  } else {
    Serial.println("Failed to send data - check target IP: " + targetIP);
  }
}

// ===== SENSOR READING FUNCTIONS =====
// TODO: Customize these for your specific sensor

float readSensor1() {
  if (sensorConnected) {
    // TODO: Read from your actual sensor
    // return sensor.readValue1();
  }
  
  // Fallback: test sine wave
  float time = millis() / 1000.0;
  return 50.0 + 10.0 * sin(time * 0.5);
}

float readSensor2() {
  if (sensorConnected) {
    // TODO: Read from your actual sensor
    // return sensor.readValue2();
  }
  
  // Fallback: test sine wave
  float time = millis() / 1000.0;
  return 25.0 + 5.0 * sin(time * 0.3);
}

float readSensor3() {
  if (sensorConnected) {
    // TODO: Read from your actual sensor
    // return sensor.readValue3();
  }
  
  // Fallback: test sine wave
  float time = millis() / 1000.0;
  return 100.0 + 20.0 * sin(time * 0.7);
}
