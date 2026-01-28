// ESP32 Wind Tunnel Network Sensor
// Supports automatic discovery and remote configuration
// Required libraries: WiFi, AsyncTCP, ESPAsyncWebServer

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <HX711.h>
#include <ESPmDNS.h>

// Forward declarations
void setupHTTPAPI();
void sendAnnouncement();
void sendSensorData(float value);
void sendMultiSensorData(float lift, float drag, float temp);
float readSensor();
float readLiftSensor();
float readDragSensor();
float readTemperatureSensor();

// WiFi credentials - CHANGE THESE!
const char* ssid = "windtunnel";
const char* password = "windtunnel";

// ===== SENSOR CONFIGURATION =====
// Set to true for multi-value mode (lift, drag, temp)
// Set to false for single-value mode
const bool MULTI_VALUE_MODE = true;  // CHANGE THIS!

// Sensor configuration
String sensorID = "esp32_sensor_1";  // Unique ID for this sensor
String sensorType = "force_balance";    // Type description (temperature, pressure, force_balance, etc.)
const char* firmwareVersion = "1.1.0";

// Target configuration (where to send data)
String targetIP = "";          // Will be set via HTTP config
int targetPort = 5000;         // Will be set via HTTP config
int sensorRate = 1000;         // Milliseconds between readings
bool sendingData = false;      // Data transmission state

// Discovery configuration
const int discoveryPort = 5555;
unsigned long lastAnnouncement = 0;
const unsigned long announcementInterval = 3000;  // 3 seconds

// Sensor reading
unsigned long lastReading = 0;

WiFiUDP udpData;       // For sending sensor data
WiFiUDP udpDiscovery;  // For broadcasting announcements
AsyncWebServer server(80);
Preferences preferences;

// HX711 Load Cell Configuration
const int LOADCELL_LIFT_DOUT = 16;
const int LOADCELL_LIFT_SCK = 4;
const int LOADCELL_DRAG_DOUT = 17;
const int LOADCELL_DRAG_SCK = 5;
const int LOADCELL_TEMP_DOUT = 18;  // If using a third load cell
const int LOADCELL_TEMP_SCK = 19;

HX711 scaleLift;
HX711 scaleDrag;
HX711 scaleTemp;

// Calibration factors (adjust these for your load cells)
float calibrationLift = 1.0;
float calibrationDrag = 1.0;
float calibrationTemp = 1.0;

bool liftSensorConnected = false;
bool dragSensorConnected = false;
bool tempSensorConnected = false;

