// ESP32 Wind Tunnel Network Sensor - SDP811 Pressure Sensor
// Supports automatic discovery and remote configuration

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <SensirionI2CSdp.h>
#include <Wire.h>
#include <ESPmDNS.h>

// Forward declarations
void setupHTTPAPI();
void sendAnnouncement();
void sendSensorData(float value);
void sendMultiSensorData(float pressure, float temp, float flow);
float readPressure();
float readTemperature();
float readFlowRate();

// WiFi credentials - CHANGE THESE!
const char* ssid = "windtunnel";
const char* password = "windtunnel";

// ===== SENSOR CONFIGURATION =====
const bool MULTI_VALUE_MODE = true;  // Send pressure, temp, flow

// Sensor configuration
String sensorID = "esp32_sensor_1";
String sensorType = "pressure_sensor";
const char* firmwareVersion = "1.1.0";

// Target configuration
String targetIP = "";
int targetPort = 5000;
int sensorRate = 1000;
bool sendingData = false;

// Calibration coefficients (3rd degree polynomial: y = a*x^3 + b*x^2 + c*x + d)
float calibration_a = 0.0;  // x^3 coefficient
float calibration_b = 0.0;  // x^2 coefficient
float calibration_c = 1.0;  // x coefficient (default: pass-through)
float calibration_d = 0.0;  // constant offset

// Discovery configuration
const int discoveryPort = 5555;
unsigned long lastAnnouncement = 0;
const unsigned long announcementInterval = 3000;

// Sensor reading
unsigned long lastReading = 0;

// Averaging for noise reduction
float pressureSum = 0;
float tempSum = 0;
int sampleCount = 0;
unsigned long lastSensorRead = 0;
const int MIN_READ_INTERVAL = 1;  // Read sensor every 1ms (safe margin above 0.5ms)

WiFiUDP udpData;
WiFiUDP udpDiscovery;
AsyncWebServer server(80);
Preferences preferences;

// SDP811 Sensor
SensirionI2CSdp sdp;
bool sensorConnected = false;

void setup() {
  Serial.begin(115200);
  
  // Load saved configuration
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
  if (preferences.isKey("cal_a")) {
    calibration_a = preferences.getFloat("cal_a", 0.0);
  }
  if (preferences.isKey("cal_b")) {
    calibration_b = preferences.getFloat("cal_b", 0.0);
  }
  if (preferences.isKey("cal_c")) {
    calibration_c = preferences.getFloat("cal_c", 1.0);
  }
  if (preferences.isKey("cal_d")) {
    calibration_d = preferences.getFloat("cal_d", 0.0);
  }
  preferences.end();
  
  // Validate configuration
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
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("ESP-SDP811");  // Set network hostname
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
  Serial.print("Gateway: ");
  Serial.println(WiFi.gatewayIP());
  Serial.print("Subnet: ");
  Serial.println(WiFi.subnetMask());
  Serial.print("DNS: ");
  Serial.println(WiFi.dnsIP());
  Serial.print("Hostname: ");
  Serial.println(WiFi.getHostname());
  
  // Start mDNS (allows access via ESP-SDP811.local)
  if (MDNS.begin("ESP-SDP811")) {
    Serial.println("mDNS responder started: ESP-SDP811.local");
    MDNS.addService("http", "tcp", 80);
  }
  Serial.print("RSSI: ");
  Serial.print(WiFi.RSSI());
  Serial.println(" dBm");
  
  delay(1000);
  
  // Start UDP
  udpDiscovery.begin(discoveryPort);
  
  // Setup HTTP API
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
  
  // Initialize SDP811 sensor
  Serial.println("Initializing SDP811 sensor...");
  
  // Initialize I2C (SDA=16, SCL=5)
  Wire.begin(16, 5);
  Wire.setClock(400000);  // 400 kHz
  
  // Wait 25ms after power-up
  delay(25);
  
  // Initialize sensor
  sdp.begin(Wire, 0x26);
  sdp.stopContinuousMeasurement();
  delay(1);
  
  // Try to start measurement (with averaging for better accuracy)
  uint16_t error = sdp.startContinuousMeasurementWithDiffPressureTCompAndAveraging();
  if (!error) {
    delay(8);  // Wait for first measurement
    
    // Read raw values to check scale factor
    int16_t pressureRaw, tempRaw, scaleFactor;
    error = sdp.readMeasurementRaw(pressureRaw, tempRaw, scaleFactor);
    if (!error) {
      sensorConnected = true;
      Serial.println("  SDP811 sensor: CONNECTED");
      Serial.print("  Scale factor: ");
      Serial.println(scaleFactor);
      Serial.print("  First raw pressure: ");
      Serial.println(pressureRaw);
    } else {
      sensorConnected = false;
      Serial.println("  SDP811 sensor: NOT RESPONDING - using sine wave");
    }
  } else {
    sensorConnected = false;
    Serial.println("  SDP811 sensor: NOT FOUND - using sine wave");
  }
  
  Serial.println("========================================");
}