void setup() {
  Serial.begin(115200);
  
  // Load saved configuration
  preferences.begin("sensor-config", true);  // Read-only mode
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
  
  // Validate configuration - reset bad values
  bool configChanged = false;
  if (targetIP.length() > 0) {
    targetIP.trim();
    if (targetIP.length() == 0) {
      sendingData = false;  // Disable sending if IP is invalid
      configChanged = true;
    }
  }
  if (sendingData && (targetIP.length() == 0 || targetIP == ":")) {
    sendingData = false;  // Disable sending if no valid IP
    configChanged = true;
  }
  
  // Fix corrupted sensor ID
  if (sensorID == ":" || sensorID == ": " || sensorID.length() == 0) {
    sensorID = "esp32_sensor_" + WiFi.macAddress().substring(12);  // Use last 6 chars of MAC
    sensorID.replace(":", "");
    configChanged = true;
    Serial.println("WARNING: Reset corrupted sensor ID to: " + sensorID);
  }
  
  // Save corrected configuration
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
  
  // Connect to WiFi
  Serial.println("Connecting to WiFi...");
  WiFi.mode(WIFI_STA);  // Set to station mode
  WiFi.setHostname("ESP-HX711");  // Set network hostname
  WiFi.setSleep(false);  // Disable WiFi sleep for better connectivity
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
  Serial.print("Gateway: ");
  Serial.println(WiFi.gatewayIP());
  Serial.print("Subnet: ");
  Serial.println(WiFi.subnetMask());
  Serial.print("DNS: ");
  Serial.println(WiFi.dnsIP());
  Serial.print("Hostname: ");
  Serial.println(WiFi.getHostname());
  
  // Start mDNS (allows access via ESP-HX711.local)
  if (MDNS.begin("ESP-HX711")) {
    Serial.println("mDNS responder started: ESP-HX711.local");
    MDNS.addService("http", "tcp", 80);
  }
  Serial.print("RSSI: ");
  Serial.print(WiFi.RSSI());
  Serial.println(" dBm");
  
  // Small delay to ensure WiFi is fully ready
  delay(1000);
  
  // Start UDP for discovery announcements
  udpDiscovery.begin(discoveryPort);
  
  // Setup HTTP API endpoints
  setupHTTPAPI();
  
  // Start web server
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
  
  // Initialize HX711 load cells
  Serial.println("Initializing load cells...");
  
  scaleLift.begin(LOADCELL_LIFT_DOUT, LOADCELL_LIFT_SCK);
  if (scaleLift.wait_ready_timeout(1000)) {
    // Try to read a value to confirm sensor is really connected
    long reading = scaleLift.read();
    if (reading != 0 && reading != -1) {
      scaleLift.set_scale(calibrationLift);
      scaleLift.tare();
      liftSensorConnected = true;
      Serial.println("  Lift sensor: CONNECTED");
    } else {
      liftSensorConnected = false;
      Serial.println("  Lift sensor: NOT FOUND (no valid data) - using sine wave");
    }
  } else {
    liftSensorConnected = false;
    Serial.println("  Lift sensor: NOT FOUND (timeout) - using sine wave");
  }
  
  scaleDrag.begin(LOADCELL_DRAG_DOUT, LOADCELL_DRAG_SCK);
  if (scaleDrag.wait_ready_timeout(1000)) {
    long reading = scaleDrag.read();
    if (reading != 0 && reading != -1) {
      scaleDrag.set_scale(calibrationDrag);
      scaleDrag.tare();
      dragSensorConnected = true;
      Serial.println("  Drag sensor: CONNECTED");
    } else {
      dragSensorConnected = false;
      Serial.println("  Drag sensor: NOT FOUND (no valid data) - using sine wave");
    }
  } else {
    dragSensorConnected = false;
    Serial.println("  Drag sensor: NOT FOUND (timeout) - using sine wave");
  }
  
  scaleTemp.begin(LOADCELL_TEMP_DOUT, LOADCELL_TEMP_SCK);
  if (scaleTemp.wait_ready_timeout(1000)) {
    long reading = scaleTemp.read();
    if (reading != 0 && reading != -1) {
      scaleTemp.set_scale(calibrationTemp);
      scaleTemp.tare();
      tempSensorConnected = true;
      Serial.println("  Temp sensor: CONNECTED");
    } else {
      tempSensorConnected = false;
      Serial.println("  Temp sensor: NOT FOUND (no valid data) - using sine wave");
    }
  } else {
    tempSensorConnected = false;
    Serial.println("  Temp sensor: NOT FOUND (timeout) - using sine wave");
  }
  
  Serial.println("========================================");
  
  // Initialize your sensor hardware here
  // pinMode(SENSOR_PIN, INPUT);
  // ...
}

void loop() {
  // Broadcast discovery announcement
  if (millis() - lastAnnouncement > announcementInterval) {
    sendAnnouncement();
    lastAnnouncement = millis();
  }
  
  // Send sensor data if enabled and target configured
  if (sendingData && targetIP.length() > 0 && targetPort > 0 && millis() - lastReading > sensorRate) {
    // Validate IP address is not just whitespace
    String trimmedIP = targetIP;
    trimmedIP.trim();
    if (trimmedIP.length() > 0) {
      if (MULTI_VALUE_MODE) {
        // Multi-value mode: Send lift, drag, temperature
        float lift = readLiftSensor();
        float drag = readDragSensor(); 
        float temp = readTemperatureSensor();
        sendMultiSensorData(lift, drag, temp);
      } else {
        // Single value mode: Send one value
        float value = readSensor();
        sendSensorData(value);
      }
    }
    
    lastReading = millis();
  }
  
  delay(1);  // Minimal delay for high-speed sampling (up to 200Hz)
}