void loop() {
  // Broadcast discovery announcement
  if (millis() - lastAnnouncement > announcementInterval) {
    sendAnnouncement();
    lastAnnouncement = millis();
  }
  
  // Continuously read sensor and accumulate for averaging
  if (millis() - lastSensorRead >= MIN_READ_INTERVAL) {
    if (sensorConnected) {
      float pressure, temp;
      uint16_t error = sdp.readMeasurement(pressure, temp);
      if (!error) {
        pressureSum += pressure;
        tempSum += temp;
        sampleCount++;
      }
    }
    lastSensorRead = millis();
  }
  
  // Send averaged sensor data at configured rate
  if (sendingData && targetIP.length() > 0 && targetPort > 0 && millis() - lastReading >= sensorRate) {
    String trimmedIP = targetIP;
    trimmedIP.trim();
    if (trimmedIP.length() > 0) {
      if (MULTI_VALUE_MODE) {
        float pressure = readPressure();  // Returns averaged value
        float temp = readTemperature();   // Returns averaged value
        float flow = readFlowRate();
        sendMultiSensorData(pressure, temp, flow);
      } else {
        float value = readPressure();
        sendSensorData(value);
      }
      
      // Reset averaging accumulators
      pressureSum = 0;
      tempSum = 0;
      sampleCount = 0;
    }
    
    lastReading = millis();
  }
  
  delay(1);  // Minimal delay for high-speed sampling (up to 200Hz)
}

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
    json += "\"sending_data\":" + String(sendingData ? "true" : "false") + ",";
    json += "\"calibration\":{";
    json += "\"a\":" + String(calibration_a, 8) + ",";
    json += "\"b\":" + String(calibration_b, 8) + ",";
    json += "\"c\":" + String(calibration_c, 8) + ",";
    json += "\"d\":" + String(calibration_d, 8);
    json += "}";
    json += "}";
    request->send(200, "application/json", json);
  });
  
  server.on("/config", HTTP_POST, [](AsyncWebServerRequest *request) {
    Serial.println("Received POST /config (no body handler)");
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
      if (body.indexOf("cal_a") > 0) {
        int start = body.indexOf("\"cal_a\":") + 8;
        int end = body.indexOf(",", start);
        if (end == -1) end = body.indexOf("}", start);
        calibration_a = body.substring(start, end).toFloat();
      }
      if (body.indexOf("cal_b") > 0) {
        int start = body.indexOf("\"cal_b\":") + 8;
        int end = body.indexOf(",", start);
        if (end == -1) end = body.indexOf("}", start);
        calibration_b = body.substring(start, end).toFloat();
      }
      if (body.indexOf("cal_c") > 0) {
        int start = body.indexOf("\"cal_c\":") + 8;
        int end = body.indexOf(",", start);
        if (end == -1) end = body.indexOf("}", start);
        calibration_c = body.substring(start, end).toFloat();
      }
      if (body.indexOf("cal_d") > 0) {
        int start = body.indexOf("\"cal_d\":") + 8;
        int end = body.indexOf(",", start);
        if (end == -1) end = body.indexOf("}", start);
        calibration_d = body.substring(start, end).toFloat();
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
      preferences.putFloat("cal_a", calibration_a);
      preferences.putFloat("cal_b", calibration_b);
      preferences.putFloat("cal_c", calibration_c);
      preferences.putFloat("cal_d", calibration_d);
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
    json += "\"sensor_keys\":[\"pressure\",\"temp\",\"flow\"]";
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
    Serial.println("Sent: " + json + " to " + targetIP + ":" + String(targetPort));
  } else {
    Serial.println("Failed to send data - check target IP: " + targetIP);
  }
}

void sendMultiSensorData(float pressure, float temp, float flow) {
  String json = "{";
  json += "\"id\":\"" + sensorID + "\",";
  json += "\"values\":{";
  json += "\"pressure\":" + String(pressure, 2) + ",";
  json += "\"temp\":" + String(temp, 2) + ",";
  json += "\"flow\":" + String(flow, 2);
  json += "}}";
  
  if (udpData.beginPacket(targetIP.c_str(), targetPort)) {
    udpData.print(json);
    udpData.endPacket();
    Serial.println("Sent multi: " + json);
  } else {
    Serial.println("Failed to send data - check target IP: " + targetIP);
  }
}

float readPressure() {
  float raw_pressure = 0.0;
  
  if (sensorConnected && sampleCount > 0) {
    raw_pressure = pressureSum / sampleCount;  // Averaged pressure
  } else if (!sensorConnected) {
    // Fallback: sine wave (period ~20 seconds, 0-100 Pa range)
    float time = millis() / 1000.0;
    raw_pressure = 50.0 + 50.0 * sin(time * 0.314);
  }
  
  // Apply calibration polynomial: y = a*x^3 + b*x^2 + c*x + d
  float calibrated = calibration_a * raw_pressure * raw_pressure * raw_pressure +
                     calibration_b * raw_pressure * raw_pressure +
                     calibration_c * raw_pressure +
                     calibration_d;
  
  return calibrated;
}

float readTemperature() {
  if (sensorConnected && sampleCount > 0) {
    return tempSum / sampleCount;  // Return averaged temperature
  } else if (!sensorConnected) {
    // Fallback: sine wave
    float time = millis() / 1000.0;
    return 22.0 + 2.5 * sin(time * 0.209);
  }
  
  // No samples yet, return 0
  return 0.0;
}

float readFlowRate() {
  // Flow can be calculated from pressure if needed
  // For now, return a calculated/simulated value
  float pressure = readPressure();
  
  // Simple flow calculation (adjust based on your setup)
  // This is just a placeholder - real flow calculation depends on your system
  return pressure * 0.1;  // Arbitrary scaling factor
}