void setupHTTPAPI() {
  // GET /status - Get current configuration and status
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
  
  // POST /config - Configure target and parameters
    Serial.println("Received POST /config (no body handler)");
  
  server.on("/config", HTTP_POST, [](AsyncWebServerRequest *request) {}, NULL,
    [](AsyncWebServerRequest *request, uint8_t *data, size_t len, size_t index, size_t total) {
      // Parse JSON body
      String body = "";
      for (size_t i = 0; i < len; i++) {
        body += (char)data[i];
      }
      
      Serial.println("========================================");
      Serial.println("Configuration request received:");
      Serial.println("Raw JSON body:");
      Serial.println(body);
      Serial.println("========================================");
      
      // Store old values for comparison
      String oldIP = targetIP;
      String oldID = sensorID;
      int oldPort = targetPort;
      int oldRate = sensorRate;
      
      // Simple JSON parsing (handles whitespace)
      // Remove all spaces for easier parsing
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
      
      // Validate configuration
      bool configValid = true;
      String errorMsg = "";
      
      if (targetIP.length() == 0 || targetIP == ":") {
        errorMsg = "Invalid target IP";
        configValid = false;
        targetIP = oldIP;  // Restore old value
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
      // Only validate sensor_id if it was actually provided in the request
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
      
      // Save to preferences
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
  
  // POST /start - Start sending data
  server.on("/start", HTTP_POST, [](AsyncWebServerRequest *request) {
    // Validate before starting
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
  
  // POST /stop - Stop sending data
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

void sendAnnouncement() {
  String json = "{";
  json += "\"type\":\"announcement\",";
  json += "\"sensor_id\":\"" + sensorID + "\",";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"mac\":\"" + WiFi.macAddress() + "\",";
  json += "\"sensor_type\":\"" + sensorType + "\",";
  json += "\"firmware\":\"" + String(firmwareVersion) + "\",";
  
  // Announce sensor capabilities (for setup wizard)
  if (MULTI_VALUE_MODE) {
    json += "\"multi_value\":true,";
    json += "\"sensor_keys\":[\"lift\",\"drag\",\"temp\"]";
  } else {
    json += "\"multi_value\":false,";
    json += "\"sensor_keys\":[\"value\"]";
  }
  
  json += "}";
  
  // Try to send broadcast, but don't block if it fails
  if (udpDiscovery.beginPacket("255.255.255.255", discoveryPort)) {
    udpDiscovery.print(json);
    if (udpDiscovery.endPacket()) {
      Serial.println("Announcement sent");
    }
  }
}

void sendSensorData(float value) {
  // SINGLE VALUE FORMAT:
  String json = "{";
  json += "\"id\":\"" + sensorID + "\",";
  json += "\"value\":" + String(value, 2);
  json += "}";
  
  if (udpData.beginPacket(targetIP.c_str(), targetPort)) {
    udpData.print(json);
    udpData.endPacket();
    Serial.println("Sent: " + json + " to " + targetIP + ":" + String(targetPort));
  } else {
    Serial.println("Failed to send data - check target IP: " + targetIP);
  }
}

void sendMultiSensorData(float lift, float drag, float temp) {
  // MULTI-VALUE FORMAT (for multiple sensors on one ESP32):
  // This creates separate sensor IDs: "esp32_1_lift", "esp32_1_drag", "esp32_1_temp"
  String json = "{";
  json += "\"id\":\"" + sensorID + "\",";
  json += "\"values\":{";
  json += "\"lift\":" + String(lift, 2) + ",";
  json += "\"drag\":" + String(drag, 2) + ",";
  json += "\"temp\":" + String(temp, 2);
  json += "}}";
  
  if (udpData.beginPacket(targetIP.c_str(), targetPort)) {
    udpData.print(json);
    udpData.endPacket();
    Serial.println("Sent multi: " + json);
  } else {
    Serial.println("Failed to send data - check target IP: " + targetIP);
  }
}

float readSensor() {
  // For single value mode: slow sine wave (period ~20 seconds)
  float time = millis() / 1000.0;
  return 20.0 + 3.0 * sin(time * 0.314);  // 20°C ± 3°C
}

float readLiftSensor() {
  if (liftSensorConnected && scaleLift.wait_ready_timeout(200)) {
    return scaleLift.get_units(10);  // Average of 10 readings
  }
  
  // Fallback: slow sine wave (period ~15 seconds)
  float time = millis() / 1000.0;
  return 5.0 + 8.0 * sin(time * 0.419);  // 5N ± 8N
}

float readDragSensor() {
  if (dragSensorConnected && scaleDrag.wait_ready_timeout(200)) {
    return scaleDrag.get_units(10);  // Average of 10 readings
  }
  
  // Fallback: slow sine wave (period ~12 seconds, phase shifted)
  float time = millis() / 1000.0;
  return 4.0 + 3.0 * sin(time * 0.524 + 1.57);  // 4N ± 3N, 90° phase shift
}

float readTemperatureSensor() {
  if (tempSensorConnected && scaleTemp.wait_ready_timeout(200)) {
    return scaleTemp.get_units(10);  // Average of 10 readings
  }
  
  // Fallback: very slow sine wave (period ~30 seconds)
  float time = millis() / 1000.0;
  return 22.0 + 2.5 * sin(time * 0.209);  // 22°C ± 2.5°C
}
