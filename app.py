from flask import Flask, render_template, jsonify, Response, request
from flask_socketio import SocketIO, emit
import random
import time
import json
import os
import re
import csv
import sys
import sqlite3
import socket
import logging
import math
import threading
from datetime import datetime
from threading import Lock, Thread

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s [%(name)s] %(message)s'
)
logger = logging.getLogger('windtunnel')
sensor_logger = logging.getLogger('windtunnel.sensor')
hx711_logger = logging.getLogger('windtunnel.hx711')

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching in development

# Settings file path
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')

# Database configuration
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sensor_data.db')
DATA_RETENTION_HOURS = 24  # Keep last 24 hours of data
UPDATE_INTERVAL_MS = 200  # Fixed at 200ms (5Hz) for consistency

# Data logging directory (for CSV exports) - DISABLED, using database only
# DATA_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data_logs')
# if not os.path.exists(DATA_LOG_DIR):
#     os.makedirs(DATA_LOG_DIR)

# Log management configuration - DISABLED
# MAX_LOG_FILE_SIZE_MB = 50
# MAX_LOG_FILES = 100
# MAX_TOTAL_LOG_SIZE_MB = 2000

# Current log file (set when logging starts) - DISABLED
# current_log_file = None
# log_session_start = None
# log_rows_written = 0

# Database connection and lock
db_lock = Lock()
db_write_queue = []  # Buffer for batch writes

def init_database():
    """Initialize SQLite database with sensor data table and indexes."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Create table with composite primary key (timestamp, sensor_id)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sensor_data (
            timestamp REAL NOT NULL,
            sensor_id TEXT NOT NULL,
            value REAL NOT NULL,
            PRIMARY KEY (timestamp, sensor_id)
        )
    ''')
    
    # Create indexes for fast time-range queries
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_timestamp 
        ON sensor_data(timestamp)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_sensor_time 
        ON sensor_data(sensor_id, timestamp)
    ''')
    
    conn.commit()
    conn.close()
    print(f"Database initialized: {DB_FILE}")

def write_sensor_data_to_db(timestamp, sensor_data):
    """
    Write sensor data to database.
    Adds to write queue for batch processing.
    """
    with db_lock:
        for sensor_id, value in sensor_data.items():
            if sensor_id != 'timestamp':  # Skip timestamp field
                db_write_queue.append((timestamp, sensor_id, value))

def flush_db_write_queue():
    """
    Flush queued writes to database in a single transaction.
    Called periodically from background thread.
    """
    global db_write_queue
    
    with db_lock:
        if not db_write_queue:
            return
        
        queue_copy = db_write_queue[:]
        db_write_queue = []
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.executemany(
            'INSERT OR REPLACE INTO sensor_data (timestamp, sensor_id, value) VALUES (?, ?, ?)',
            queue_copy
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error writing to database: {e}")
        # Re-queue failed writes
        with db_lock:
            db_write_queue.extend(queue_copy)

def cleanup_old_data():
    """Remove sensor data older than configured retention period."""
    retention_hours = current_settings.get('dataRetentionHours', DATA_RETENTION_HOURS)
    cutoff_time = time.time() - (retention_hours * 3600)
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM sensor_data WHERE timestamp < ?', (cutoff_time,))
        deleted_rows = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted_rows > 0:
            print(f"Cleaned up {deleted_rows} old sensor data rows (retention: {retention_hours}h)")
    except Exception as e:
        print(f"Error cleaning up database: {e}")

# Initialize database on startup
init_database()

# Default settings
DEFAULT_SETTINGS = {
    'updateInterval': UPDATE_INTERVAL_MS,  # Fixed at 500ms
    'darkMode': False,
    'developerMode': False,
    'decimalPlaces': 2,
    'velocityUnit': 'ms',
    'temperatureUnit': 'c',
    'systemName': 'Wind Tunnel Alpha',
    'dataRetentionHours': 24,  # How long to keep sensor data in database
    'sensors': []
}

# Default sensor configurations (when no sensors are configured)
DEFAULT_SENSORS = [
    {'id': 'velocity', 'name': 'Velocity', 'type': 'mock', 'unit': 'm/s', 'color': '#e74c3c', 'enabled': True, 'config': {}},
    {'id': 'lift', 'name': 'Lift Force', 'type': 'mock', 'unit': 'N', 'color': '#e74c3c', 'enabled': True, 'config': {}},
    {'id': 'drag', 'name': 'Drag Force', 'type': 'mock', 'unit': 'N', 'color': '#e74c3c', 'enabled': True, 'config': {}},
    {'id': 'pressure', 'name': 'Pressure', 'type': 'mock', 'unit': 'kPa', 'color': '#3498db', 'enabled': True, 'config': {}},
    {'id': 'temperature', 'name': 'Temperature', 'type': 'mock', 'unit': '°C', 'color': '#3498db', 'enabled': True, 'config': {}},
    {'id': 'rpm', 'name': 'Fan RPM', 'type': 'mock', 'unit': 'RPM', 'color': '#3498db', 'enabled': True, 'config': {}},
    {'id': 'power', 'name': 'Power', 'type': 'mock', 'unit': 'W', 'color': '#3498db', 'enabled': True, 'config': {}},
    {'id': 'liftDragRatio', 'name': 'Lift/Drag Ratio', 'type': 'calculated', 'unit': '', 'color': '#27ae60', 'enabled': True, 'config': {'formula': 'lift/drag'}}
]

# Sensor type definitions with required configuration fields
SENSOR_TYPES = {
    'mock': {
        'name': 'Mock Data Generator',
        'fields': []
    },
    'calculated': {
        'name': 'Calculated Value',
        'fields': [
            {'name': 'formula', 'label': 'Formula (use sensor IDs)', 'type': 'text', 'placeholder': 'e.g., lift / drag'}
        ]
    },
    'force_balance_lift': {
        'name': 'Force Balance - Lift',
        'category': 'calculated',
        'description': 'Multi-sensor force balance with calibration for lift measurement',
        'fields': [
            {'name': 'source_sensor_1', 'label': 'Source Sensor 1', 'type': 'sensor_select', 'required': True, 'help': 'Any sensor type (HX711, UDP, calculated, etc.)'},
            {'name': 'source_sensor_2', 'label': 'Source Sensor 2', 'type': 'sensor_select', 'required': True, 'help': 'Any sensor type (HX711, UDP, calculated, etc.)'},
            {'name': 'source_sensor_3', 'label': 'Source Sensor 3', 'type': 'sensor_select', 'required': True, 'help': 'Any sensor type (HX711, UDP, calculated, etc.)'},
            {'name': 'formula', 'label': 'Geometric Formula', 'type': 'text', 'placeholder': 'e.g., (s1 * 0.254 + s2 * 0.254) / 1000', 'help': 'Use s1, s2, s3 for sensor readings (after tare). Include lever arms and unit conversions.'},
            {'name': 'calibration_info', 'label': 'Calibration Status', 'type': 'info', 'value': 'Not calibrated - use Calibrate button to tare and set calibration factor'}
        ]
    },
    'force_balance_drag': {
        'name': 'Force Balance - Drag',
        'category': 'calculated',
        'description': 'Multi-sensor force balance with calibration for drag measurement',
        'fields': [
            {'name': 'source_sensor_1', 'label': 'Source Sensor 1', 'type': 'sensor_select', 'required': True, 'help': 'Any sensor type (HX711, UDP, calculated, etc.)'},
            {'name': 'source_sensor_2', 'label': 'Source Sensor 2', 'type': 'sensor_select', 'required': True, 'help': 'Any sensor type (HX711, UDP, calculated, etc.)'},
            {'name': 'source_sensor_3', 'label': 'Source Sensor 3', 'type': 'sensor_select', 'required': True, 'help': 'Any sensor type (HX711, UDP, calculated, etc.)'},
            {'name': 'formula', 'label': 'Geometric Formula', 'type': 'text', 'placeholder': 'e.g., s3 * 0.180 / 1000', 'help': 'Use s1, s2, s3 for sensor readings (after tare). Include lever arms and unit conversions.'},
            {'name': 'calibration_info', 'label': 'Calibration Status', 'type': 'info', 'value': 'Not calibrated - use Calibrate button to tare and set calibration factor'}
        ]
    },
    'udp_network': {
        'name': 'UDP Network Sensor (ESP32/Network)',
        'category': 'network',
        'description': 'Receive sensor data over UDP from ESP32 or other network devices',
        'fields': [
            {'name': 'udp_port', 'label': 'UDP Port', 'type': 'number', 'default': 5000, 'min': 1024, 'max': 65535, 'help': 'Port to listen on for UDP packets. Multiple sensors can share the same port.'},
            {'name': 'sensor_id', 'label': 'Sensor ID (Must be unique!)', 'type': 'text', 'placeholder': 'e.g., esp32_temp_1', 'help': 'Each sensor must have a unique ID. Your device must send packets with this exact ID.'},
            {'name': 'timeout', 'label': 'Timeout (seconds)', 'type': 'number', 'default': 5, 'min': 1, 'max': 60, 'help': 'Mark as disconnected if no data received for this long'},
            {'name': 'packet_format', 'label': 'Packet Format', 'type': 'info', 'value': 'Single: {"id": "sensor_id", "value": 23.5} OR Multi: {"id": "base_id", "values": {"lift": 10.5, "drag": 2.3}}'},
            {'name': 'multi_value_info', 'label': 'Multi-Value Sensors', 'type': 'info', 'value': 'Multi-value packets create multiple sensor IDs: "base_id_lift", "base_id_drag", etc.'},
            {'name': 'discovery_info', 'label': 'Discovery', 'type': 'info', 'value': 'Tip: Visit /api/udp/devices to see all devices currently sending UDP data'}
        ]
    },
    'HX711': {
        'name': 'HX711 Load Cell Amplifier',
        'category': 'hardware',
        'description': 'For measuring force/weight with load cells',
        'fields': [
            {'name': 'dout_pin', 'label': 'DOUT Pin (GPIO #)', 'type': 'gpio_select', 'default': 5, 'pin_type': 'gpio'},
            {'name': 'pd_sck_pin', 'label': 'PD_SCK Pin (GPIO #)', 'type': 'gpio_select', 'default': 6, 'pin_type': 'gpio'},
            {'name': 'channel', 'label': 'Channel & Gain', 'type': 'select', 'options': ['A-128', 'A-64', 'B-32'], 'default': 'A-128'},
            {'name': 'reference_unit', 'label': 'Calibration Factor', 'type': 'number', 'default': 1, 'step': 0.1},
            {'name': 'offset', 'label': 'Zero Offset', 'type': 'number', 'default': 0}
        ]
    },
    'ADS1115': {
        'name': 'ADS1115 16-bit ADC',
        'category': 'hardware',
        'description': 'Precision analog-to-digital converter',
        'fields': [
            {'name': 'i2c_info', 'label': 'I2C Connection', 'type': 'info', 'value': 'SDA: GPIO2 (Pin 3) • SCL: GPIO3 (Pin 5)', 'description': 'I2C pins are shared between all I2C sensors'},
            {'name': 'address', 'label': 'I2C Address', 'type': 'select', 'options': ['0x48', '0x49', '0x4A', '0x4B'], 'default': '0x48'},
            {'name': 'channel', 'label': 'Channel', 'type': 'select', 'options': ['0', '1', '2', '3'], 'default': '0'},
            {'name': 'gain', 'label': 'Gain', 'type': 'select', 'options': ['2/3', '1', '2', '4', '8', '16'], 'default': '1', 
             'description': '2/3=±6.144V, 1=±4.096V, 2=±2.048V, 4=±1.024V, 8=±0.512V, 16=±0.256V'},
            {'name': 'data_rate', 'label': 'Sample Rate (SPS)', 'type': 'select', 'options': ['8', '16', '32', '64', '128', '250', '475', '860'], 'default': '128'}
        ]
    },
    'BMP280': {
        'name': 'BMP280 Pressure/Temperature',
        'category': 'hardware',
        'description': 'Barometric pressure and temperature sensor',
        'fields': [
            {'name': 'i2c_info', 'label': 'I2C Connection', 'type': 'info', 'value': 'SDA: GPIO2 (Pin 3) • SCL: GPIO3 (Pin 5)', 'description': 'I2C pins are shared between all I2C sensors'},
            {'name': 'address', 'label': 'I2C Address', 'type': 'select', 'options': ['0x76', '0x77'], 'default': '0x76'},
            {'name': 'sea_level_pressure', 'label': 'Sea Level Pressure (hPa)', 'type': 'number', 'default': 1013.25, 'step': 0.01}
        ]
    },
    'SDP811': {
        'name': 'Sensirion SDP811-500Pa',
        'category': 'hardware',
        'description': 'Differential pressure sensor for pitot tube airspeed',
        'fields': [
            {'name': 'i2c_info', 'label': 'I2C Connection', 'type': 'info', 'value': 'SDA: GPIO2 (Pin 3) • SCL: GPIO3 (Pin 5)', 'description': 'I2C pins are shared between all I2C sensors'},
            {'name': 'address', 'label': 'I2C Address', 'type': 'select', 'options': ['0x25', '0x26'], 'default': '0x25'},
            {'name': 'averaging', 'label': 'Averaging Mode', 'type': 'select', 
             'options': ['none', 'until_stable', 'update_2s'], 'default': 'until_stable',
             'description': 'Temperature compensation averaging'},
            {'name': 'altitude', 'label': 'Altitude (m)', 'type': 'number', 'default': 0,
             'description': 'For accurate air density calculation'}
        ]
    },
    'DHT22': {
        'name': 'DHT22 Temperature/Humidity',
        'category': 'hardware',
        'description': 'Digital temperature and humidity sensor',
        'fields': [
            {'name': 'pin', 'label': 'Data Pin (GPIO #)', 'type': 'gpio_select', 'default': 4, 'pin_type': 'gpio'}
        ]
    },
    'DS18B20': {
        'name': 'DS18B20 Temperature',
        'category': 'hardware',
        'description': 'High-precision digital temperature sensor',
        'fields': [
            {'name': 'address', 'label': 'Device Address', 'type': 'text', 'placeholder': 'Auto-detect or enter address',
             'description': 'Leave empty to auto-detect first sensor'}
        ]
    },
    'MCP3008': {
        'name': 'MCP3008 8-channel ADC',
        'category': 'hardware',
        'description': '10-bit analog-to-digital converter',
        'fields': [
            {'name': 'spi_info', 'label': 'SPI Bus Connection (Shared)', 'type': 'info', 'value': 'MISO: GPIO9 (Pin 21) • MOSI: GPIO10 (Pin 19) • SCLK: GPIO11 (Pin 23)', 'description': 'SPI bus pins are shared between all SPI sensors'},
            {'name': 'cs_pin', 'label': 'CS (Chip Select) Pin (GPIO #)', 'type': 'gpio_select', 'default': 8, 'pin_type': 'gpio', 'description': 'Each SPI sensor needs a unique CS pin'},
            {'name': 'channel', 'label': 'Channel', 'type': 'select', 'options': ['0', '1', '2', '3', '4', '5', '6', '7'], 'default': '0'},
            {'name': 'vref', 'label': 'Reference Voltage', 'type': 'number', 'default': 3.3, 'step': 0.1}
        ]
    },
    'MPU6050': {
        'name': 'MPU6050 Gyro/Accelerometer',
        'category': 'hardware',
        'description': '6-axis motion tracking sensor',
        'fields': [
            {'name': 'i2c_info', 'label': 'I2C Connection', 'type': 'info', 'value': 'SDA: GPIO2 (Pin 3) • SCL: GPIO3 (Pin 5)', 'description': 'I2C pins are shared between all I2C sensors'},
            {'name': 'address', 'label': 'I2C Address', 'type': 'select', 'options': ['0x68', '0x69'], 'default': '0x68'},
            {'name': 'output', 'label': 'Output Value', 'type': 'select', 
             'options': ['accel_x', 'accel_y', 'accel_z', 'gyro_x', 'gyro_y', 'gyro_z', 'temperature'], 
             'default': 'accel_x'}
        ]
    },
    'XGZP6847A': {
        'name': 'XGZP6847A Gauge Pressure',
        'category': 'hardware',
        'description': 'I2C gauge pressure sensor (measures relative to atmospheric pressure)',
        'fields': [
            {'name': 'i2c_info', 'label': 'I2C Connection', 'type': 'info', 'value': 'SDA: GPIO2 (Pin 3) • SCL: GPIO3 (Pin 5)', 'description': 'I2C pins are shared between all I2C sensors'},
            {'name': 'address', 'label': 'I2C Address', 'type': 'select', 'options': ['0x6D', '0x6C', '0x6E', '0x6F'], 'default': '0x6D'},
            {'name': 'pressure_range', 'label': 'Pressure Range', 'type': 'select', 
             'options': [
                 {'value': '1', 'label': '0-1 kPa'},
                 {'value': '2.5', 'label': '0-2.5 kPa'},
                 {'value': '5', 'label': '0-5 kPa'},
                 {'value': '10', 'label': '0-10 kPa'},
                 {'value': '20', 'label': '0-20 kPa'},
                 {'value': '40', 'label': '0-40 kPa'}
             ], 
             'default': '5',
             'description': 'Select your sensor variant range (check markings on sensor)'},
            {'name': 'output', 'label': 'Output Value', 'type': 'select',
             'options': ['pressure', 'temperature'],
             'default': 'pressure',
             'description': 'Pressure (Pa) or Temperature (°C)'},
            {'name': 'altitude', 'label': 'Altitude (m)', 'type': 'number', 'default': 0, 'step': 1,
             'description': 'For reference only (not used in calculation)'}
        ]
    },
    'BME280': {
        'name': 'BME280 Pressure/Temp/Humidity',
        'category': 'hardware',
        'description': 'Environmental sensor with pressure, temperature, and humidity',
        'fields': [
            {'name': 'i2c_info', 'label': 'I2C Connection', 'type': 'info', 'value': 'SDA: GPIO2 (Pin 3) • SCL: GPIO3 (Pin 5)', 'description': 'I2C pins are shared between all I2C sensors'},
            {'name': 'address', 'label': 'I2C Address', 'type': 'select', 'options': ['0x76', '0x77'], 'default': '0x77'},
            {'name': 'output', 'label': 'Output Value', 'type': 'select',
             'options': ['pressure', 'temperature', 'humidity', 'altitude'],
             'default': 'pressure'},
            {'name': 'sea_level_pressure', 'label': 'Sea Level Pressure (hPa)', 'type': 'number', 'default': 1013.25, 'step': 0.01}
        ]
    },
    'INA219': {
        'name': 'INA219 Current/Voltage/Power',
        'category': 'hardware',
        'description': 'High-side current sensor for motor power measurement',
        'fields': [
            {'name': 'i2c_info', 'label': 'I2C Connection', 'type': 'info', 'value': 'SDA: GPIO2 (Pin 3) • SCL: GPIO3 (Pin 5)', 'description': 'I2C pins are shared between all I2C sensors'},
            {'name': 'address', 'label': 'I2C Address', 'type': 'select', 
             'options': ['0x40', '0x41', '0x44', '0x45'], 'default': '0x40'},
            {'name': 'output', 'label': 'Output Value', 'type': 'select',
             'options': ['current', 'voltage', 'power'],
             'default': 'current',
             'description': 'Current (mA), Bus Voltage (V), or Power (mW)'}
        ]
    },
    'VL53L0X': {
        'name': 'VL53L0X Time-of-Flight Distance',
        'category': 'hardware',
        'description': 'Laser ranging sensor for precise distance measurement',
        'fields': [
            {'name': 'i2c_info', 'label': 'I2C Connection', 'type': 'info', 'value': 'SDA: GPIO2 (Pin 3) • SCL: GPIO3 (Pin 5)', 'description': 'I2C pins are shared between all I2C sensors'},
            {'name': 'address', 'label': 'I2C Address', 'type': 'select', 'options': ['0x29'], 'default': '0x29'},
            {'name': 'mode', 'label': 'Ranging Mode', 'type': 'select',
             'options': ['better_accuracy', 'long_range', 'high_speed'],
             'default': 'better_accuracy'}
        ]
    }
}

# Sensor initialization cache and handlers
sensor_instances = {}  # Cache initialized sensors {sensor_id: instance}
sensor_last_values = {}  # Cache last reading from each sensor {sensor_id: value}

# UDP sensor data storage
udp_sensor_data = {}  # {sensor_id: {'value': float, 'timestamp': float, 'port': int, 'source_ip': str}}
udp_listeners = {}  # {port: thread} - Track active UDP data listener threads

# UDP discovery system
UDP_DISCOVERY_PORT = 5555  # Dedicated port for ESP32 announcements
discovered_devices = {}  # {device_id: {'sensor_id', 'ip', 'mac', 'sensor_type', 'firmware', 'last_seen'}}
deleted_udp_sensors = set()  # Track manually deleted UDP sensor IDs to prevent auto-recreation
discovery_listener_thread = None
discovery_lock = Lock()

# Fan control state
fan_state = {
    'running': False,
    'speed': 0,  # 0-100%
    'pwm_pin': 12,  # GPIO12 (matches user's working configuration)
    'pwm_instance': None,
    'last_heartbeat': None  # Track last client heartbeat
}

# Global PWM device to prevent garbage collection
_pwm_device = None

# Safety timeout configuration (in seconds)
FAN_SAFETY_TIMEOUT = 0  # Disabled - set to positive value to enable auto-stop

# PID Controller for airspeed control
class PIDController:
    """
    PID Controller with anti-windup for airspeed control.
    """
    def __init__(self, kp=5.0, ki=0.5, kd=0.1, min_output=15.0, max_output=100.0):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.min_output = min_output  # Minimum fan speed (%)
        self.max_output = max_output  # Maximum fan speed (%)
        
        self.setpoint = 0.0
        self.last_error = 0.0
        self.integral = 0.0
        self.last_time = None
        
    def reset(self):
        """Reset controller state"""
        self.last_error = 0.0
        self.integral = 0.0
        self.last_time = None
        
    def update(self, current_value, dt=None):
        """
        Calculate PID output.
        
        Args:
            current_value: Current measured value (airspeed)
            dt: Time delta (seconds). If None, uses time since last call.
            
        Returns:
            Control output (fan speed %)
        """
        current_time = time.time()
        
        if dt is None:
            if self.last_time is not None:
                dt = current_time - self.last_time
            else:
                dt = 0.1  # Default 100ms
        
        self.last_time = current_time
        
        # Calculate error
        error = self.setpoint - current_value
        
        # Proportional term
        p_term = self.kp * error
        
        # Integral term with anti-windup
        self.integral += error * dt
        # Clamp integral to prevent windup
        max_integral = (self.max_output - self.min_output) / (self.ki if self.ki != 0 else 1.0)
        self.integral = max(-max_integral, min(max_integral, self.integral))
        i_term = self.ki * self.integral
        
        # Derivative term
        if dt > 0:
            derivative = (error - self.last_error) / dt
        else:
            derivative = 0.0
        d_term = self.kd * derivative
        
        self.last_error = error
        
        # Calculate output
        output = p_term + i_term + d_term
        
        # Clamp output to valid range
        output = max(self.min_output, min(self.max_output, output))
        
        return output

# PID control state
pid_state = {
    'enabled': False,
    'controller': None,
    'target_airspeed': 0.0,  # m/s
    'current_airspeed': 0.0,
    'control_output': 0.0,  # Fan speed %
    'airspeed_sensor_id': None,  # Which sensor to use for feedback
    'min_fan_speed': 15.0,  # Minimum fan speed %
    'thread': None,
    'stop_event': None,
    'auto_tuning': False,
    'auto_tune_cycles': 0,
    'auto_tune_kp': None,
    'auto_tune_ki': None,
    'auto_tune_kd': None
}

available_sensor_libraries = {}  # Track which libraries are installed
import importlib
import math

# Function to check which sensor libraries are available
def check_sensor_library_availability():
    """Check which sensor hardware libraries are installed and working."""
    global available_sensor_libraries
    
    library_checks = {
        'HX711': 'hx711',
        'ADS1115': 'adafruit_ads1x15.ads1115',
        'BMP280': 'adafruit_bmp280',
        'SDP811': 'sensirion_i2c_sdp',
        'DHT22': 'adafruit_dht',
        'DS18B20': 'w1thermsensor',
        'MCP3008': 'adafruit_mcp3xxx.mcp3008',
        'MPU6050': 'adafruit_mpu6050',
        'XGZP6847A': 'smbus2',  # Uses generic I2C library
        'BME280': 'adafruit_bme280',
        'INA219': 'adafruit_ina219',
        'VL53L0X': 'adafruit_vl53l0x'
    }
    
    for sensor_type, module_name in library_checks.items():
        try:
            # Invalidate import cache to force fresh check
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
            else:
                importlib.import_module(module_name)
            available_sensor_libraries[sensor_type] = True
            print(f"✓ {sensor_type} library available")
        except (ImportError, ModuleNotFoundError) as e:
            available_sensor_libraries[sensor_type] = False
            print(f"✗ {sensor_type} library not available (not installed)")
        except RuntimeError as e:
            # Library is installed but can't run (e.g., RPi.GPIO on non-Pi)
            if "Raspberry Pi" in str(e) or "GPIO" in str(e):
                available_sensor_libraries[sensor_type] = False
                print(f"⚠ {sensor_type} library installed but requires Raspberry Pi hardware")
            else:
                available_sensor_libraries[sensor_type] = False
                print(f"✗ {sensor_type} library error: {e}")
        except Exception as e:
            # Catch any other import errors
            available_sensor_libraries[sensor_type] = False
            print(f"✗ {sensor_type} library error: {e}")
    
    print(f"Library check complete: {sum(available_sensor_libraries.values())}/{len(available_sensor_libraries)} available")
    return available_sensor_libraries

# Check sensor library availability at startup
check_sensor_library_availability()

# Sensor handler functions
def init_hx711(config):
    """Initialize HX711 load cell amplifier using lgpio directly"""
    try:
        hx711_logger.info("=" * 60)
        hx711_logger.info("HX711 INITIALIZATION START")
        hx711_logger.info("=" * 60)
        
        import lgpio
        import time
        import os
        import glob
        
        dout = int(config.get('dout_pin', 5))
        sck = int(config.get('pd_sck_pin', 6))
        
        hx711_logger.info(f"Configuration: DOUT=GPIO{dout}, SCK=GPIO{sck}")
        hx711_logger.info(f"Physical pins: DOUT=Pin{dout_to_physical(dout)}, SCK=Pin{sck_to_physical(sck)}")
        hx711_logger.info("Using lgpio backend for Raspberry Pi 5")
        
        # Check available gpiochip devices
        gpiochips = glob.glob('/dev/gpiochip*')
        hx711_logger.info(f"Available gpiochip devices: {gpiochips}")
        
        if not gpiochips:
            hx711_logger.error("No /dev/gpiochip* devices found!")
            hx711_logger.error("This is a kernel/hardware issue - GPIO not available")
            return None
        
        # Find and open the correct gpiochip
        chip_handle = None
        chip_num = None
        last_error = None
        hx711_logger.info(f"lgpio module location: {lgpio.__file__}")
        hx711_logger.info(f"Current process UID: {os.getuid()}, GID: {os.getgid()}, Groups: {os.getgroups()}")
        
        for chip in range(10):
            try:
                hx711_logger.info(f"Attempting to open /dev/gpiochip{chip}...")
                h = lgpio.gpiochip_open(chip)
                hx711_logger.info(f"Successfully opened gpiochip{chip}, handle: {h}")
                lgpio.gpio_claim_input(h, dout)
                hx711_logger.info(f"Claimed GPIO{dout} as input")
                lgpio.gpio_claim_output(h, sck, 0)
                hx711_logger.info(f"Claimed GPIO{sck} as output")
                chip_handle = h
                chip_num = chip
                break
            except Exception as e:
                last_error = f"chip{chip}: {type(e).__name__}: {str(e)}"
                hx711_logger.info(f"Failed on chip{chip}: {last_error}")
                try:
                    # Try to free any claimed GPIOs before closing
                    try:
                        lgpio.gpio_free(h, dout)
                    except:
                        pass
                    try:
                        lgpio.gpio_free(h, sck)
                    except:
                        pass
                    lgpio.gpiochip_close(h)
                except:
                    pass
        
        if chip_handle is None:
            hx711_logger.error(f"Could not claim GPIO pins on any gpiochip")
            hx711_logger.error(f"Last error: {last_error}")
            hx711_logger.error("Make sure python3-lgpio is installed: sudo apt-get install python3-lgpio")
            return None
        
        hx711_logger.info(f"Using /dev/gpiochip{chip_num}")
        
        # Store configuration in a dict (our "sensor object")
        # Handle None values from config
        reference_unit = config.get('reference_unit', 1.0)
        if reference_unit is None:
            reference_unit = 1.0
        
        offset = config.get('offset', 0.0)
        if offset is None:
            offset = 0.0
        
        hx_dict = {
            'handle': chip_handle,
            'chip': chip_num,
            'dout': dout,
            'sck': sck,
            'reference_unit': float(reference_unit),
            'offset': float(offset),
            'channel': config.get('channel', 'A-128')
        }
        
        # Test read to verify hardware
        hx711_logger.info("Testing hardware connection...")
        time.sleep(0.2)
        
        try:
            test_val = _hx711_read_raw(hx_dict)
            if test_val is None:
                hx711_logger.error("No data from HX711 - check wiring")
                lgpio.gpiochip_close(chip_handle)
                return None
        except Exception as e:
            hx711_logger.error(f"Hardware test failed: {e}")
            lgpio.gpiochip_close(chip_handle)
            return None
        
        hx711_logger.info("=" * 60)
        hx711_logger.info(f"✓ HX711 SUCCESSFULLY INITIALIZED - Raw value: {test_val}")
        hx711_logger.info("=" * 60)
        return hx_dict
        
    except ImportError as e:
        hx711_logger.error(f"lgpio not available: {e}")
        hx711_logger.error("Install with: sudo apt-get install python3-lgpio")
        return None
    except Exception as e:
        hx711_logger.error(f"Initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return None

def _hx711_read_raw(hx_dict):
    """Read raw 24-bit value from HX711 using lgpio"""
    import lgpio
    import time
    
    h = hx_dict['handle']
    dout = hx_dict['dout']
    sck = hx_dict['sck']
    
    # Wait for data ready (DT goes low)
    timeout = time.time() + 1.0
    while lgpio.gpio_read(h, dout) == 1:
        if time.time() > timeout:
            return None
        time.sleep(0.001)
    
    count = 0
    # Read 24 bits
    for _ in range(24):
        lgpio.gpio_write(h, sck, 1)
        count = (count << 1) | (1 if lgpio.gpio_read(h, dout) else 0)
        lgpio.gpio_write(h, sck, 0)
    
    # Set gain/channel with extra pulses
    channel = hx_dict.get('channel', 'A-128')
    pulses = {'A-128': 1, 'A-64': 3, 'B-32': 2}.get(channel, 1)
    for _ in range(pulses):
        lgpio.gpio_write(h, sck, 1)
        lgpio.gpio_write(h, sck, 0)
    
    # Convert from 24-bit two's complement to signed int
    if count & 0x800000:
        count -= 1 << 24
    
    return count

def dout_to_physical(gpio):
    """Helper to convert GPIO to physical pin for debugging"""
    gpio_to_physical = {2: 3, 3: 5, 4: 7, 17: 11, 27: 13, 22: 15, 10: 19, 9: 21, 11: 23, 5: 29, 6: 31, 13: 33, 19: 35, 26: 37,
                        14: 8, 15: 10, 18: 12, 23: 16, 24: 18, 25: 22, 8: 24, 7: 26, 1: 28, 12: 32, 16: 36, 20: 38, 21: 40}
    return gpio_to_physical.get(gpio, '?')

def sck_to_physical(gpio):
    """Helper to convert GPIO to physical pin for debugging"""
    return dout_to_physical(gpio)

def read_hx711(sensor, config):
    """Read force from HX711"""
    try:
        if sensor is None:
            return 0
        
        import time
        
        # Take multiple readings and average
        readings = []
        for _ in range(3):
            raw = _hx711_read_raw(sensor)
            if raw is not None:
                readings.append(raw)
            time.sleep(0.01)
        
        if not readings:
            hx711_logger.warning("No valid readings from HX711")
            return 0
        
        # Average the readings
        raw_avg = sum(readings) / len(readings)
        
        # Apply calibration: (raw - offset) / reference_unit
        offset = sensor.get('offset', 0)
        reference_unit = sensor.get('reference_unit', 1)
        
        value = (raw_avg - offset) / reference_unit
        return value
        
    except Exception as e:
        hx711_logger.error(f"Error reading sensor: {e}")
        return 0

def cleanup_hx711(sensor):
    """Clean up HX711 GPIO resources"""
    try:
        if sensor is None:
            return
        
        import lgpio
        
        handle = sensor.get('handle')
        dout = sensor.get('dout')
        sck = sensor.get('sck')
        
        if handle is not None:
            # Free GPIO lines
            try:
                lgpio.gpio_free(handle, dout)
            except:
                pass
            try:
                lgpio.gpio_free(handle, sck)
            except:
                pass
            
            # Close gpiochip
            try:
                lgpio.gpiochip_close(handle)
                hx711_logger.info(f"HX711 cleaned up: freed GPIO{dout}, GPIO{sck}")
            except:
                pass
    except Exception as e:
        hx711_logger.error(f"Error cleaning up HX711: {e}")

# ==================== FAN PWM CONTROL ====================

def init_fan_pwm():
    """Initialize PWM for fan control"""
    global fan_state, _pwm_device
    try:
        from gpiozero import PWMOutputDevice, Device
        
        # If already initialized, return success
        if _pwm_device is not None:
            print("✓ PWM already initialized")
            fan_state['pwm_instance'] = _pwm_device
            return True
        
        pin = fan_state['pwm_pin']
        
        print(f"🔧 Initializing PWM on GPIO{pin}...")
        
        # Initialize PWM with gpiozero (GPIO12)
        # Using 2kHz frequency (good for most 0-10V PWM modules and fans)
        # Let gpiozero auto-detect the best pin factory
        _pwm_device = PWMOutputDevice(pin, frequency=2000)
        
        fan_state['pwm_instance'] = _pwm_device
        
        print(f"✓ Fan PWM initialized on GPIO{pin} at 2000Hz")
        print(f"✓ PWM device: {_pwm_device}")
        print(f"✓ Pin factory: {Device.pin_factory}")
        print(f"✓ Pin class: {_pwm_device.pin.__class__.__name__}")
        return True
    except Exception as e:
        print(f"✗ Failed to initialize fan PWM: {e}")
        import traceback
        traceback.print_exc()
        return False

def set_fan_speed(speed_percent):
    """Set fan speed (0-100%)"""
    global fan_state
    try:
        print(f"🎛️  set_fan_speed called with {speed_percent}%")
        
        if fan_state['pwm_instance'] is None:
            print("PWM not initialized, initializing now...")
            if not init_fan_pwm():
                print("❌ Failed to initialize PWM")
                return False
        
        pwm_device = fan_state['pwm_instance']
        print(f"PWM device before: {pwm_device}")
        
        # Clamp speed to 0-100
        speed_percent = max(0, min(100, speed_percent))
        
        # Convert percentage to 0.0-1.0 range for gpiozero
        duty_value = speed_percent / 100.0
        
        print(f"Setting PWM value to {duty_value:.2f} ({speed_percent}%)")
        
        # Set PWM value
        pwm_device.value = duty_value
        
        # Verify it's actually set
        print(f"PWM device after: {pwm_device}")
        print(f"PWM value readback: {pwm_device.value}")
        print(f"PWM is_active: {pwm_device.is_active}")
        
        fan_state['running'] = (speed_percent > 0)
        fan_state['speed'] = speed_percent
        print(f"✓ Fan speed set to {speed_percent}% (PWM value: {duty_value:.2f})")
        return True
        
    except Exception as e:
        print(f"❌ Error setting fan speed: {e}")
        import traceback
        traceback.print_exc()
        return False
        return False

def cleanup_fan_pwm():
    """Clean up fan PWM resources"""
    global fan_state, _pwm_device
    try:
        if _pwm_device is not None:
            # Close gpiozero PWM device
            _pwm_device.close()
            _pwm_device = None
            fan_state['pwm_instance'] = None
            fan_state['running'] = False
            fan_state['speed'] = 0
            print(f"Fan PWM cleaned up (GPIO{fan_state['pwm_pin']})")
    except Exception as e:
        print(f"Error cleaning up fan PWM: {e}")

def check_fan_safety():
    """Monitor client heartbeat and stop fan if connection is lost"""
    import time
    while True:
        try:
            if FAN_SAFETY_TIMEOUT > 0 and fan_state['running'] and fan_state['last_heartbeat'] is not None:
                time_since_heartbeat = time.time() - fan_state['last_heartbeat']
                if time_since_heartbeat > FAN_SAFETY_TIMEOUT:
                    print(f"⚠️ No client heartbeat for {time_since_heartbeat:.1f}s - Emergency stopping fan")
                    set_fan_speed(0)
                    socketio.emit('fan_emergency_stop', {
                        'reason': 'Client connection lost',
                        'timeout': FAN_SAFETY_TIMEOUT
                    })
            time.sleep(1)  # Check every second
        except Exception as e:
            print(f"Error in fan safety monitor: {e}")
            time.sleep(1)

def pid_control_loop():
    """
    PID control loop thread - reads airspeed sensor and controls fan speed.
    Runs at ~10Hz (100ms updates).
    """
    global pid_state
    import threading
    
    logger.info("PID control loop started")
    
    while True:
        try:
            # Check stop event
            if pid_state['stop_event'] and pid_state['stop_event'].is_set():
                logger.info("PID control loop stopped")
                break
            
            if not pid_state['enabled'] or pid_state['controller'] is None:
                time.sleep(0.1)
                continue
            
            # Get airspeed sensor reading
            sensor_id = pid_state['airspeed_sensor_id']
            if not sensor_id:
                logger.warning("PID: No airspeed sensor configured")
                time.sleep(0.1)
                continue
            
            # Read current airspeed from sensor_last_values cache
            current_airspeed = sensor_last_values.get(sensor_id, 0.0)
            
            if current_airspeed is None:
                current_airspeed = 0.0
            
            pid_state['current_airspeed'] = current_airspeed
            
            # Calculate PID output
            control_output = pid_state['controller'].update(current_airspeed)
            pid_state['control_output'] = control_output
            
            # Set fan speed
            set_fan_speed(int(control_output))
            
            # Emit PID status for live updates
            socketio.emit('pid_update', {
                'target': pid_state['target_airspeed'],
                'current': current_airspeed,
                'output': control_output,
                'enabled': pid_state['enabled']
            })
            
            # Run at 10Hz
            time.sleep(0.1)
            
        except Exception as e:
            logger.error(f"Error in PID control loop: {e}")
            time.sleep(0.1)

def init_ads1115(config):
    """Initialize ADS1115 ADC"""
    try:
        import board
        import busio
        import adafruit_ads1x15.ads1115 as ADS
        from adafruit_ads1x15.analog_in import AnalogIn
        
        i2c = busio.I2C(board.SCL, board.SDA)
        address = int(config.get('address', '0x48'), 16)
        ads = ADS.ADS1115(i2c, address=address)
        
        # Set gain
        gain_map = {'2/3': 2/3, '1': 1, '2': 2, '4': 4, '8': 8, '16': 16}
        ads.gain = gain_map.get(config.get('gain', '1'), 1)
        
        # Set data rate
        ads.data_rate = int(config.get('data_rate', '128'))
        
        # Create channel
        channel_num = int(config.get('channel', '0'))
        chan = AnalogIn(ads, channel_num)
        
        print(f"ADS1115 initialized at {config.get('address')} channel {channel_num}")
        return {'ads': ads, 'channel': chan}
    except Exception as e:
        print(f"Error initializing ADS1115: {e}")
        return None

def read_ads1115(sensor, config):
    """Read voltage from ADS1115"""
    try:
        if sensor is None:
            return 0
        return sensor['channel'].voltage
    except Exception as e:
        print(f"Error reading ADS1115: {e}")
        return 0

def init_bmp280(config):
    """Initialize BMP280 pressure/temperature sensor"""
    try:
        import board
        import busio
        import adafruit_bmp280
        
        i2c = busio.I2C(board.SCL, board.SDA)
        address = int(config.get('address', '0x76'), 16)
        sensor = adafruit_bmp280.Adafruit_BMP280_I2C(i2c, address)
        
        sensor.sea_level_pressure = float(config.get('sea_level_pressure', 1013.25))
        
        print(f"BMP280 initialized at {config.get('address')}")
        return sensor
    except Exception as e:
        print(f"Error initializing BMP280: {e}")
        return None

def read_bmp280(sensor, config, output='pressure'):
    """Read pressure or temperature from BMP280"""
    try:
        if sensor is None:
            return 0
        if output == 'temperature':
            return sensor.temperature
        elif output == 'altitude':
            return sensor.altitude
        else:  # pressure
            return sensor.pressure
    except Exception as e:
        print(f"Error reading BMP280: {e}")
        return 0

def init_sdp811(config):
    """Initialize SDP811 differential pressure sensor"""
    try:
        from sensirion_i2c_driver import LinuxI2cTransceiver, I2cConnection
        from sensirion_i2c_sdp import Sdp8xxI2cDevice
        
        i2c_transceiver = LinuxI2cTransceiver('/dev/i2c-1')
        i2c_connection = I2cConnection(i2c_transceiver)
        
        address = int(config.get('address', '0x25'), 16)
        sensor = Sdp8xxI2cDevice(i2c_connection, slave_address=address)
        
        # Start measurement with averaging mode
        averaging = config.get('averaging', 'until_stable')
        if averaging == 'until_stable':
            sensor.start_continuous_measurement_with_averaging()
        else:
            sensor.start_continuous_measurement()
        
        print(f"SDP811 initialized at {config.get('address')}")
        return {'sensor': sensor, 'altitude': float(config.get('altitude', 0))}
    except Exception as e:
        print(f"Error initializing SDP811: {e}")
        return None

def read_sdp811(sensor_data, config, output='airspeed'):
    """Read differential pressure or calculate airspeed from SDP811"""
    try:
        if sensor_data is None:
            return 0
        
        sensor = sensor_data['sensor']
        dp_pa, temp_c = sensor.read_measurement()
        
        if output == 'differential_pressure':
            return dp_pa
        elif output == 'temperature':
            return temp_c
        else:  # airspeed - calculate from differential pressure
            # Air density calculation
            altitude = sensor_data.get('altitude', 0)
            temp_k = temp_c + 273.15
            pressure_pa = 101325 * (1 - 0.0065 * altitude / 288.15) ** 5.255
            rho = pressure_pa / (287.05 * temp_k)  # kg/m³
            
            # Airspeed from Bernoulli equation: v = sqrt(2*dP/rho)
            if dp_pa < 0:
                return -math.sqrt(abs(2 * dp_pa / rho))
            return math.sqrt(2 * dp_pa / rho)
    except Exception as e:
        print(f"Error reading SDP811: {e}")
        return 0

def init_dht22(config):
    """Initialize DHT22 temperature/humidity sensor"""
    try:
        import adafruit_dht
        import board
        
        pin_num = int(config.get('pin', 4))
        pin = getattr(board, f'D{pin_num}')
        sensor = adafruit_dht.DHT22(pin)
        
        print(f"DHT22 initialized on pin {pin_num}")
        return sensor
    except Exception as e:
        print(f"Error initializing DHT22: {e}")
        return None

def read_dht22(sensor, config, output='temperature'):
    """Read temperature or humidity from DHT22"""
    try:
        if sensor is None:
            return 0
        if output == 'humidity':
            return sensor.humidity
        else:  # temperature
            return sensor.temperature
    except Exception as e:
        print(f"Error reading DHT22: {e}")
        return 0

def init_ds18b20(config):
    """Initialize DS18B20 temperature sensor"""
    try:
        from w1thermsensor import W1ThermSensor, Unit
        
        address = config.get('address', '').strip()
        if address:
            sensor = W1ThermSensor(sensor_id=address)
        else:
            sensor = W1ThermSensor()  # Auto-detect first sensor
        
        print(f"DS18B20 initialized: {sensor.id}")
        return sensor
    except Exception as e:
        print(f"Error initializing DS18B20: {e}")
        return None

def read_ds18b20(sensor, config):
    """Read temperature from DS18B20"""
    try:
        if sensor is None:
            return 0
        from w1thermsensor import Unit
        return sensor.get_temperature(Unit.DEGREES_C)
    except Exception as e:
        print(f"Error reading DS18B20: {e}")
        return 0

def init_mcp3008(config):
    """Initialize MCP3008 ADC"""
    try:
        import busio
        import digitalio
        import board
        import adafruit_mcp3xxx.mcp3008 as MCP
        from adafruit_mcp3xxx.analog_in import AnalogIn
        
        spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)
        cs = digitalio.DigitalInOut(board.CE0)
        mcp = MCP.MCP3008(spi, cs)
        
        channel_num = int(config.get('channel', '0'))
        chan = AnalogIn(mcp, channel_num)
        
        print(f"MCP3008 initialized on channel {channel_num}")
        return {'mcp': mcp, 'channel': chan, 'vref': float(config.get('vref', 3.3))}
    except Exception as e:
        print(f"Error initializing MCP3008: {e}")
        return None

def read_mcp3008(sensor, config):
    """Read voltage from MCP3008"""
    try:
        if sensor is None:
            return 0
        return sensor['channel'].voltage
    except Exception as e:
        print(f"Error reading MCP3008: {e}")
        return 0

def init_mpu6050(config):
    """Initialize MPU6050 gyro/accelerometer"""
    try:
        import board
        import busio
        import adafruit_mpu6050
        
        i2c = busio.I2C(board.SCL, board.SDA)
        address = int(config.get('address', '0x68'), 16)
        sensor = adafruit_mpu6050.MPU6050(i2c, address)
        
        print(f"MPU6050 initialized at {config.get('address')}")
        return sensor
    except Exception as e:
        print(f"Error initializing MPU6050: {e}")
        return None

def read_mpu6050(sensor, config):
    """Read value from MPU6050"""
    try:
        if sensor is None:
            return 0
        
        output = config.get('output', 'accel_x')
        
        if output == 'accel_x':
            return sensor.acceleration[0]
        elif output == 'accel_y':
            return sensor.acceleration[1]
        elif output == 'accel_z':
            return sensor.acceleration[2]
        elif output == 'gyro_x':
            return sensor.gyro[0]
        elif output == 'gyro_y':
            return sensor.gyro[1]
        elif output == 'gyro_z':
            return sensor.gyro[2]
        elif output == 'temperature':
            return sensor.temperature
        return 0
    except Exception as e:
        print(f"Error reading MPU6050: {e}")
        return 0

def init_xgzp6847a(config):
    """Initialize XGZP6847A differential pressure sensor"""
    try:
        from smbus2 import SMBus
        
        address = int(config.get('address', '0x6D'), 16)
        bus = SMBus(1)  # I2C bus 1
        
        # Store config with sensor instance
        sensor_data = {
            'bus': bus,
            'address': address,
            'pressure_range': float(config.get('pressure_range', 5))  # kPa
        }
        
        print(f"XGZP6847A initialized at {config.get('address')}")
        return sensor_data
    except Exception as e:
        print(f"Error initializing XGZP6847A: {e}")
        return None

def read_xgzp6847a(sensor, config):
    """Read value from XGZP6847A"""
    try:
        if sensor is None:
            return 0
        
        bus = sensor['bus']
        address = sensor['address']
        pressure_range_kpa = sensor['pressure_range']
        
        # Read 3 bytes from sensor
        data = bus.read_i2c_block_data(address, 0x00, 3)
        
        # Convert to pressure (Pa)
        # 24-bit value: combine bytes
        raw = (data[0] << 16) | (data[1] << 8) | data[2]
        
        # Calculate gauge pressure based on range
        # Full scale = 2^24 - 1 = 16777215
        # This is a gauge pressure sensor (relative to atmospheric)
        pressure_pa = (raw / 16777215.0) * pressure_range_kpa * 1000
        
        output = config.get('output', 'pressure')
        
        if output == 'pressure':
            return pressure_pa
        elif output == 'temperature':
            # Temperature reading (if available - some variants support this)
            # For now return 0 as not all variants have temp sensor
            return 0
        
        return pressure_pa
    except Exception as e:
        print(f"Error reading XGZP6847A: {e}")
        return 0

def init_bme280(config):
    """Initialize BME280 environmental sensor"""
    try:
        import board
        import busio
        import adafruit_bme280
        
        i2c = busio.I2C(board.SCL, board.SDA)
        address = int(config.get('address', '0x77'), 16)
        sensor = adafruit_bme280.Adafruit_BME280_I2C(i2c, address)
        
        # Set sea level pressure for altitude calculation
        sensor.sea_level_pressure = float(config.get('sea_level_pressure', 1013.25))
        
        print(f"BME280 initialized at {config.get('address')}")
        return sensor
    except Exception as e:
        print(f"Error initializing BME280: {e}")
        return None

def read_bme280(sensor, config):
    """Read value from BME280"""
    try:
        if sensor is None:
            return 0
        
        output = config.get('output', 'pressure')
        
        if output == 'pressure':
            return sensor.pressure  # hPa
        elif output == 'temperature':
            return sensor.temperature  # °C
        elif output == 'humidity':
            return sensor.humidity  # %
        elif output == 'altitude':
            return sensor.altitude  # meters
        return 0
    except Exception as e:
        print(f"Error reading BME280: {e}")
        return 0

def init_ina219(config):
    """Initialize INA219 current sensor"""
    try:
        import board
        import busio
        import adafruit_ina219
        
        i2c = busio.I2C(board.SCL, board.SDA)
        address = int(config.get('address', '0x40'), 16)
        sensor = adafruit_ina219.INA219(i2c, address)
        
        print(f"INA219 initialized at {config.get('address')}")
        return sensor
    except Exception as e:
        print(f"Error initializing INA219: {e}")
        return None

def read_ina219(sensor, config):
    """Read value from INA219"""
    try:
        if sensor is None:
            return 0
        
        output = config.get('output', 'current')
        
        if output == 'current':
            return sensor.current  # mA
        elif output == 'voltage':
            return sensor.bus_voltage  # V
        elif output == 'power':
            return sensor.power  # mW
        return 0
    except Exception as e:
        print(f"Error reading INA219: {e}")
        return 0

def init_vl53l0x(config):
    """Initialize VL53L0X distance sensor"""
    try:
        import board
        import busio
        import adafruit_vl53l0x
        
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_vl53l0x.VL53L0X(i2c)
        
        # Set measurement mode
        mode = config.get('mode', 'better_accuracy')
        if mode == 'better_accuracy':
            sensor.measurement_timing_budget = 200000
        elif mode == 'long_range':
            sensor.measurement_timing_budget = 33000
        elif mode == 'high_speed':
            sensor.measurement_timing_budget = 20000
        
        print(f"VL53L0X initialized in {mode} mode")
        return sensor
    except Exception as e:
        print(f"Error initializing VL53L0X: {e}")
        return None

def read_vl53l0x(sensor, config):
    """Read distance from VL53L0X"""
    try:
        if sensor is None:
            return 0
        return sensor.range  # mm
    except Exception as e:
        print(f"Error reading VL53L0X: {e}")
        return 0

def init_force_balance(config):
    """Initialize force balance sensor - no hardware init needed, just config validation"""
    try:
        # Validate required fields
        if not config.get('source_sensor_1') or not config.get('source_sensor_2') or not config.get('source_sensor_3'):
            print("Error: Force balance requires 3 source sensors")
            return None
        
        if not config.get('formula'):
            print("Error: Force balance requires geometric formula")
            return None
        
        # Return config as "instance" - we'll use it during reads
        return {
            'config': config,
            'calibration': config.get('calibration', {
                'tare_offsets': [0, 0, 0],
                'calibration_factor': 1.0,
                'is_calibrated': False
            })
        }
    except Exception as e:
        print(f"Error initializing force balance: {e}")
        return None

def read_force_balance(instance, config):
    """Read force balance value using geometric formula and calibration"""
    try:
        if instance is None:
            return 0.0
        
        # Get source sensor IDs
        s1_id = config.get('source_sensor_1')
        s2_id = config.get('source_sensor_2')
        s3_id = config.get('source_sensor_3')
        
        # Debug: Log what we're looking for and what's available
        print(f"Force balance looking for: s1={s1_id}, s2={s2_id}, s3={s3_id}")
        print(f"Available sensor values: {list(sensor_last_values.keys())}")
        
        # Read raw values from source sensors
        raw_s1 = sensor_last_values.get(s1_id, 0)
        raw_s2 = sensor_last_values.get(s2_id, 0)
        raw_s3 = sensor_last_values.get(s3_id, 0)
        
        print(f"Raw values: s1={raw_s1}, s2={raw_s2}, s3={raw_s3}")
        
        # Apply tare offsets
        calibration = instance.get('calibration', {})
        tare_offsets = calibration.get('tare_offsets', [0, 0, 0])
        
        s1 = raw_s1 - tare_offsets[0]
        s2 = raw_s2 - tare_offsets[1]
        s3 = raw_s3 - tare_offsets[2]
        
        # Evaluate geometric formula
        formula = config.get('formula', '0')
        
        # Replace s1, s2, s3 in formula
        eval_formula = formula.replace('s1', str(s1)).replace('s2', str(s2)).replace('s3', str(s3))
        
        # Replace ^ with ** for power operation
        eval_formula = eval_formula.replace('^', '**')
        
        # Validate formula safety
        if not re.match(r'^[\d\s\.\+\-\*/\(\)\*]+$', eval_formula):
            print(f"Warning: Invalid formula: {formula}")
            return 0.0
        
        # Calculate raw result
        raw_result = eval(eval_formula)
        
        # Apply calibration factor
        calibration_factor = calibration.get('calibration_factor', 1.0)
        final_value = raw_result * calibration_factor
        
        return float(final_value)
        
    except Exception as e:
        print(f"Error reading force balance: {e}")
        return 0.0

def auto_create_udp_sensor(sensor_id, port, source_ip):
    """Auto-create a UDP sensor configuration if it doesn't exist"""
    global deleted_udp_sensors
    try:
        # Check if sensor was manually deleted (don't auto-recreate)
        if sensor_id in deleted_udp_sensors:
            return
        
        # Check if this is a composite sensor (e.g., "esp32_sensor_063C_lift")
        # and if the base sensor or any variant was deleted
        if '_' in sensor_id:
            # Check all deleted sensors for matches with same base
            base_pattern = sensor_id.rsplit('_', 1)[0]  # "esp32_sensor_063C" from "esp32_sensor_063C_lift"
            for deleted_id in deleted_udp_sensors:
                if deleted_id.startswith(base_pattern + '_'):
                    # Another sensor from this device was deleted, don't auto-create this one
                    return
        
        # Check if sensor already exists
        sensors = current_settings.get('sensors', [])
        for sensor in sensors:
            if sensor.get('id') == sensor_id:
                return  # Already exists
        
        # Create new sensor configuration
        new_sensor = {
            'id': sensor_id,
            'name': f'UDP: {sensor_id}',
            'type': 'udp_network',
            'enabled': True,
            'config': {
                'udp_port': port,
                'sensor_id': sensor_id,
                'timeout': 5
            }
        }
        
        sensors.append(new_sensor)
        current_settings['sensors'] = sensors
        save_settings_to_file(current_settings)
        print(f"Auto-created UDP sensor: {sensor_id} on port {port}")
        
        # Emit socket event to update UI
        socketio.emit('sensor_added', new_sensor)
        
    except Exception as e:
        print(f"Error auto-creating UDP sensor: {e}")

def udp_listener_thread(port):
    """Background thread to listen for UDP packets on a specific port"""
    import socket
    import json
    import time
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)  # 1 second timeout for clean shutdown
    
    try:
        sock.bind(('0.0.0.0', port))
        print(f"UDP listener started on port {port}")
        
        while True:
            try:
                data, addr = sock.recvfrom(1024)  # Buffer size 1024 bytes
                
                # Try to parse JSON
                try:
                    packet = json.loads(data.decode('utf-8'))
                    
                    # Check for multi-value format: {"id": "esp32_1", "values": {"lift": 10.5, "drag": 2.3}}
                    if 'values' in packet and isinstance(packet['values'], dict):
                        base_id = packet.get('id', 'unknown')
                        for key, value in packet['values'].items():
                            sensor_id = f"{base_id}_{key}"
                            udp_sensor_data[sensor_id] = {
                                'value': float(value),
                                'timestamp': time.time(),
                                'port': port,
                                'source_ip': addr[0]
                            }
                            # Auto-create sensor configuration
                            auto_create_udp_sensor(sensor_id, port, addr[0])
                            print(f"UDP multi-value from {addr[0]}: {sensor_id} = {value}")
                    
                    # Check for single-value format: {"id": "sensor_id", "value": 23.5}
                    elif 'id' in packet and 'value' in packet:
                        sensor_id = packet.get('id')
                        value = packet.get('value')
                        
                        if sensor_id and value is not None:
                            udp_sensor_data[sensor_id] = {
                                'value': float(value),
                                'timestamp': time.time(),
                                'port': port,
                                'source_ip': addr[0]
                            }
                            # Auto-create sensor configuration
                            auto_create_udp_sensor(sensor_id, port, addr[0])
                            print(f"UDP received from {addr[0]}: {sensor_id} = {value}")
                        else:
                            print(f"UDP packet missing id or value: {packet}")
                    else:
                        print(f"UDP packet invalid format: {packet}")
                        
                except json.JSONDecodeError:
                    print(f"UDP packet not valid JSON from {addr[0]}: {data}")
                except ValueError as e:
                    print(f"UDP packet value conversion error: {e}")
                    
            except socket.timeout:
                # Normal timeout, continue loop
                continue
            except Exception as e:
                print(f"Error receiving UDP packet on port {port}: {e}")
                
    except Exception as e:
        print(f"Error starting UDP listener on port {port}: {e}")
    finally:
        sock.close()
        print(f"UDP listener stopped on port {port}")
        if port in udp_listeners:
            del udp_listeners[port]


def udp_discovery_listener():
    """
    Background thread that listens for UDP announcement packets from ESP32 devices.
    Runs on port 5555 and stores discovered device information.
    """
    global discovered_devices
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)  # Enable broadcast
    sock.settimeout(1.0)  # 1 second timeout
    
    try:
        sock.bind(('0.0.0.0', UDP_DISCOVERY_PORT))
        logger.info(f"UDP discovery listener started on port {UDP_DISCOVERY_PORT}")
        print(f"UDP discovery listener is active on port {UDP_DISCOVERY_PORT}")
        print(f"Socket bound successfully to 0.0.0.0:{UDP_DISCOVERY_PORT}")
        
        while True:
            try:
                data, addr = sock.recvfrom(2048)
                logger.info(f"Received UDP packet from {addr[0]}: {data}")  # Log full packet
                
                try:
                    packet = json.loads(data.decode('utf-8'))
                    
                    # Check if this is an announcement packet
                    if packet.get('type') == 'announcement':
                        sensor_id = packet.get('sensor_id')
                        if not sensor_id:
                            logger.warning(f"Announcement packet missing sensor_id from {addr[0]}")
                            continue
                            
                        # Store discovered device info
                        with discovery_lock:
                            device_id = f"{sensor_id}_{addr[0]}"  # Unique key
                            discovered_devices[device_id] = {
                                'sensor_id': sensor_id,
                                'ip': packet.get('ip', addr[0]),
                                'mac': packet.get('mac', 'unknown'),
                                'sensor_type': packet.get('sensor_type', 'unknown'),
                                'firmware': packet.get('firmware', 'unknown'),
                                'multi_value': packet.get('multi_value', False),
                                'sensor_keys': packet.get('sensor_keys', ['value']),
                                'last_seen': time.time()
                            }
                            logger.info(f"✓ Discovered device: {sensor_id} at {addr[0]} (multi_value={packet.get('multi_value')})")
                            logger.info(f"✓ discovered_devices now has {len(discovered_devices)} entries, dict id: {id(discovered_devices)}")
                            print(f"✓ ESP32 discovered: {sensor_id} at {addr[0]}")
                    else:
                        logger.debug(f"Received non-announcement packet from {addr[0]}: {packet.get('type')}")
                            
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON from {addr[0]}: {e}")
                    pass
                except Exception as e:
                    logger.error(f"Error processing discovery packet from {addr[0]}: {e}")
                    
            except socket.timeout:
                # Clean up stale devices (not seen in 60 seconds)
                with discovery_lock:
                    current_time = time.time()
                    stale_devices = [
                        dev_id for dev_id, dev in discovered_devices.items()
                        if current_time - dev['last_seen'] > 60
                    ]
                    for dev_id in stale_devices:
                        del discovered_devices[dev_id]
                continue
                
            except Exception as e:
                logger.error(f"Error in discovery listener: {e}")
                
    except Exception as e:
        logger.error(f"Failed to start UDP discovery listener: {e}")
    finally:
        sock.close()
        logger.info("UDP discovery listener stopped")


def send_esp32_command(ip, endpoint, data=None, timeout=5):
    """
    Send HTTP command to ESP32 device.
    
    Args:
        ip: ESP32 IP address
        endpoint: API endpoint (e.g., '/config', '/start')
        data: Dictionary to send as JSON body (optional)
        timeout: Request timeout in seconds
        
    Returns:
        dict: Response JSON or error dict
    """
    try:
        import requests
        
        url = f"http://{ip}{endpoint}"
        
        if data is not None:
            response = requests.post(url, json=data, timeout=timeout)
        else:
            response = requests.get(url, timeout=timeout)
            
        response.raise_for_status()
        return response.json()
        
    except requests.exceptions.Timeout:
        return {'status': 'error', 'error': f'Request timeout after {timeout}s'}
    except requests.exceptions.ConnectionError:
        return {'status': 'error', 'error': f'Cannot connect to device at {ip}'}
    except requests.exceptions.HTTPError as e:
        # Try to parse error message from ESP32 response
        try:
            error_data = e.response.json()
            # ESP32 may return error in various fields
            device_error = error_data.get('error') or error_data.get('message') or str(e)
            return {
                'status': 'error',
                'error': f'HTTP {e.response.status_code}',
                'device_error': device_error,
                'details': error_data.get('details')
            }
        except:
            # If can't parse JSON, use the HTTP error
            return {'status': 'error', 'error': f'HTTP error: {e}'}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def get_local_ip():
    """Get the local IP address of this machine that can reach the internet."""
    try:
        # Create a socket and connect to a public IP (doesn't actually send data)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        logger.error(f"Failed to get local IP: {e}")
        # Fallback to hostname method
        try:
            return socket.gethostbyname(socket.gethostname())
        except:
            return "127.0.0.1"

def configure_esp32_sensor(ip, target_ip, target_port, sensor_rate=1000, sensor_id=None):
    """
    Configure ESP32 sensor to send data to this Raspberry Pi.
    
    Args:
        ip: ESP32 IP address
        target_ip: Raspberry Pi IP to send data to
        target_port: UDP port to send data to
        sensor_rate: Milliseconds between readings (default 1000 = 1 second)
        sensor_id: Optional new sensor ID to assign
        
    Returns:
        dict: Response from ESP32
    """
    config_data = {
        'target_ip': target_ip,
        'target_port': target_port,
        'sensor_rate': sensor_rate
    }
    
    if sensor_id:
        config_data['sensor_id'] = sensor_id
    
    logger.info(f"Configuring ESP32 at {ip} with data: {config_data}")
    return send_esp32_command(ip, '/config', config_data)


def start_esp32_sensor(ip):
    """Tell ESP32 to start sending data."""
    return send_esp32_command(ip, '/start', {})


def stop_esp32_sensor(ip):
    """Tell ESP32 to stop sending data."""
    return send_esp32_command(ip, '/stop', {})


def get_esp32_status(ip):
    """Get current status/configuration from ESP32."""
    return send_esp32_command(ip, '/status')


def init_udp_sensor(config):
    """Initialize UDP network sensor"""
    try:
        import threading
        
        port = int(config.get('udp_port', 5000))
        sensor_id = config.get('sensor_id')
        timeout = int(config.get('timeout', 5))
        
        if not sensor_id:
            print("Error: UDP sensor requires sensor_id")
            return None
        
        # Start UDP listener thread if not already running for this port
        if port not in udp_listeners:
            listener_thread = threading.Thread(target=udp_listener_thread, args=(port,), daemon=True)
            listener_thread.start()
            udp_listeners[port] = listener_thread
            print(f"Started UDP listener on port {port}")
        
        # Return config as instance
        return {
            'port': port,
            'sensor_id': sensor_id,
            'timeout': timeout
        }
        
    except Exception as e:
        print(f"Error initializing UDP sensor: {e}")
        return None

def read_udp_sensor(instance, config):
    """Read value from UDP sensor data cache"""
    try:
        import time
        
        if instance is None:
            return 0.0
        
        sensor_id = instance['sensor_id']
        timeout = instance['timeout']
        
        # Check if we have data for this sensor
        if sensor_id not in udp_sensor_data:
            return 0.0
        
        data = udp_sensor_data[sensor_id]
        age = time.time() - data['timestamp']
        
        # Check if data is stale
        if age > timeout:
            return 0.0
        
        return float(data['value'])
        
    except Exception as e:
        print(f"Error reading UDP sensor: {e}")
        return 0.0

# Sensor handler registry
SENSOR_HANDLERS = {
    'HX711': {'init': init_hx711, 'read': read_hx711, 'cleanup': cleanup_hx711},
    'ADS1115': {'init': init_ads1115, 'read': read_ads1115},
    'BMP280': {'init': init_bmp280, 'read': read_bmp280},
    'SDP811': {'init': init_sdp811, 'read': read_sdp811},
    'DHT22': {'init': init_dht22, 'read': read_dht22},
    'DS18B20': {'init': init_ds18b20, 'read': read_ds18b20},
    'MCP3008': {'init': init_mcp3008, 'read': read_mcp3008},
    'MPU6050': {'init': init_mpu6050, 'read': read_mpu6050},
    'XGZP6847A': {'init': init_xgzp6847a, 'read': read_xgzp6847a},
    'BME280': {'init': init_bme280, 'read': read_bme280},
    'INA219': {'init': init_ina219, 'read': read_ina219},
    'VL53L0X': {'init': init_vl53l0x, 'read': read_vl53l0x},
    'force_balance_lift': {'init': init_force_balance, 'read': read_force_balance},
    'force_balance_drag': {'init': init_force_balance, 'read': read_force_balance},
    'udp_network': {'init': init_udp_sensor, 'read': read_udp_sensor}
}


# Load settings from file
def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading settings: {e}")
    return DEFAULT_SETTINGS.copy()

# Save settings to file
def save_settings_to_file(settings):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving settings: {e}")
        return False

# Global settings
current_settings = load_settings()

# Initialize PID state from settings
pid_state['airspeed_sensor_id'] = current_settings.get('airspeed_sensor_id')
pid_state['min_fan_speed'] = current_settings.get('min_fan_speed', 15.0)

# Add cache control headers
@app.after_request
def add_header(response):
    """Add headers to prevent caching of static files."""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Thread lock for data updates
thread_lock = Lock()
background_thread = None

# Update status tracking
update_in_progress = False
update_lock = Lock()



def generate_mock_data():
    """
    Generate mock sensor data in SI units based on configured sensors.
    
    ALL DATA IS STORED AND TRANSMITTED IN SI UNITS:
    - velocity: meters per second (m/s)
    - lift: Newtons (N)
    - drag: Newtons (N)
    - pressure: kilopascals (kPa)
    - temperature: degrees Celsius (°C)
    - rpm: revolutions per minute (RPM)
    - power: Watts (W)
    - timestamp: Unix timestamp (seconds)
    
    Unit conversions are handled client-side for display only.
    Data logging, calculations, and storage always use SI units.
    """
    sensors = current_settings.get('sensors', [])
    if not sensors or len(sensors) == 0:
        sensors = DEFAULT_SENSORS
        print(f"Using DEFAULT_SENSORS: {len(sensors)} sensors")
    
    data = {'timestamp': time.time()}
    sensor_values = {}
    
    # First pass: Generate data for base sensors (hardware and mock)
    for sensor in sensors:
        if not sensor.get('enabled', True):
            continue
            
        sensor_id = sensor['id']
        sensor_type = sensor['type']
        
        # Skip calculated and force_balance sensors in first pass
        if sensor_type in ['calculated', 'force_balance_lift', 'force_balance_drag']:
            continue
        
        print(f"Processing sensor: {sensor_id}, type: {sensor_type}, name: {sensor['name']}")
        
        if sensor_type == 'mock':
            # Generate random mock data
            # Try to generate sensible defaults based on sensor ID/name, otherwise random
            sensor_lower = (sensor_id + sensor['name']).lower()
            
            if 'velocity' in sensor_lower or 'speed' in sensor_lower:
                value = 15.5 + random.uniform(-2, 2)
            elif 'lift' in sensor_lower:
                value = 125.3 + random.uniform(-10, 10)
            elif 'drag' in sensor_lower:
                value = 45.2 + random.uniform(-5, 5)
            elif 'pressure' in sensor_lower:
                value = 101.3 + random.uniform(-0.5, 0.5)
            elif 'temperature' in sensor_lower or 'temp' in sensor_lower:
                value = 22.5 + random.uniform(-1, 1)
            elif 'rpm' in sensor_lower or 'rotation' in sensor_lower:
                value = 3500 + random.randint(-100, 100)
            elif 'power' in sensor_lower or 'watt' in sensor_lower:
                value = 850 + random.uniform(-50, 50)
            elif 'force' in sensor_lower:
                value = 50.0 + random.uniform(-5, 5)
            elif 'angle' in sensor_lower:
                value = random.uniform(-45, 45)
            else:
                # Generic mock data for unknown sensor types
                value = random.uniform(0, 100)
            
            sensor_values[sensor_id] = value
            data[sensor_id] = value
            
        elif sensor_type == 'calculated':
            # Handle calculated values (will be computed after all sensors)
            pass
        elif sensor_type in SENSOR_HANDLERS:
            # Hardware sensor - initialize if needed and read value
            if sensor_id not in sensor_instances:
                print(f"Initializing hardware sensor: {sensor_id} ({sensor_type})")
                handler = SENSOR_HANDLERS[sensor_type]
                instance = handler['init'](sensor.get('config', {}))
                sensor_instances[sensor_id] = instance
            
            # Read value from sensor
            if sensor_id in sensor_instances:
                handler = SENSOR_HANDLERS[sensor_type]
                value = handler['read'](sensor_instances[sensor_id], sensor.get('config', {}))
                sensor_values[sensor_id] = value
                sensor_last_values[sensor_id] = value  # Cache for status checks
                data[sensor_id] = value
            else:
                # Failed to initialize
                value = 0.0
                sensor_values[sensor_id] = value
                data[sensor_id] = value
        else:
            # Unknown sensor type - return 0
            value = 0.0
            sensor_values[sensor_id] = value
            data[sensor_id] = value
    
    # Calculate derived values with dependency resolution
    # Build dependency graph for calculated sensors
    calculated_sensors = [s for s in sensors if s.get('enabled', True) and s['type'] == 'calculated']
    
    # Topological sort to handle dependencies
    evaluated = set()
    max_iterations = len(calculated_sensors) + 1
    iteration = 0
    
    while calculated_sensors and iteration < max_iterations:
        iteration += 1
        made_progress = False
        
        for sensor in calculated_sensors[:]:  # Copy list to modify during iteration
            formula = sensor.get('config', {}).get('formula', '')
            sensor_id = sensor['id']
            
            # Extract referenced sensor IDs from formula
            referenced_ids = set()
            for sid in sensor_values.keys():
                if re.search(r'\b' + re.escape(sid) + r'\b', formula):
                    referenced_ids.add(sid)
            
            # Check if sensor references itself
            if re.search(r'\b' + re.escape(sensor_id) + r'\b', formula):
                print(f"Warning: Circular reference detected in sensor {sensor_id}")
                data[sensor_id] = 0
                calculated_sensors.remove(sensor)
                made_progress = True
                continue
            
            # Check if all dependencies are satisfied
            if referenced_ids.issubset(sensor_values.keys()):
                try:
                    # Replace sensor IDs with their values
                    eval_formula = formula
                    for sid, val in sensor_values.items():
                        eval_formula = re.sub(r'\b' + re.escape(sid) + r'\b', str(val), eval_formula)
                    
                    # Replace ^ with ** for power operation
                    eval_formula = eval_formula.replace('^', '**')
                    
                    # Create safe math context with common functions
                    safe_math = {
                        'sqrt': math.sqrt,
                        'pow': math.pow,
                        'abs': abs,
                        'sin': math.sin,
                        'cos': math.cos,
                        'tan': math.tan,
                        'log': math.log,
                        'log10': math.log10,
                        'exp': math.exp,
                        'pi': math.pi,
                        'e': math.e,
                        '__builtins__': {}  # Restrict access to built-in functions
                    }
                    
                    # Evaluate formula with safe math functions
                    result = eval(eval_formula, safe_math)
                    
                    # Check for invalid results
                    if result is None or (isinstance(result, float) and (result != result or abs(result) == float('inf'))):
                        print(f"Warning: Invalid result for sensor {sensor_id}: {result}")
                        data[sensor_id] = 0
                    else:
                        data[sensor_id] = float(result)
                        sensor_values[sensor_id] = float(result)  # Make available for other calculated sensors
                    
                    calculated_sensors.remove(sensor)
                    evaluated.add(sensor_id)
                    made_progress = True
                    
                except ZeroDivisionError:
                    print(f"Warning: Division by zero in sensor {sensor_id}")
                    data[sensor_id] = 0
                    calculated_sensors.remove(sensor)
                    made_progress = True
                except (ValueError, SyntaxError, NameError) as e:
                    print(f"Warning: Error evaluating formula for sensor {sensor_id}: {e}")
                    data[sensor_id] = 0
                    calculated_sensors.remove(sensor)
                    made_progress = True
                except Exception as e:
                    print(f"Warning: Unexpected error for sensor {sensor_id}: {e}")
                    data[sensor_id] = 0
                    calculated_sensors.remove(sensor)
                    made_progress = True
        
        # If no progress was made, we have circular dependencies
        if not made_progress:
            for sensor in calculated_sensors:
                print(f"Warning: Circular dependency or missing reference for sensor {sensor['id']}")
                data[sensor['id']] = 0
            break
    
    # Second pass: Process force balance sensors (depend on HX711 readings)
    force_balance_sensors = [s for s in sensors if s.get('enabled', True) and s['type'] in ['force_balance_lift', 'force_balance_drag']]
    
    for sensor in force_balance_sensors:
        sensor_id = sensor['id']
        sensor_type = sensor['type']
        
        print(f"Processing force balance sensor: {sensor_id} ({sensor_type})")
        
        # Initialize if needed
        if sensor_id not in sensor_instances:
            print(f"Initializing force balance sensor: {sensor_id}")
            handler = SENSOR_HANDLERS[sensor_type]
            instance = handler['init'](sensor.get('config', {}))
            sensor_instances[sensor_id] = instance
        
        # Read value
        if sensor_id in sensor_instances and sensor_instances[sensor_id] is not None:
            handler = SENSOR_HANDLERS[sensor_type]
            value = handler['read'](sensor_instances[sensor_id], sensor.get('config', {}))
            sensor_values[sensor_id] = value
            sensor_last_values[sensor_id] = value
            data[sensor_id] = value
        else:
            # Failed to initialize
            data[sensor_id] = 0.0
    
    return data

def get_directory_size_mb(directory):
    """Calculate total size of directory in MB."""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(directory):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            if os.path.exists(filepath):
                total_size += os.path.getsize(filepath)
    return total_size / (1024 * 1024)

# CSV LOGGING FUNCTIONS - DISABLED (using SQLite database instead)
# def cleanup_old_logs():
#     """
#     Remove old log files to prevent disk space issues.
#     Deletes oldest files first when limits are exceeded.
#     """
#     try:
#         if not os.path.exists(DATA_LOG_DIR):
#             return
#         
#         # Get all log files with their modification times
#         log_files = []
#         for filename in os.listdir(DATA_LOG_DIR):
#             if filename.endswith('.csv'):
#                 filepath = os.path.join(DATA_LOG_DIR, filename)
#                 if filepath != current_log_file:  # Don't delete current log
#                     log_files.append({
#                         'path': filepath,
#                         'size': os.path.getsize(filepath),
#                         'mtime': os.path.getmtime(filepath)
#                     })
#         
#         # Sort by modification time (oldest first)
#         log_files.sort(key=lambda x: x['mtime'])
#         
#         # Check if we exceed file count limit
#         while len(log_files) >= MAX_LOG_FILES:
#             oldest = log_files.pop(0)
#             print(f"Deleting old log file (file count limit): {os.path.basename(oldest['path'])}")
#             os.remove(oldest['path'])
#         
#         # Check if we exceed total size limit
#         total_size_mb = sum(f['size'] for f in log_files) / (1024 * 1024)
#         if current_log_file:
#             total_size_mb += os.path.getsize(current_log_file) / (1024 * 1024)
#         
#         while total_size_mb > MAX_TOTAL_LOG_SIZE_MB and log_files:
#             oldest = log_files.pop(0)
#             file_size_mb = oldest['size'] / (1024 * 1024)
#             print(f"Deleting old log file (size limit): {os.path.basename(oldest['path'])} ({file_size_mb:.2f} MB)")
#             os.remove(oldest['path'])
#             total_size_mb -= file_size_mb
#         
#         print(f"Log cleanup complete. Total log size: {total_size_mb:.2f} MB, File count: {len(log_files) + (1 if current_log_file else 0)}")
#     
#     except Exception as e:
#         print(f"Error during log cleanup: {e}")

# def rotate_log_file():
#     """
#     Rotate to a new log file.
#     Called when current file exceeds size limit.
#     """
#     global current_log_file, log_session_start, log_rows_written
#     
#     print(f"Rotating log file (size limit reached)...")
#     
#     # Close current log (already closed in append mode)
#     old_file = current_log_file
#     
#     # Create new log file
#     log_session_start = datetime.now()
#     filename = f"windtunnel_{log_session_start.strftime('%Y%m%d_%H%M%S')}.csv"
#     current_log_file = os.path.join(DATA_LOG_DIR, filename)
#     log_rows_written = 0
#     
#     # Write header
#     sensors = current_settings.get('sensors', [])
#     if not sensors or len(sensors) == 0:
#         sensors = DEFAULT_SENSORS
#     headers = ['timestamp', 'datetime'] + [s['id'] for s in sensors if s.get('enabled', True)]
#     
#     with open(current_log_file, 'w', newline='') as f:
#         writer = csv.writer(f)
#         writer.writerow(headers)
#     
#     print(f"Rotated to new log file: {current_log_file}")
#     
#     # Cleanup old logs
#     cleanup_old_logs()

# def log_data_to_csv(data):
#     """
#     Log data to CSV file if logging is enabled.
#     Automatically rotates files when size limit is reached.
#     All data logged in SI units.
#     """
#     global current_log_file, log_session_start, log_rows_written
#     
#     if not current_settings.get('dataLogging', False):
#         return
#     
#     # Create new log file if needed
#     if current_log_file is None:
#         log_session_start = datetime.now()
#         filename = f"windtunnel_{log_session_start.strftime('%Y%m%d_%H%M%S')}.csv"
#         current_log_file = os.path.join(DATA_LOG_DIR, filename)
#         log_rows_written = 0
#         
#         # Write header
#         sensors = current_settings.get('sensors', [])
#         if not sensors or len(sensors) == 0:
#             sensors = DEFAULT_SENSORS
#         headers = ['timestamp', 'datetime'] + [s['id'] for s in sensors if s.get('enabled', True)]
#         
#         with open(current_log_file, 'w', newline='') as f:
#             writer = csv.writer(f)
#             writer.writerow(headers)
#         
#         print(f"Started logging to {current_log_file}")
#         
#         # Run cleanup on startup
#         cleanup_old_logs()
#     
#     # Check if file size exceeds limit (check every 100 rows for performance)
#     if log_rows_written % 100 == 0 and os.path.exists(current_log_file):
#         file_size_mb = os.path.getsize(current_log_file) / (1024 * 1024)
#         if file_size_mb > MAX_LOG_FILE_SIZE_MB:
#             rotate_log_file()
#     
#     # Append data
#     try:
#         sensors = current_settings.get('sensors', [])
#         if not sensors or len(sensors) == 0:
#             sensors = DEFAULT_SENSORS
#         enabled_sensor_ids = [s['id'] for s in sensors if s.get('enabled', True)]
#         
#         row = [
#             data.get('timestamp', time.time()),
#             datetime.fromtimestamp(data.get('timestamp', time.time())).isoformat()
#         ]
#         row.extend([data.get(sid, '') for sid in enabled_sensor_ids])
#         
#         with open(current_log_file, 'a', newline='') as f:
#             writer = csv.writer(f)
#             writer.writerow(row)
#         
#         log_rows_written += 1
#     except Exception as e:
#         print(f"Error logging data: {e}")

# def stop_logging():
#     """Stop current logging session."""
#     global current_log_file, log_session_start, log_rows_written
#     if current_log_file:
#         print(f"Stopped logging to {current_log_file} ({log_rows_written} rows written)")
#     current_log_file = None
#     log_session_start = None
#     log_rows_written = 0
# END CSV LOGGING FUNCTIONS

def background_data_updater():
    """
    Background thread to send data updates to all connected clients.
    Fixed at 200ms (5Hz) intervals for database consistency.
    All data transmitted in SI units.
    
    Note: Database stores full resolution from all sensors (up to 200Hz from UDP).
    UI receives decimated updates at 10Hz for smooth performance.
    """
    global current_log_file
    
    last_db_flush = time.time()
    last_cleanup = time.time()
    last_ui_update = time.time()
    DB_FLUSH_INTERVAL = 5  # Flush database writes every 5 seconds (handles bursts better)
    CLEANUP_INTERVAL = 3600  # Cleanup old data every hour
    UI_UPDATE_INTERVAL = 0.1  # Send to UI at 10Hz (100ms)
    
    while True:
        data = generate_mock_data()
        timestamp = data.get('timestamp', time.time())
        
        # Write to database (queued for batch processing) - always
        write_sensor_data_to_db(timestamp, data)
        
        # Send to connected clients via WebSocket - decimated to 10Hz
        current_time = time.time()
        if current_time - last_ui_update >= UI_UPDATE_INTERVAL:
            socketio.emit('data_update', data)
            last_ui_update = current_time
        
        # Periodically flush database writes
        if current_time - last_db_flush >= DB_FLUSH_INTERVAL:
            flush_db_write_queue()
            last_db_flush = current_time
        
        # Periodically cleanup old data
        if current_time - last_cleanup >= CLEANUP_INTERVAL:
            cleanup_old_data()
            last_cleanup = current_time
        
        # Fixed update interval (200ms = 5Hz for hardware sensors)
        time.sleep(UPDATE_INTERVAL_MS / 1000)

@app.route('/')
def index():
    """Main control screen page."""
    return render_template('index.html')

@app.route('/settings')
def settings():
    """Settings page."""
    return render_template('settings.html')

@app.route('/esp32-code')
def esp32_code():
    """Serve the ESP32 code template."""
    try:
        with open('esp32_sensor_template.ino', 'r') as f:
            code = f.read()
        return Response(code, mimetype='text/plain', headers={
            'Content-Disposition': 'attachment; filename=esp32_sensor_template.ino'
        })
    except Exception as e:
        return f"Error loading ESP32 code: {str(e)}", 500

@app.route('/api/sensor-types', methods=['GET'])
def get_sensor_types():
    """Get available sensor types and their configuration requirements."""
    # Add availability information to sensor types
    sensor_types_with_availability = {}
    developer_mode = current_settings.get('developerMode', False)
    
    for type_id, type_info in SENSOR_TYPES.items():
        sensor_types_with_availability[type_id] = type_info.copy()
        
        # In developer mode, all sensors are available
        if developer_mode:
            sensor_types_with_availability[type_id]['available'] = True
        # Hide mock sensor in production mode
        elif type_id == 'mock':
            sensor_types_with_availability[type_id]['available'] = False
        # Mark hardware sensors based on library presence
        elif type_info.get('category') == 'hardware':
            sensor_types_with_availability[type_id]['available'] = available_sensor_libraries.get(type_id, False)
        else:
            # Calculated sensors always available
            sensor_types_with_availability[type_id]['available'] = True
    
    return jsonify(sensor_types_with_availability)

@app.route('/api/sensors', methods=['GET'])
def get_sensors():
    """Get configured sensors."""
    sensors = current_settings.get('sensors', [])
    if not sensors or len(sensors) == 0:
        sensors = DEFAULT_SENSORS
        # Also update current_settings to use defaults
        current_settings['sensors'] = DEFAULT_SENSORS
        save_settings_to_file(current_settings)
    return jsonify(sensors)

@app.route('/api/gpio/available-pins', methods=['GET'])
def get_available_pins():
    """
    Get available GPIO pins for a sensor, considering:
    - Pin capabilities (GPIO, I2C, SPI, etc.)
    - Currently occupied pins by other sensors
    - Sensor type requirements
    
    Query params:
    - sensor_type: Type of sensor (e.g., 'HX711', 'DHT22')
    - current_sensor_id: ID of sensor being edited (to exclude its own pins)
    - pin_field: Specific field being selected (e.g., 'dout_pin', 'pd_sck_pin')
    """
    from flask import request
    
    sensor_type = request.args.get('sensor_type')
    current_sensor_id = request.args.get('current_sensor_id')
    pin_field = request.args.get('pin_field', '')
    
    # Raspberry Pi GPIO pin mappings (BCM GPIO numbers)
    # Define all pins with their capabilities
    gpio_pins = {
        # GPIO number: {physical_pin, capabilities, description}
        2: {'physical': 3, 'caps': ['I2C'], 'desc': 'I2C1 SDA'},
        3: {'physical': 5, 'caps': ['I2C'], 'desc': 'I2C1 SCL'},
        4: {'physical': 7, 'caps': ['GPIO', 'GPCLK0'], 'desc': 'GPIO'},
        5: {'physical': 29, 'caps': ['GPIO'], 'desc': 'GPIO'},
        6: {'physical': 31, 'caps': ['GPIO'], 'desc': 'GPIO'},
        7: {'physical': 26, 'caps': ['GPIO', 'SPI'], 'desc': 'SPI0 CE1'},
        8: {'physical': 24, 'caps': ['GPIO', 'SPI'], 'desc': 'SPI0 CE0'},
        9: {'physical': 21, 'caps': ['GPIO', 'SPI'], 'desc': 'SPI0 MISO'},
        10: {'physical': 19, 'caps': ['GPIO', 'SPI'], 'desc': 'SPI0 MOSI'},
        11: {'physical': 23, 'caps': ['GPIO', 'SPI'], 'desc': 'SPI0 SCLK'},
        12: {'physical': 32, 'caps': ['GPIO', 'PWM'], 'desc': 'PWM0'},
        13: {'physical': 33, 'caps': ['GPIO', 'PWM'], 'desc': 'PWM1'},
        14: {'physical': 8, 'caps': ['GPIO', 'UART'], 'desc': 'UART TXD'},
        15: {'physical': 10, 'caps': ['GPIO', 'UART'], 'desc': 'UART RXD'},
        16: {'physical': 36, 'caps': ['GPIO'], 'desc': 'GPIO'},
        17: {'physical': 11, 'caps': ['GPIO'], 'desc': 'GPIO'},
        18: {'physical': 12, 'caps': ['GPIO', 'PWM'], 'desc': 'PWM0'},
        19: {'physical': 35, 'caps': ['GPIO', 'PWM', 'SPI'], 'desc': 'SPI1 MISO'},
        20: {'physical': 38, 'caps': ['GPIO', 'SPI'], 'desc': 'SPI1 MOSI'},
        21: {'physical': 40, 'caps': ['GPIO', 'SPI'], 'desc': 'SPI1 SCLK'},
        22: {'physical': 15, 'caps': ['GPIO'], 'desc': 'GPIO'},
        23: {'physical': 16, 'caps': ['GPIO'], 'desc': 'GPIO'},
        24: {'physical': 18, 'caps': ['GPIO'], 'desc': 'GPIO'},
        25: {'physical': 22, 'caps': ['GPIO'], 'desc': 'GPIO'},
        26: {'physical': 37, 'caps': ['GPIO'], 'desc': 'GPIO'},
        27: {'physical': 13, 'caps': ['GPIO'], 'desc': 'GPIO'}
    }
    
    # Get all configured sensors except the current one being edited
    sensors = current_settings.get('sensors', [])
    occupied_pins = {}  # {gpio_num: {'sensor_id', 'sensor_name', 'pin_field', 'shareable'}}
    
    # Reserve fan PWM pin
    fan_pin = fan_state['pwm_pin']
    occupied_pins[fan_pin] = {
        'sensor_id': 'system',
        'sensor_name': 'Fan Control',
        'pin_field': 'PWM Output',
        'shareable': False
    }
    
    for sensor in sensors:
        if sensor.get('id') == current_sensor_id:
            continue  # Skip current sensor being edited
            
        sensor_config = sensor.get('config', {})
        s_type = sensor.get('type')
        s_id = sensor.get('id')
        s_name = sensor.get('name')
        
        # Check which pins this sensor uses
        if s_type == 'HX711':
            dout = sensor_config.get('dout_pin')
            sck = sensor_config.get('pd_sck_pin')
            if dout: occupied_pins[int(dout)] = {'sensor_id': s_id, 'sensor_name': s_name, 'pin_field': 'dout_pin', 'shareable': False}
            if sck: occupied_pins[int(sck)] = {'sensor_id': s_id, 'sensor_name': s_name, 'pin_field': 'pd_sck_pin', 'shareable': False}
        
        elif s_type == 'DHT22':
            pin = sensor_config.get('pin')
            if pin: occupied_pins[int(pin)] = {'sensor_id': s_id, 'sensor_name': s_name, 'pin_field': 'pin', 'shareable': False}
        
        elif s_type in ['ADS1115', 'BMP280', 'SDP811', 'MPU6050', 'XGZP6847A', 'BME280', 'INA219', 'VL53L0X']:
            # I2C sensors - pins 2 and 3 are shareable
            if 2 not in occupied_pins:
                occupied_pins[2] = {'sensor_id': s_id, 'sensor_name': s_name, 'pin_field': 'I2C SDA', 'shareable': True}
            if 3 not in occupied_pins:
                occupied_pins[3] = {'sensor_id': s_id, 'sensor_name': s_name, 'pin_field': 'I2C SCL', 'shareable': True}
        
        elif s_type == 'MCP3008':
            # SPI sensor - shared SPI bus pins (MOSI, MISO, SCLK) + individual CS pin
            # Mark shared SPI pins as occupied but shareable
            for spi_pin in [9, 10, 11]:  # MISO, MOSI, SCLK
                if spi_pin not in occupied_pins:
                    occupied_pins[spi_pin] = {'sensor_id': s_id, 'sensor_name': s_name, 'pin_field': 'SPI Bus', 'shareable': True}
            
            # CS pin is exclusive to this sensor
            cs_pin = sensor_config.get('cs_pin')
            if cs_pin: 
                occupied_pins[int(cs_pin)] = {'sensor_id': s_id, 'sensor_name': s_name, 'pin_field': 'SPI CS', 'shareable': False}
    
    # Determine which pins are available for this sensor type
    available_pins = []
    
    for gpio_num, pin_info in sorted(gpio_pins.items()):
        is_occupied = gpio_num in occupied_pins
        occupation_info = occupied_pins.get(gpio_num, {})
        is_shareable = occupation_info.get('shareable', False)
        
        # Determine if this pin can be used based on sensor type
        can_use = False
        pin_type_label = ''
        
        if sensor_type in ['ADS1115', 'BMP280', 'SDP811', 'MPU6050', 'XGZP6847A', 'BME280', 'INA219', 'VL53L0X']:
            # I2C sensors - only need pins 2 and 3, always shareable
            if gpio_num in [2, 3]:
                can_use = True
                pin_type_label = 'I2C (shared)'
        
        elif sensor_type == 'MCP3008':
            # SPI sensor - different rules for CS pin vs bus pins
            if pin_field == 'cs_pin':
                # CS pin needs exclusive GPIO access (not shared SPI bus pins)
                if 'GPIO' in pin_info['caps']:
                    # Prefer SPI-capable pins for CS, but any GPIO works
                    can_use = True
                    if 'SPI' in pin_info['caps'] and gpio_num in [7, 8]:
                        pin_type_label = 'SPI CS'
                    else:
                        pin_type_label = 'GPIO (for CS)'
            else:
                # For other fields (shouldn't happen, but handle gracefully)
                if 'SPI' in pin_info['caps']:
                    can_use = True
                    pin_type_label = 'SPI'
        
        elif sensor_type in ['HX711', 'DHT22']:
            # GPIO sensors - need exclusive access
            if 'GPIO' in pin_info['caps']:
                if not is_occupied or is_shareable:
                    can_use = True
                pin_type_label = 'GPIO'
        
        else:
            # Default: any GPIO-capable pin
            if 'GPIO' in pin_info['caps']:
                can_use = True
                pin_type_label = 'GPIO'
        
        # Build pin entry
        pin_entry = {
            'gpio': gpio_num,
            'physical': pin_info['physical'],
            'description': pin_info['desc'],
            'capabilities': pin_info['caps'],
            'available': can_use and (not is_occupied or is_shareable),
            'occupied': is_occupied,
            'pin_type': pin_type_label,
            'label': f'GPIO{gpio_num} (Pin {pin_info["physical"]}) - {pin_info["desc"]}'
        }
        
        if is_occupied:
            pin_entry['occupied_by'] = occupation_info.get('sensor_name', 'Unknown')
            pin_entry['occupied_field'] = occupation_info.get('pin_field', '')
            pin_entry['shareable'] = is_shareable
        
        available_pins.append(pin_entry)
    
    return jsonify({
        'pins': available_pins,
        'sensor_type': sensor_type,
        'note': 'I2C and SPI pins can be shared between multiple sensors. GPIO pins require exclusive access.'
    })

@app.route('/api/historical-data', methods=['GET'])
def get_historical_data():
    """
    Get historical sensor data from database.
    Query parameters:
    - sensor: sensor ID to retrieve (required)
    - start_time: Unix timestamp for start (optional, default: 24 hours ago)
    - end_time: Unix timestamp for end (optional, default: now)
    - max_points: Maximum number of points to return (optional, default: 100000)
    """
    from flask import request
    
    try:
        sensor_id = request.args.get('sensor')
        if not sensor_id:
            return jsonify({'status': 'error', 'message': 'sensor parameter required'}), 400
        
        # Default to last 24 hours if not specified
        end_time = float(request.args.get('end_time', time.time()))
        start_time = float(request.args.get('start_time', end_time - 86400))
        max_points = int(request.args.get('max_points', 100000))
        
        # Query database
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT timestamp, value 
            FROM sensor_data 
            WHERE sensor_id = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
        ''', (sensor_id, start_time, end_time))
        
        rows = cursor.fetchall()
        conn.close()
        
        # Convert to list of dicts
        data = [{'timestamp': row[0], 'value': row[1]} for row in rows]
        
        # Downsample if too many points
        if len(data) > max_points:
            step = len(data) // max_points
            data = data[::step]
        
        # Get buffer info
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM sensor_data WHERE sensor_id = ?', (sensor_id,))
        buffer_info = cursor.fetchone()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'sensor': sensor_id,
            'data': data,
            'buffer_start': buffer_info[0] if buffer_info[0] else None,
            'buffer_end': buffer_info[1] if buffer_info[1] else None,
            'total_points': buffer_info[2] if buffer_info[2] else 0,
            'returned_points': len(data)
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get current settings."""
    return jsonify(current_settings)

@app.route('/api/settings', methods=['POST'])
def update_settings():
    """Update settings."""
    from flask import request
    global current_settings, sensor_instances, deleted_udp_sensors
    
    try:
        new_settings = request.get_json()
        
        # Validate and update settings
        if new_settings:
            # Check if sensors changed - if so, cleanup old hardware sensors
            if 'sensors' in new_settings:
                old_sensor_ids = {s.get('id') for s in current_settings.get('sensors', [])}
                new_sensor_ids = {s.get('id') for s in new_settings.get('sensors', [])}
                removed_sensors = old_sensor_ids - new_sensor_ids
                
                # Track deleted UDP sensors to prevent auto-recreation
                for sensor_id in removed_sensors:
                    for sensor in current_settings.get('sensors', []):
                        if sensor.get('id') == sensor_id and sensor.get('type') == 'udp_network':
                            deleted_udp_sensors.add(sensor_id)
                            print(f"Added {sensor_id} to deleted UDP sensors blacklist")
                            break
                
                # Cleanup removed sensors
                for sensor_id in removed_sensors:
                    if sensor_id in sensor_instances:
                        # Find sensor type
                        for sensor in current_settings.get('sensors', []):
                            if sensor.get('id') == sensor_id:
                                sensor_type = sensor.get('type')
                                if sensor_type in SENSOR_HANDLERS:
                                    handler = SENSOR_HANDLERS[sensor_type]
                                    if 'cleanup' in handler:
                                        print(f"Cleaning up sensor: {sensor_id}")
                                        handler['cleanup'](sensor_instances[sensor_id])
                                break
                        del sensor_instances[sensor_id]
            
            # Ensure updateInterval is always 500ms (ignore any client changes)
            new_settings['updateInterval'] = UPDATE_INTERVAL_MS
            
            # Validate numeric fields
            if 'decimalPlaces' in new_settings:
                new_settings['decimalPlaces'] = max(0, min(5, int(new_settings['decimalPlaces'])))
            
            # Update current settings
            current_settings.update(new_settings)
            
            # Save to file
            if save_settings_to_file(current_settings):
                # Emit settings update to all connected clients
                socketio.emit('settings_updated', current_settings)
                return jsonify({'status': 'success', 'message': 'Settings saved successfully'})
            else:
                return jsonify({'status': 'error', 'message': 'Failed to save settings to file'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    
    return jsonify({'status': 'error', 'message': 'Invalid request'}), 400

@app.route('/api/logs/clear', methods=['POST'])
def clear_logs():
    """Clear all sensor data from database."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM sensor_data')
        deleted_rows = cursor.rowcount
        conn.commit()
        conn.close()
        
        print(f"Cleared {deleted_rows} sensor data rows from database")
        return jsonify({
            'status': 'success',
            'message': f'Successfully cleared {deleted_rows} data records from database',
            'deleted_rows': deleted_rows
        })
    except Exception as e:
        print(f"Error clearing database: {e}")
        return jsonify({
            'status': 'error',
            'message': f'Failed to clear logs: {str(e)}'
        }), 500

@app.route('/api/wifi/status', methods=['GET'])
def wifi_status():
    """Get current WiFi connection status and signal strength."""
    try:
        import subprocess
        import re
        import os
        
        # Check if WiFi interface exists - try multiple methods
        wifi_interface = None
        
        # Method 1: Check /sys/class/net for wireless interfaces
        if os.path.exists('/sys/class/net'):
            for iface in os.listdir('/sys/class/net'):
                if os.path.exists(f'/sys/class/net/{iface}/wireless'):
                    wifi_interface = iface
                    break
        
        # Method 2: Try iw dev if interface not found
        if not wifi_interface:
            try:
                iw_result = subprocess.run(['iw', 'dev'], capture_output=True, text=True, timeout=5)
                if iw_result.returncode == 0 and 'Interface' in iw_result.stdout:
                    for line in iw_result.stdout.split('\n'):
                        if 'Interface' in line:
                            wifi_interface = line.split('Interface')[1].strip()
                            break
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                pass
        
        if not wifi_interface:
            return jsonify({'connected': False, 'no_adapter': True, 'message': 'No WiFi adapter found'})
        
        print(f"Checking WiFi status for interface: {wifi_interface}")
        
        # Use iw to get WiFi connection info (more reliable than iwconfig on modern systems)
        result = subprocess.run(['iw', 'dev', wifi_interface, 'info'], 
                              capture_output=True, text=True, timeout=5)
        output = result.stdout
        
        print(f"iw output: {output}")
        
        # Parse WiFi info from iw output
        ssid_match = re.search(r'ssid (.+)', output)
        
        if ssid_match and ssid_match.group(1).strip():
            ssid = ssid_match.group(1).strip()
            
            # Get signal strength from iw station dump
            signal_result = subprocess.run(['iw', 'dev', wifi_interface, 'station', 'dump'], 
                                          capture_output=True, text=True, timeout=5)
            signal_output = signal_result.stdout
            
            # Parse signal level (e.g., "signal: -45 dBm")
            signal_match = re.search(r'signal:\s+(-?\d+)', signal_output)
            signal_level = int(signal_match.group(1)) if signal_match else -100
            
            # Convert signal level to percentage (typical range: -90 to -30 dBm)
            signal_percent = max(0, min(100, (signal_level + 90) * 100 // 60))
            
            print(f"Connected to {ssid}, signal: {signal_level} dBm ({signal_percent}%)")
            
            return jsonify({
                'connected': True,
                'ssid': ssid,
                'signal_level': signal_level,
                'signal_percent': signal_percent
            })
        else:
            return jsonify({'connected': False})
    except Exception as e:
        print(f"Error getting WiFi status: {e}")
        return jsonify({'connected': False, 'error': str(e)})

@app.route('/api/wifi/scan', methods=['GET'])
def wifi_scan():
    """Scan for available WiFi networks."""
    try:
        import subprocess
        import re
        import os
        
        # Check if WiFi interface exists - try multiple methods
        wifi_interface = None
        
        # Method 1: Check /sys/class/net for wireless interfaces
        if os.path.exists('/sys/class/net'):
            for iface in os.listdir('/sys/class/net'):
                if os.path.exists(f'/sys/class/net/{iface}/wireless'):
                    wifi_interface = iface
                    break
        
        # Method 2: Try iw dev if interface not found
        if not wifi_interface:
            try:
                iw_result = subprocess.run(['iw', 'dev'], capture_output=True, text=True, timeout=5)
                if iw_result.returncode == 0 and 'Interface' in iw_result.stdout:
                    # Extract interface name (e.g., wlan0)
                    for line in iw_result.stdout.split('\n'):
                        if 'Interface' in line:
                            wifi_interface = line.split('Interface')[1].strip()
                            break
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                pass
        
        if not wifi_interface:
            return jsonify({'networks': [], 'error': 'No WiFi adapter found on this system'})
        
        print(f"Found WiFi interface: {wifi_interface}")
        
        # Use iwlist to scan for networks
        # If running as root (UID 0), use iwlist directly; otherwise use sudo
        import os as os_module
        if os_module.getuid() == 0:
            # Running as root, no sudo needed
            result = subprocess.run(['/usr/sbin/iwlist', wifi_interface, 'scan'], 
                                  capture_output=True, text=True, timeout=10)
        else:
            # Not root, try with sudo
            result = subprocess.run(['/usr/bin/sudo', '/usr/sbin/iwlist', wifi_interface, 'scan'], 
                                  capture_output=True, text=True, timeout=10)
        output = result.stdout
        
        # Check for permission errors
        if 'Operation not permitted' in output or result.returncode != 0:
            return jsonify({'networks': [], 'error': 'Permission denied. WiFi scanning requires sudo permissions.'})
        
        networks = []
        current_network = {}
        
        for line in output.split('\n'):
            line = line.strip()
            
            # New cell/network
            if 'Cell' in line and 'Address' in line:
                if current_network:
                    networks.append(current_network)
                current_network = {}
            
            # SSID
            elif 'ESSID:' in line:
                match = re.search(r'ESSID:"([^"]*)"', line)
                if match:
                    current_network['ssid'] = match.group(1)
            
            # Signal quality
            elif 'Quality=' in line:
                match = re.search(r'Quality=(\d+)/(\d+)', line)
                if match:
                    quality = int(match.group(1))
                    max_quality = int(match.group(2))
                    current_network['signal_percent'] = (quality * 100) // max_quality
                
                # Signal level in dBm
                signal_match = re.search(r'Signal level=(-?\d+)', line)
                if signal_match:
                    current_network['signal_level'] = int(signal_match.group(1))
            
            # Encryption
            elif 'Encryption key:' in line:
                current_network['encrypted'] = 'on' in line.lower()
            
            # WPA/WPA2
            elif 'WPA' in line:
                current_network['security'] = 'WPA'
        
        # Add last network
        if current_network and 'ssid' in current_network:
            networks.append(current_network)
        
        # Get current connected network
        current_ssid = None
        try:
            iwconfig_result = subprocess.run(['iwconfig', wifi_interface], 
                                            capture_output=True, text=True, timeout=5)
            ssid_match = re.search(r'ESSID:"([^"]*)"', iwconfig_result.stdout)
            if ssid_match and ssid_match.group(1):
                current_ssid = ssid_match.group(1)
        except Exception:
            pass
        
        # Deduplicate networks by SSID, keeping the one with strongest signal
        unique_networks = {}
        for network in networks:
            ssid = network.get('ssid', '')
            if ssid and (ssid not in unique_networks or 
                        network.get('signal_percent', 0) > unique_networks[ssid].get('signal_percent', 0)):
                unique_networks[ssid] = network
                # Mark if this is the current network
                network['is_current'] = (ssid == current_ssid)
        
        # Convert back to list and sort by signal strength
        networks = list(unique_networks.values())
        networks.sort(key=lambda x: x.get('signal_percent', 0), reverse=True)
        
        return jsonify({'networks': networks, 'current_ssid': current_ssid})
    except Exception as e:
        print(f"Error scanning WiFi: {e}")
        return jsonify({'networks': [], 'error': str(e)})

@app.route('/api/wifi/connect', methods=['POST'])
def wifi_connect():
    """Connect to a WiFi network."""
    try:
        import subprocess
        import os as os_module
        data = request.get_json()
        ssid = data.get('ssid')
        password = data.get('password', '')
        
        if not ssid:
            return jsonify({'status': 'error', 'message': 'SSID is required'}), 400
        
        # Check if already connected to this network
        check_result = subprocess.run(['/usr/bin/nmcli', '-t', '-f', 'ACTIVE,SSID', 'dev', 'wifi'], 
                                     capture_output=True, text=True, timeout=5)
        if check_result.returncode == 0:
            for line in check_result.stdout.split('\n'):
                if line.startswith('yes:') and ssid in line:
                    return jsonify({
                        'status': 'success',
                        'message': f'Already connected to {ssid}'
                    })
        
        # Use nmcli to connect (NetworkManager)
        # For WPA/WPA2 networks with password, we need to create a proper connection profile
        if password:
            # Delete existing connection if it exists to avoid conflicts
            subprocess.run(['/usr/bin/nmcli', 'connection', 'delete', ssid], 
                         capture_output=True, text=True, timeout=5)
            
            # Create new connection with proper security settings
            if os_module.getuid() == 0:
                cmd = ['/usr/bin/nmcli', 'connection', 'add', 'type', 'wifi', 
                       'con-name', ssid, 'ifname', 'wlan0', 'ssid', ssid,
                       'wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', password]
            else:
                cmd = ['/usr/bin/sudo', '/usr/bin/nmcli', 'connection', 'add', 'type', 'wifi',
                       'con-name', ssid, 'ifname', 'wlan0', 'ssid', ssid,
                       'wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', password]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return jsonify({
                    'status': 'error',
                    'message': f'Failed to create connection: {result.stderr}'
                }), 500
            
            # Activate the connection
            if os_module.getuid() == 0:
                activate_cmd = ['/usr/bin/nmcli', 'connection', 'up', ssid]
            else:
                activate_cmd = ['/usr/bin/sudo', '/usr/bin/nmcli', 'connection', 'up', ssid]
            
            activate_result = subprocess.run(activate_cmd, capture_output=True, text=True, timeout=30)
            if activate_result.returncode == 0:
                return jsonify({
                    'status': 'success',
                    'message': f'Successfully connected to {ssid}'
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': f'Failed to activate connection: {activate_result.stderr}'
                }), 500
        else:
            # Open network without password
            if os_module.getuid() == 0:
                cmd = ['/usr/bin/nmcli', 'dev', 'wifi', 'connect', ssid]
            else:
                cmd = ['/usr/bin/sudo', '/usr/bin/nmcli', 'dev', 'wifi', 'connect', ssid]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return jsonify({
                    'status': 'success',
                    'message': f'Successfully connected to {ssid}'
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': f'Failed to connect: {result.stderr}'
                }), 500
    except Exception as e:
        print(f"Error connecting to WiFi: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/fan/status', methods=['GET'])
def fan_status():
    """Get current fan status"""
    import time
    last_hb = fan_state.get('last_heartbeat')
    return jsonify({
        'running': fan_state['running'],
        'speed': fan_state['speed'],
        'pwm_pin': fan_state['pwm_pin'],
        'safety_enabled': True,
        'last_heartbeat': last_hb,
        'heartbeat_age': time.time() - last_hb if last_hb else None,
        'safety_timeout': FAN_SAFETY_TIMEOUT
    })

@app.route('/api/fan/start', methods=['POST'])
def fan_start():
    """Start fan at specified speed"""
    try:
        data = request.get_json()
        speed = int(data.get('speed', 50))
        
        if speed < 0 or speed > 100:
            return jsonify({'status': 'error', 'message': 'Speed must be between 0 and 100'}), 400
        
        if set_fan_speed(speed):
            return jsonify({
                'status': 'success',
                'message': f'Fan started at {speed}%',
                'speed': speed,
                'running': True
            })
        else:
            return jsonify({'status': 'error', 'message': 'Failed to start fan'}), 500
    except Exception as e:
        print(f"Error starting fan: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/fan/stop', methods=['POST'])
def fan_stop():
    """Stop fan"""
    try:
        if set_fan_speed(0):
            return jsonify({
                'status': 'success',
                'message': 'Fan stopped',
                'speed': 0,
                'running': False
            })
        else:
            return jsonify({'status': 'error', 'message': 'Failed to stop fan'}), 500
    except Exception as e:
        print(f"Error stopping fan: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/pid/start', methods=['POST'])
def pid_start():
    """Start PID airspeed control"""
    global pid_state
    try:
        data = request.get_json()
        target_airspeed = float(data.get('target_airspeed', 10.0))
        
        if pid_state['enabled']:
            return jsonify({'status': 'error', 'message': 'PID control already running'}), 400
        
        if not pid_state['airspeed_sensor_id']:
            return jsonify({'status': 'error', 'message': 'No airspeed sensor configured'}), 400
        
        # Create PID controller with current parameters
        kp = current_settings.get('pid_kp', 5.0)
        ki = current_settings.get('pid_ki', 0.5)
        kd = current_settings.get('pid_kd', 0.1)
        min_speed = current_settings.get('min_fan_speed', 15.0)
        
        pid_state['controller'] = PIDController(kp=kp, ki=ki, kd=kd, min_output=min_speed, max_output=100.0)
        pid_state['controller'].setpoint = target_airspeed
        pid_state['target_airspeed'] = target_airspeed
        pid_state['enabled'] = True
        
        # Start PID thread if not running
        if pid_state['thread'] is None or not pid_state['thread'].is_alive():
            pid_state['stop_event'] = threading.Event()
            pid_state['thread'] = threading.Thread(target=pid_control_loop, daemon=True)
            pid_state['thread'].start()
        
        logger.info(f"PID control started: target={target_airspeed} m/s, Kp={kp}, Ki={ki}, Kd={kd}")
        
        return jsonify({
            'status': 'success',
            'message': f'PID control started (target: {target_airspeed} m/s)',
            'target_airspeed': target_airspeed,
            'kp': kp,
            'ki': ki,
            'kd': kd
        })
    except Exception as e:
        logger.error(f"Error starting PID control: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/pid/stop', methods=['POST'])
def pid_stop():
    """Stop PID airspeed control"""
    global pid_state
    try:
        pid_state['enabled'] = False
        pid_state['target_airspeed'] = 0.0
        
        # Stop fan
        set_fan_speed(0)
        
        logger.info("PID control stopped")
        
        return jsonify({
            'status': 'success',
            'message': 'PID control stopped'
        })
    except Exception as e:
        logger.error(f"Error stopping PID control: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/pid/status', methods=['GET'])
def pid_status():
    """Get current PID control status"""
    global pid_state
    try:
        response = {
            'status': 'success',
            'running': pid_state['enabled'],
            'enabled': pid_state['enabled'],
            'target_speed': pid_state['target_airspeed'],
            'target_airspeed': pid_state['target_airspeed'],
            'current_speed': pid_state['current_airspeed'],
            'current_airspeed': pid_state['current_airspeed'],
            'fan_speed': pid_state['control_output'],
            'control_output': pid_state['control_output'],
            'sensor_id': pid_state['airspeed_sensor_id'],
            'airspeed_sensor_id': pid_state['airspeed_sensor_id'],
            'min_fan_speed': pid_state['min_fan_speed'],
            'auto_tuning': pid_state.get('auto_tuning', False),
            'auto_tune_cycles': pid_state.get('auto_tune_cycles', 0)
        }
        
        # Add tuned parameters if available
        if pid_state.get('auto_tune_kp') is not None:
            response['kp'] = pid_state['auto_tune_kp']
            response['ki'] = pid_state['auto_tune_ki']
            response['kd'] = pid_state['auto_tune_kd']
        
        return jsonify(response)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/pid/settings', methods=['GET', 'POST'])
def pid_settings():
    """Get or update PID settings"""
    global current_settings, pid_state
    try:
        if request.method == 'GET':
            return jsonify({
                'status': 'success',
                'kp': current_settings.get('pid_kp', 5.0),
                'ki': current_settings.get('pid_ki', 0.5),
                'kd': current_settings.get('pid_kd', 0.1),
                'min_fan_speed': current_settings.get('min_fan_speed', 15.0),
                'airspeed_sensor_id': current_settings.get('airspeed_sensor_id')
            })
        else:  # POST
            data = request.get_json()
            
            if 'kp' in data:
                current_settings['pid_kp'] = float(data['kp'])
            if 'ki' in data:
                current_settings['pid_ki'] = float(data['ki'])
            if 'kd' in data:
                current_settings['pid_kd'] = float(data['kd'])
            if 'min_fan_speed' in data:
                current_settings['min_fan_speed'] = float(data['min_fan_speed'])
                pid_state['min_fan_speed'] = float(data['min_fan_speed'])
            if 'airspeed_sensor_id' in data:
                current_settings['airspeed_sensor_id'] = data['airspeed_sensor_id']
                pid_state['airspeed_sensor_id'] = data['airspeed_sensor_id']
            
            save_settings_to_file(current_settings)
            
            # Update running controller if active
            if pid_state['enabled'] and pid_state['controller']:
                pid_state['controller'].kp = current_settings.get('pid_kp', 5.0)
                pid_state['controller'].ki = current_settings.get('pid_ki', 0.5)
                pid_state['controller'].kd = current_settings.get('pid_kd', 0.1)
                pid_state['controller'].min_output = current_settings.get('min_fan_speed', 15.0)
            
            return jsonify({
                'status': 'success',
                'message': 'PID settings updated'
            })
    except Exception as e:
        logger.error(f"Error with PID settings: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/pid/autotune', methods=['POST'])
def pid_autotune():
    """Start PID auto-tuning using relay method"""
    global pid_state
    try:
        data = request.get_json()
        sensor_id = data.get('sensor_id')
        target_setpoint = data.get('setpoint', 3.5)  # Allow custom setpoint, default 3.5 m/s
        
        if not sensor_id:
            return jsonify({'status': 'error', 'error': 'Sensor ID is required'}), 400
        
        if pid_state['enabled']:
            return jsonify({'status': 'error', 'error': 'Stop PID control before auto-tuning'}), 400
        
        # Start auto-tune process
        pid_state['auto_tuning'] = True
        pid_state['auto_tune_cycles'] = 0
        pid_state['airspeed_sensor_id'] = sensor_id
        pid_state['auto_tune_kp'] = None
        pid_state['auto_tune_ki'] = None
        pid_state['auto_tune_kd'] = None
        
        # Start auto-tune thread
        def auto_tune_thread():
            try:
                logger.info(f"Auto-tune started for sensor {sensor_id}, target setpoint: {target_setpoint} m/s")
                
                # Test multiple setpoints for more robust tuning
                # Use 70%, 85%, and 100% of target setpoint
                test_setpoints = [
                    target_setpoint * 0.7,
                    target_setpoint * 0.85,
                    target_setpoint
                ]
                
                all_periods = []
                all_amplitudes = []
                all_ku_values = []
                
                # Relay auto-tune parameters (configurable)
                relay_amplitude = 20.0  # Fan speed variation (+/- 20%)
                min_cycles = 5  # Minimum cycles per setpoint for accuracy
                max_cycles = 12  # Maximum cycles per setpoint
                timeout_per_setpoint = 150  # 2.5 minutes per setpoint
                min_time_per_setpoint = 30  # Minimum 30 seconds per setpoint
                
                total_cycles_completed = 0
                
                for setpoint_idx, setpoint in enumerate(test_setpoints):
                    if not pid_state.get('auto_tuning'):
                        logger.info("Auto-tune cancelled")
                        set_fan_speed(0)
                        return
                    
                    logger.info(f"===== Auto-tune: Testing setpoint {setpoint:.2f} m/s ({setpoint_idx + 1}/{len(test_setpoints)}) =====")
                    
                    # Calculate base speed for this setpoint (rough estimate)
                    base_speed = min(80.0, max(30.0, setpoint / target_setpoint * 50.0))
                    
                    start_time = time.time()
                    oscillation_periods = []
                    oscillation_amplitudes = []
                    
                    last_airspeed = None
                    crossing_times = []
                    
                    logger.info(f"Auto-tune: Setting fan to base speed {base_speed:.0f}%")
                    set_fan_speed(base_speed)
                    logger.info("Auto-tune: Waiting 5 seconds for system to stabilize...")
                    time.sleep(5)  # Let system stabilize longer
                    logger.info("Auto-tune: Starting oscillation detection")
                    
                    cycle_count = 0
                    samples = []
                    last_log_time = time.time()
                    iteration_count = 0
                    
                    elapsed_time = 0
                    while cycle_count < max_cycles and elapsed_time < timeout_per_setpoint:
                        iteration_count += 1
                        elapsed_time = time.time() - start_time
                        
                        if not pid_state.get('auto_tuning'):
                            logger.info("Auto-tune cancelled by user")
                            set_fan_speed(0)
                            return
                        
                        # Don't allow early exit in first 45 seconds - need time to collect data
                        if elapsed_time > 45 and elapsed_time > min_time_per_setpoint and cycle_count >= min_cycles:
                            logger.info(f"Auto-tune: Collected sufficient data for setpoint {setpoint:.2f} ({cycle_count} cycles in {elapsed_time:.0f}s)")
                            break
                        logger.info("Auto-tune cancelled")
                        set_fan_speed(0)
                        return
                    
                        # Read current airspeed
                        try:
                            conn = sqlite3.connect(DB_FILE)
                            cursor = conn.cursor()
                            cursor.execute("""
                                SELECT value FROM sensor_data 
                                WHERE sensor_id = ? 
                                ORDER BY timestamp DESC 
                                LIMIT 1
                            """, (sensor_id,))
                            result = cursor.fetchone()
                            conn.close()
                            
                            if result:
                                current_airspeed = float(result[0])
                            else:
                                current_airspeed = 0.0
                                logger.warning(f"No sensor data for {sensor_id}")
                        except Exception as e:
                            current_airspeed = 0.0
                            logger.error(f"Error reading sensor: {e}")
                        
                        samples.append(current_airspeed)
                        
                        # Log status periodically
                        if time.time() - last_log_time > 5:
                            logger.info(f"Auto-tune: Elapsed {elapsed_time:.0f}s, Airspeed={current_airspeed:.2f}, Cycles={cycle_count}, Samples={len(samples)}")
                            last_log_time = time.time()
                        
                        # Relay logic: switch fan speed based on error
                        error = setpoint - current_airspeed
                        
                        if error > 0:
                            # Below setpoint, increase fan
                            fan_speed = base_speed + relay_amplitude
                        else:
                            # Above setpoint, decrease fan
                            fan_speed = base_speed - relay_amplitude
                        
                        set_fan_speed(max(15.0, min(100.0, fan_speed)))
                        
                        # Detect zero crossings (when error changes sign)
                        if last_airspeed is not None:
                            last_error = setpoint - last_airspeed
                            
                            if (last_error > 0 and error < 0) or (last_error < 0 and error > 0):
                                crossing_times.append(time.time())
                                logger.debug(f"Zero crossing detected at {elapsed_time:.1f}s")
                                
                                # Calculate period from last two crossings
                                if len(crossing_times) >= 3:
                                    period = crossing_times[-1] - crossing_times[-3]
                                    oscillation_periods.append(period)
                                    
                                    # Calculate amplitude from recent samples
                                    if len(samples) >= 20:
                                        recent_samples = samples[-20:]
                                        amplitude = (max(recent_samples) - min(recent_samples)) / 2
                                        oscillation_amplitudes.append(amplitude)
                                    
                                    cycle_count += 1
                                    total_cycles_completed += 1
                                    pid_state['auto_tune_cycles'] = total_cycles_completed
                                    logger.info(f"Auto-tune cycle {cycle_count} (total {total_cycles_completed}): Period={period:.2f}s, Amplitude={amplitude:.2f} m/s")
                        
                        last_airspeed = current_airspeed
                        time.sleep(0.1)  # Sample at 10 Hz
                    
                    # Log why loop ended
                    logger.info(f"Auto-tune: Loop ended for setpoint {setpoint:.2f} - Iterations: {iteration_count}, Cycles: {cycle_count}, Time: {elapsed_time:.1f}s")
                    
                    # Store results from this setpoint
                    if len(oscillation_periods) > 0 and len(oscillation_amplitudes) > 0:
                        avg_period = sum(oscillation_periods) / len(oscillation_periods)
                        avg_amplitude = sum(oscillation_amplitudes) / len(oscillation_amplitudes)
                        ku = 4.0 * relay_amplitude / (math.pi * avg_amplitude) if avg_amplitude > 0 else 0
                        
                        all_periods.append(avg_period)
                        all_amplitudes.append(avg_amplitude)
                        all_ku_values.append(ku)
                        
                        logger.info(f"Setpoint {setpoint:.2f} results: Ku={ku:.2f}, Tu={avg_period:.2f}s")
                    else:
                        logger.warning(f"No valid oscillation data for setpoint {setpoint:.2f}")
                
                # Stop fan
                logger.info(f"===== Auto-tune finished all setpoints. Stopping fan. =====")
                set_fan_speed(0)
                
                if len(all_ku_values) < 1:
                    logger.error(f"Auto-tune failed: Not enough oscillation data collected")
                    logger.error(f"Completed {total_cycles_completed} cycles across {len(test_setpoints)} setpoints")
                    logger.error(f"Collected Ku values: {len(all_ku_values)}, Periods: {len(all_periods)}")
                    
                    # Provide fallback conservative values if no data collected
                    if total_cycles_completed > 0:
                        logger.info("Using conservative fallback PID values")
                        pid_state['auto_tune_kp'] = 2.0
                        pid_state['auto_tune_ki'] = 0.2
                        pid_state['auto_tune_kd'] = 0.05
                    else:
                        pid_state['auto_tuning'] = False
                        return
                else:
                    # Calculate average system characteristics from all test points
                    avg_ku = sum(all_ku_values) / len(all_ku_values)
                    avg_tu = sum(all_periods) / len(all_periods)
                    
                    logger.info(f"Auto-tune measurements from {len(all_ku_values)} setpoints: Ku={avg_ku:.2f}, Tu={avg_tu:.2f}s")
                    
                    # Ziegler-Nichols PID tuning rules
                    pid_state['auto_tune_kp'] = 0.6 * avg_ku
                    pid_state['auto_tune_ki'] = 1.2 * avg_ku / avg_tu
                    pid_state['auto_tune_kd'] = 0.075 * avg_ku * avg_tu
                
                logger.info(f"Auto-tune complete: Kp={pid_state['auto_tune_kp']:.2f}, Ki={pid_state['auto_tune_ki']:.3f}, Kd={pid_state['auto_tune_kd']:.3f} (from {total_cycles_completed} total cycles)")
                
            except Exception as e:
                logger.error(f"Auto-tune error: {e}")
                set_fan_speed(0)
            finally:
                pid_state['auto_tuning'] = False
                set_fan_speed(0)
        
        thread = Thread(target=auto_tune_thread, daemon=True)
        thread.start()
        
        return jsonify({
            'status': 'started',
            'message': 'Auto-tune started',
            'sensor_id': sensor_id
        })
        
    except Exception as e:
        logger.error(f"Error starting auto-tune: {e}")
        pid_state['auto_tuning'] = False
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/pid/autotune/stop', methods=['POST'])
def pid_autotune_stop():
    """Stop auto-tuning"""
    global pid_state
    try:
        pid_state['auto_tuning'] = False
        set_fan_speed(0)
        logger.info("Auto-tune stopped by user")
        return jsonify({'status': 'success', 'message': 'Auto-tune stopped'})
    except Exception as e:
        logger.error(f"Error stopping auto-tune: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/internet/check', methods=['GET'])
def internet_check():
    """Check internet connectivity."""
    try:
        import socket
        
        # Try to connect to Google's DNS server
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return jsonify({'connected': True, 'message': 'Internet connection active'})
    except socket.error as e:
        print(f"Internet check failed: {e}")
        return jsonify({'connected': False, 'message': 'No internet connection'})
    except Exception as e:
        print(f"Error checking internet: {e}")
        return jsonify({'connected': False, 'message': 'Connection check failed'})

@app.route('/api/export/usb-drives', methods=['GET'])
def list_usb_drives():
    """List available USB drives."""
    try:
        import subprocess
        import re
        
        drives = []
        
        # Try to detect USB drives using different methods based on platform
        try:
            # Linux: Use lsblk to list block devices
            result = subprocess.run(['lsblk', '-o', 'NAME,SIZE,MOUNTPOINT,TYPE', '-J'], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                import json
                devices = json.loads(result.stdout)
                
                for device in devices.get('blockdevices', []):
                    # Look for USB devices that are mounted
                    if device.get('type') == 'part' and device.get('mountpoint'):
                        mountpoint = device['mountpoint']
                        # Check if it's likely a USB drive (not / or /boot)
                        if mountpoint not in ['/', '/boot', '/boot/efi'] and '/media' in mountpoint or '/mnt' in mountpoint:
                            drives.append({
                                'name': device.get('name', 'USB Drive'),
                                'path': mountpoint,
                                'size': device.get('size', 'Unknown')
                            })
        except FileNotFoundError:
            # lsblk not available, try alternative method
            pass
        
        # If no drives found, try looking in common mount points
        if not drives:
            import os
            common_mounts = ['/media', '/mnt']
            for mount_base in common_mounts:
                if os.path.exists(mount_base):
                    for user_dir in os.listdir(mount_base):
                        user_path = os.path.join(mount_base, user_dir)
                        if os.path.isdir(user_path):
                            for drive_dir in os.listdir(user_path):
                                drive_path = os.path.join(user_path, drive_dir)
                                if os.path.isdir(drive_path):
                                    # Try to get size
                                    try:
                                        stat = os.statvfs(drive_path)
                                        size_bytes = stat.f_frsize * stat.f_blocks
                                        size_gb = size_bytes / (1024**3)
                                        size_str = f"{size_gb:.1f} GB"
                                    except:
                                        size_str = "Unknown"
                                    
                                    drives.append({
                                        'name': drive_dir,
                                        'path': drive_path,
                                        'size': size_str
                                    })
        
        return jsonify({'status': 'success', 'drives': drives})
    except Exception as e:
        print(f"Error listing USB drives: {e}")
        return jsonify({'status': 'error', 'message': str(e), 'drives': []}), 500

@app.route('/api/export/data', methods=['POST'])
def export_data():
    """Export sensor data to CSV file on USB drive in wide format (one column per sensor)."""
    try:
        data = request.get_json()
        drive_path = data.get('drive_path')
        time_range = data.get('time_range', 'all')  # 'all', 'last_minutes', 'last_hours', 'date_range'
        time_value = data.get('time_value', 60)  # Number of minutes/hours
        start_time = data.get('start_time')  # For date_range
        end_time = data.get('end_time')  # For date_range
        
        if not drive_path:
            return jsonify({'status': 'error', 'message': 'Drive path is required'}), 400
        
        # Create filename with timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'windtunnel_data_{timestamp}.csv'
        filepath = os.path.join(drive_path, filename)
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Build time filter query
        time_filter_sql = ''
        time_filter_params = []
        current_time = time.time()
        
        if time_range == 'last_minutes':
            cutoff_time = current_time - (int(time_value) * 60)
            time_filter_sql = 'WHERE timestamp >= ?'
            time_filter_params = [cutoff_time]
        elif time_range == 'last_hours':
            cutoff_time = current_time - (int(time_value) * 3600)
            time_filter_sql = 'WHERE timestamp >= ?'
            time_filter_params = [cutoff_time]
        elif time_range == 'date_range' and start_time and end_time:
            time_filter_sql = 'WHERE timestamp BETWEEN ? AND ?'
            time_filter_params = [float(start_time), float(end_time)]
        # else: time_range == 'all', no filter
        
        # Get all unique sensor IDs
        cursor.execute(f'SELECT DISTINCT sensor_id FROM sensor_data {time_filter_sql} ORDER BY sensor_id', time_filter_params)
        sensor_ids = [row[0] for row in cursor.fetchall()]
        
        if not sensor_ids:
            conn.close()
            return jsonify({'status': 'error', 'message': 'No data to export'}), 400
        
        # Get all unique timestamps in range
        cursor.execute(f'SELECT DISTINCT timestamp FROM sensor_data {time_filter_sql} ORDER BY timestamp', time_filter_params)
        timestamps = [row[0] for row in cursor.fetchall()]
        total_timestamps = len(timestamps)
        
        if total_timestamps == 0:
            conn.close()
            return jsonify({'status': 'error', 'message': 'No data in selected time range'}), 400
        
        # Write CSV with wide format
        with open(filepath, 'w', newline='') as csvfile:
            csv_writer = csv.writer(csvfile)
            
            # Write header: Timestamp, Sensor1, Sensor2, ...
            csv_writer.writerow(['Timestamp'] + sensor_ids)
            
            # Process in chunks to handle large datasets
            chunk_size = 1000
            total_rows = 0
            
            for i in range(0, len(timestamps), chunk_size):
                chunk_timestamps = timestamps[i:i + chunk_size]
                
                # Get all data for this chunk of timestamps
                placeholders = ','.join('?' * len(chunk_timestamps))
                cursor.execute(f'''
                    SELECT timestamp, sensor_id, value 
                    FROM sensor_data 
                    WHERE timestamp IN ({placeholders})
                    ORDER BY timestamp, sensor_id
                ''', chunk_timestamps)
                
                # Build data structure: {timestamp: {sensor_id: value}}
                data_by_timestamp = {}
                for ts, sensor_id, value in cursor.fetchall():
                    if ts not in data_by_timestamp:
                        data_by_timestamp[ts] = {}
                    data_by_timestamp[ts][sensor_id] = value
                
                # Write rows for this chunk
                for ts in chunk_timestamps:
                    row = [ts]
                    sensor_data = data_by_timestamp.get(ts, {})
                    for sensor_id in sensor_ids:
                        row.append(sensor_data.get(sensor_id, ''))  # Empty string if no data
                    csv_writer.writerow(row)
                    total_rows += 1
                
                # Emit progress update
                progress = int((total_rows / total_timestamps) * 100)
                socketio.emit('export_progress', {
                    'progress': progress,
                    'current': total_rows,
                    'total': total_timestamps
                })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Data exported successfully',
            'filename': filename,
            'filepath': filepath,
            'rows_exported': total_rows,
            'columns': len(sensor_ids) + 1  # +1 for timestamp column
        })
    except PermissionError:
        return jsonify({'status': 'error', 'message': 'Permission denied. Drive may be read-only.'}), 500
    except Exception as e:
        print(f"Error exporting data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/settings/reset', methods=['POST'])
def reset_settings():
    """Reset settings to defaults."""
    global current_settings
    
    current_settings = DEFAULT_SETTINGS.copy()
    
    if save_settings_to_file(current_settings):
        # Emit settings update to all connected clients
        socketio.emit('settings_updated', current_settings)
        return jsonify({'status': 'success', 'message': 'Settings reset to defaults', 'settings': current_settings})
    else:
        return jsonify({'status': 'error', 'message': 'Failed to save settings'}), 500

@app.route('/api/test-sensor', methods=['POST'])
def test_sensor():
    """Test hardware sensor connection and initialization."""
    try:
        data = request.get_json()
        sensor_type = data.get('sensor_type')
        sensor_id = data.get('sensor_id')  # Optional: ID of sensor being tested
        config = data.get('config', {})
        
        print(f"[TEST-SENSOR] Testing sensor type: {sensor_type}")
        print(f"[TEST-SENSOR] Config: {config}")
        
        if not sensor_type:
            return jsonify({'status': 'error', 'message': 'sensor_type is required'}), 400
        
        # Check if this sensor is already running (for GPIO-based sensors)
        if sensor_id and sensor_id in sensor_instances:
            # Sensor already initialized and running - check if it's providing data
            last_value = sensor_last_values.get(sensor_id, None)
            
            if last_value is not None:
                # Sensor is providing data
                return jsonify({
                    'status': 'success',
                    'message': f'✓ Sensor is already running! Last reading: {last_value:.2f}. Check the dashboard for live data.',
                    'value': last_value,
                    'hardware_detected': True,
                    'already_running': True
                })
            else:
                # Sensor initialized but no data yet (might be starting up)
                return jsonify({
                    'status': 'success',
                    'message': '✓ Sensor is initialized and starting up. Check the dashboard in a moment for live readings.',
                    'hardware_detected': True,
                    'already_running': True
                })
        
        # Check if library is available
        if sensor_type in available_sensor_libraries and not available_sensor_libraries[sensor_type]:
            return jsonify({
                'status': 'error',
                'message': f'Library for {sensor_type} is not installed. Please run the sensor library installation.'
            }), 400
        
        if sensor_type not in SENSOR_HANDLERS:
            return jsonify({'status': 'error', 'message': f'Unknown sensor type: {sensor_type}'}), 400
        
        handler = SENSOR_HANDLERS[sensor_type]
        
        # Try to initialize the sensor
        sensor_instance = None
        try:
            print(f"[TEST-SENSOR] Calling init handler for {sensor_type}...")
            sensor_instance = handler['init'](config)
            print(f"[TEST-SENSOR] Init returned: {sensor_instance}")
            
            # Try to read a value
            print(f"[TEST-SENSOR] Calling read handler for {sensor_type}...")
            value = handler['read'](sensor_instance, config)
            print(f"[TEST-SENSOR] Read returned: {value}")
            
            # Check if sensor initialized successfully
            # For HX711, None means initialization failed
            if sensor_instance is None:
                return jsonify({
                    'status': 'error',
                    'message': '✗ Failed to initialize sensor. Check wiring and configuration.',
                    'hardware_detected': False
                })
            
            # Clean up sensor after test
            if 'cleanup' in handler and sensor_instance is not None:
                print(f"[TEST-SENSOR] Cleaning up {sensor_type}...")
                handler['cleanup'](sensor_instance)
            
            # Hardware detected if sensor initialized (even if reading is 0)
            # HX711 can legitimately read 0 when no load applied
            return jsonify({
                'status': 'success',
                'message': f'✓ Sensor connected and working! Current reading: {value:.2f}',
                'value': value,
                'hardware_detected': True
            })
        except Exception as init_error:
            # Clean up on error
            if 'cleanup' in handler and sensor_instance is not None:
                try:
                    handler['cleanup'](sensor_instance)
                except:
                    pass
            
            print(f"[TEST-SENSOR] Exception during init/read: {init_error}")
            import traceback
            traceback.print_exc()
            return jsonify({
                'status': 'error',
                'message': f'Failed to initialize sensor: {str(init_error)}'
            }), 500
            
    except Exception as e:
        print(f"[TEST-SENSOR] Exception in test_sensor: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'error',
            'message': f'Test failed: {str(e)}'
        }), 500

@app.route('/api/udp/discover', methods=['GET'])
def discover_udp_devices():
    """Get list of discovered ESP32 devices broadcasting announcements."""
    try:
        with discovery_lock:
            current_time = time.time()
            devices = []
            
            logger.info(f"API /api/udp/discover called - discovered_devices has {len(discovered_devices)} entries")
            logger.info(f"discovered_devices dict id: {id(discovered_devices)}")
            
            for dev_id, dev in discovered_devices.items():
                age = current_time - dev['last_seen']
                devices.append({
                    'device_id': dev_id,
                    'sensor_id': dev['sensor_id'],
                    'ip': dev['ip'],
                    'mac': dev['mac'],
                    'sensor_type': dev['sensor_type'],
                    'firmware': dev['firmware'],
                    'multi_value': dev.get('multi_value', False),
                    'sensor_keys': dev.get('sensor_keys', ['value']),
                    'last_seen': age,
                    'is_stale': age > 10  # Mark as stale if not seen in 10s
                })
            
            # Sort by most recently seen
            devices.sort(key=lambda x: x['last_seen'])
            
            logger.info(f"Discovery API called - found {len(devices)} devices")
            
            return jsonify({
                'status': 'success',
                'devices': devices,
                'count': len(devices)
            })
            
    except Exception as e:
        logger.error(f"Error in discover_udp_devices: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/udp/configure-device', methods=['POST'])
def configure_udp_device():
    """Send configuration to ESP32 device via HTTP."""
    try:
        data = request.json
        device_ip = data.get('device_ip')
        target_port = data.get('target_port', 5000)
        sensor_rate = data.get('sensor_rate', 1000)
        new_sensor_id = data.get('sensor_id')  # Optional rename
        
        if not device_ip:
            return jsonify({'status': 'error', 'error': 'device_ip is required'}), 400
        
        # Get Raspberry Pi's local IP (best effort)
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            raspberry_ip = s.getsockname()[0]
        except:
            raspberry_ip = '0.0.0.0'
        finally:
            s.close()
        
        # Configure the ESP32
        result = configure_esp32_sensor(
            device_ip,
            raspberry_ip,
            target_port,
            sensor_rate,
            new_sensor_id
        )
        
        if result.get('status') == 'success':
            return jsonify(result)
        else:
            return jsonify(result), 500
            
    except Exception as e:
        logger.error(f"Error configuring UDP device: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/udp/start-device', methods=['POST'])
def start_udp_device():
    """Tell ESP32 device to start sending data."""
    try:
        data = request.json
        device_ip = data.get('device_ip')
        
        if not device_ip:
            return jsonify({'status': 'error', 'error': 'device_ip is required'}), 400
        
        result = start_esp32_sensor(device_ip)
        
        if result.get('status') == 'success':
            return jsonify(result)
        else:
            return jsonify(result), 500
            
    except Exception as e:
        logger.error(f"Error starting UDP device: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/udp/stop-device', methods=['POST'])
def stop_udp_device():
    """Tell ESP32 device to stop sending data."""
    try:
        data = request.json
        device_ip = data.get('device_ip')
        
        if not device_ip:
            return jsonify({'status': 'error', 'error': 'device_ip is required'}), 400
        
        result = stop_esp32_sensor(device_ip)
        
        if result.get('status') == 'success':
            return jsonify(result)
        else:
            return jsonify(result), 500
            
    except Exception as e:
        logger.error(f"Error stopping UDP device: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/udp/device-status', methods=['POST'])
def get_udp_device_status():
    """Get status from ESP32 device via HTTP."""
    try:
        data = request.json
        device_ip = data.get('device_ip')
        
        if not device_ip:
            return jsonify({'status': 'error', 'error': 'device_ip is required'}), 400
        
        result = get_esp32_status(device_ip)
        
        if result.get('status') == 'success':
            return jsonify(result)
        else:
            return jsonify(result), 500
            
    except Exception as e:
        logger.error(f"Error getting device status: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/sensor-status/<sensor_id>', methods=['GET'])

def get_sensor_status(sensor_id):
    """Get current status of a sensor (connected/disconnected based on whether it's running and has data)."""
    try:
        # Check if sensor is initialized and running
        if sensor_id in sensor_instances:
            # Check if it has recent data
            last_value = sensor_last_values.get(sensor_id, None)
            
            if last_value is not None:
                return jsonify({
                    'status': 'connected',
                    'message': 'Sensor is running and providing data',
                    'last_value': last_value
                })
            else:
                return jsonify({
                    'status': 'connected',
                    'message': 'Sensor is initialized',
                    'last_value': None
                })
        else:
            return jsonify({
                'status': 'disconnected',
                'message': 'Sensor not initialized'
            })
    except Exception as e:
        return jsonify({
            'status': 'unknown',
            'message': f'Error checking status: {str(e)}'
        }), 500

@app.route('/api/udp/devices', methods=['GET'])
def get_udp_devices():
    """Get list of all UDP devices currently sending data"""
    try:
        import time
        current_time = time.time()
        devices = []
        
        for sensor_id, data in udp_sensor_data.items():
            age = current_time - data['timestamp']
            devices.append({
                'sensor_id': sensor_id,
                'value': data['value'],
                'port': data['port'],
                'source_ip': data['source_ip'],
                'last_seen': age,
                'is_stale': age > 5  # Mark as stale if > 5 seconds
            })
        
        # Sort by most recently seen
        devices.sort(key=lambda x: x['last_seen'])
        
        return jsonify({
            'devices': devices,
            'count': len(devices),
            'active_ports': list(udp_listeners.keys())
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/udp/setup-device', methods=['POST'])
def setup_device_wizard():
    """Setup wizard endpoint - configure ESP32 and create sensors"""
    try:
        data = request.json
        device_ip = data.get('device_ip')
        device_id = data.get('device_id')
        sensor_keys = data.get('sensor_keys', ['value'])
        sensor_configs = data.get('sensor_configs', [])  # [{key, name, color, visible}]
        port = data.get('port', 5000)
        rate = data.get('rate', 1000)
        
        logger.info(f"Wizard received: device_ip={device_ip}, device_id={device_id}, sensor_keys={sensor_keys}")
        
        if not device_ip or not device_id:
            return jsonify({'error': 'Missing device_ip or device_id'}), 400
        
        # Get the Raspberry Pi's local IP
        raspberry_pi_ip = get_local_ip()
        logger.info(f"Setting up ESP32 {device_id} at {device_ip}, will send data to {raspberry_pi_ip}:{port}")
        
        # Step 1: Configure the ESP32
        config_result = configure_esp32_sensor(
            device_ip, 
            raspberry_pi_ip, 
            port, 
            rate,
            device_id
        )
        
        if config_result.get('status') != 'success':
            return jsonify({'error': 'Failed to configure ESP32', 'details': config_result}), 500
        
        # Step 2: Create sensor configurations
        sensors = current_settings.get('sensors', [])
        created_sensors = []
        
        for config in sensor_configs:
            key = config.get('key')
            if not key:
                continue
                
            # Create sensor ID (composite if multi-value)
            if len(sensor_keys) > 1:
                sensor_id = f"{device_id}_{key}"
            else:
                sensor_id = device_id
            
            # Check if already exists
            if any(s.get('id') == sensor_id for s in sensors):
                continue
            
            # Determine unit based on sensor key
            unit_map = {
                'lift': 'N',
                'drag': 'N',
                'temp': '°C',
                'temperature': '°C',
                'force': 'N',
                'pressure': 'Pa',
                'humidity': '%'
            }
            unit = unit_map.get(key.lower(), '')
            
            # Create sensor
            new_sensor = {
                'id': sensor_id,
                'name': config.get('name', f'{device_id} - {key}'),
                'type': 'udp_network',
                'enabled': config.get('visible', True),
                'unit': unit,
                'config': {
                    'udp_port': port,
                    'sensor_id': sensor_id,
                    'timeout': 5
                },
                'chart_color': config.get('color', generate_random_color())
            }
            
            sensors.append(new_sensor)
            created_sensors.append(new_sensor)
        
        # Save updated sensors
        current_settings['sensors'] = sensors
        save_settings_to_file(current_settings)
        
        # Step 3: Start data transmission
        start_result = start_esp32_sensor(device_ip)
        
        # Emit updates
        for sensor in created_sensors:
            socketio.emit('sensor_added', sensor)
        
        return jsonify({
            'status': 'success',
            'message': f'Created {len(created_sensors)} sensors',
            'sensors': created_sensors,
            'esp32_status': start_result
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def generate_random_color():
    """Generate a random color for sensor charts"""
    import random
    colors = [
        '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF',
        '#FF9F40', '#FF6384', '#C9CBCF', '#4BC0C0', '#FF9F40'
    ]
    return random.choice(colors)

@app.route('/api/refresh-sensor-libraries', methods=['POST'])
def refresh_sensor_libraries():
    """Recheck which sensor libraries are available (call after installation)."""
    check_sensor_library_availability()
    return jsonify({
        'status': 'success',
        'libraries': available_sensor_libraries
    })

# Force Balance Calibration Endpoints
@app.route('/api/sensor/<sensor_id>/calibration', methods=['GET'])
def get_calibration(sensor_id):
    """Get current calibration data for a force balance sensor"""
    try:
        sensors = current_settings.get('sensors', [])
        sensor = next((s for s in sensors if s['id'] == sensor_id), None)
        
        if not sensor:
            return jsonify({'error': 'Sensor not found'}), 404
        
        if sensor['type'] not in ['force_balance_lift', 'force_balance_drag']:
            return jsonify({'error': 'Not a force balance sensor'}), 400
        
        calibration = sensor.get('config', {}).get('calibration', {
            'is_calibrated': False,
            'tare_offsets': [0, 0, 0],
            'calibration_factor': 1.0
        })
        
        return jsonify(calibration)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sensor/<sensor_id>/calibration/start', methods=['POST'])
def start_calibration(sensor_id):
    """Start a new calibration session"""
    try:
        sensors = current_settings.get('sensors', [])
        sensor = next((s for s in sensors if s['id'] == sensor_id), None)
        
        if not sensor:
            return jsonify({'error': 'Sensor not found'}), 404
        
        if sensor['type'] not in ['force_balance_lift', 'force_balance_drag']:
            return jsonify({'error': 'Not a force balance sensor'}), 400
        
        # Initialize empty calibration session
        return jsonify({
            'status': 'ready',
            'message': 'Calibration session started',
            'step': 'tare'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sensor/<sensor_id>/calibration/tare', methods=['POST'])
def capture_tare(sensor_id):
    """Capture tare readings (average of N samples)"""
    try:
        data = request.get_json()
        num_samples = data.get('num_samples', 50)
        
        sensors = current_settings.get('sensors', [])
        sensor = next((s for s in sensors if s['id'] == sensor_id), None)
        
        if not sensor:
            return jsonify({'error': 'Sensor not found'}), 404
        
        config = sensor.get('config', {})
        s1_id = config.get('source_sensor_1')
        s2_id = config.get('source_sensor_2')
        s3_id = config.get('source_sensor_3')
        
        if not all([s1_id, s2_id, s3_id]):
            return jsonify({'error': 'Source sensors not configured'}), 400
        
        # Collect samples
        import time
        s1_samples = []
        s2_samples = []
        s3_samples = []
        
        for i in range(num_samples):
            s1_samples.append(sensor_last_values.get(s1_id, 0))
            s2_samples.append(sensor_last_values.get(s2_id, 0))
            s3_samples.append(sensor_last_values.get(s3_id, 0))
            time.sleep(0.05)  # 50ms between samples (20Hz)
        
        # Calculate averages
        tare_offsets = [
            sum(s1_samples) / len(s1_samples),
            sum(s2_samples) / len(s2_samples),
            sum(s3_samples) / len(s3_samples)
        ]
        
        return jsonify({
            'status': 'success',
            'tare_offsets': tare_offsets,
            'num_samples': num_samples,
            'message': f'Tare captured: {num_samples} samples averaged'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sensor/<sensor_id>/calibration/capture', methods=['POST'])
def capture_calibration_point(sensor_id):
    """Capture calibration point with known applied force"""
    try:
        data = request.get_json()
        applied_force = data.get('applied_force')
        tare_offsets = data.get('tare_offsets', [0, 0, 0])
        num_samples = data.get('num_samples', 50)
        
        if applied_force is None or applied_force <= 0:
            return jsonify({'error': 'Valid applied force required'}), 400
        
        sensors = current_settings.get('sensors', [])
        sensor = next((s for s in sensors if s['id'] == sensor_id), None)
        
        if not sensor:
            return jsonify({'error': 'Sensor not found'}), 404
        
        config = sensor.get('config', {})
        s1_id = config.get('source_sensor_1')
        s2_id = config.get('source_sensor_2')
        s3_id = config.get('source_sensor_3')
        formula = config.get('formula', '0')
        
        if not all([s1_id, s2_id, s3_id]):
            return jsonify({'error': 'Source sensors not configured'}), 400
        
        # Collect samples
        import time
        s1_samples = []
        s2_samples = []
        s3_samples = []
        
        for i in range(num_samples):
            s1_samples.append(sensor_last_values.get(s1_id, 0))
            s2_samples.append(sensor_last_values.get(s2_id, 0))
            s3_samples.append(sensor_last_values.get(s3_id, 0))
            time.sleep(0.05)
        
        # Calculate averages
        s1_avg = sum(s1_samples) / len(s1_samples)
        s2_avg = sum(s2_samples) / len(s2_samples)
        s3_avg = sum(s3_samples) / len(s3_samples)
        
        # Apply tare
        s1_tared = s1_avg - tare_offsets[0]
        s2_tared = s2_avg - tare_offsets[1]
        s3_tared = s3_avg - tare_offsets[2]
        
        # Evaluate formula with tared values
        eval_formula = formula.replace('s1', str(s1_tared)).replace('s2', str(s2_tared)).replace('s3', str(s3_tared))
        eval_formula = eval_formula.replace('^', '**')
        
        if not re.match(r'^[\d\s\.\+\-\*/\(\)\*]+$', eval_formula):
            return jsonify({'error': 'Invalid formula'}), 400
        
        raw_result = eval(eval_formula)
        
        # Calculate calibration factor
        calibration_factor = applied_force / raw_result if raw_result != 0 else 1.0
        
        return jsonify({
            'status': 'success',
            'raw_result': raw_result,
            'calibration_factor': calibration_factor,
            'applied_force': applied_force,
            'num_samples': num_samples,
            'message': f'Calibration point captured'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sensor/<sensor_id>/calibration/save', methods=['POST'])
def save_calibration(sensor_id):
    """Save calibration data to sensor config"""
    try:
        data = request.get_json()
        tare_offsets = data.get('tare_offsets')
        calibration_factor = data.get('calibration_factor')
        
        if not tare_offsets or calibration_factor is None:
            return jsonify({'error': 'Missing calibration data'}), 400
        
        sensors = current_settings.get('sensors', [])
        sensor = next((s for s in sensors if s['id'] == sensor_id), None)
        
        if not sensor:
            return jsonify({'error': 'Sensor not found'}), 404
        
        # Update sensor config
        if 'config' not in sensor:
            sensor['config'] = {}
        
        sensor['config']['calibration'] = {
            'is_calibrated': True,
            'tare_offsets': tare_offsets,
            'calibration_factor': calibration_factor,
            'calibrated_at': time.time()
        }
        
        # Update sensor instance if it exists
        if sensor_id in sensor_instances and sensor_instances[sensor_id]:
            sensor_instances[sensor_id]['calibration'] = sensor['config']['calibration']
        
        # Save settings
        current_settings['sensors'] = sensors
        save_settings_to_file(current_settings)
        
        return jsonify({
            'status': 'success',
            'message': 'Calibration saved successfully'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sensor/<sensor_id>/calibration', methods=['DELETE'])
def reset_calibration(sensor_id):
    """Reset/clear calibration data"""
    try:
        sensors = current_settings.get('sensors', [])
        sensor = next((s for s in sensors if s['id'] == sensor_id), None)
        
        if not sensor:
            return jsonify({'error': 'Sensor not found'}), 404
        
        # Reset calibration
        if 'config' in sensor:
            sensor['config']['calibration'] = {
                'is_calibrated': False,
                'tare_offsets': [0, 0, 0],
                'calibration_factor': 1.0
            }
        
        # Update sensor instance
        if sensor_id in sensor_instances and sensor_instances[sensor_id]:
            sensor_instances[sensor_id]['calibration'] = sensor['config']['calibration']
        
        # Save settings
        current_settings['sensors'] = sensors
        save_settings_to_file(current_settings)
        
        return jsonify({
            'status': 'success',
            'message': 'Calibration reset'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/update', methods=['POST'])
def trigger_update():
    """Trigger system update via install script."""
    import subprocess
    import os
    import shutil
    
    global update_in_progress
    
    with update_lock:
        if update_in_progress:
            return jsonify({'status': 'error', 'message': 'Update already in progress. Please wait.'}), 409
    
    try:
        # Get the project root directory (where app.py is located)
        project_root = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(project_root, 'install.sh')
        
        # Check if install.sh exists
        if not os.path.exists(script_path):
            # Try parent directory (in case we're in a subdirectory)
            script_path = os.path.join(os.path.dirname(project_root), 'install.sh')
            if not os.path.exists(script_path):
                return jsonify({'status': 'error', 'message': 'install.sh not found'}), 404
        
        # Check if updates are available
        try:
            # Fetch latest from remote
            subprocess.run(['git', 'fetch', 'origin', 'main'], 
                         cwd=os.path.dirname(script_path), 
                         capture_output=True, 
                         text=True,
                         timeout=10)
            
            # Check if local is behind remote
            result = subprocess.run(
                ['git', 'rev-list', '--count', 'HEAD..origin/main'],
                cwd=os.path.dirname(script_path),
                capture_output=True,
                text=True,
                timeout=5
            )
            
            commits_behind = int(result.stdout.strip())
            
            if commits_behind == 0:
                return jsonify({
                    'status': 'info', 
                    'message': 'Already up to date! No updates available.'
                })
        except Exception as e:
            # If git check fails, continue anyway (might be connectivity issue)
            pass
        
        # Find bash executable
        bash_path = shutil.which('bash')
        if not bash_path:
            # Try common locations
            for path in ['/bin/bash', '/usr/bin/bash', '/usr/local/bin/bash']:
                if os.path.exists(path):
                    bash_path = path
                    break
        
        if not bash_path:
            return jsonify({'status': 'error', 'message': 'bash executable not found'}), 500
        
        # Mark update as in progress
        with update_lock:
            update_in_progress = True
        
        # Emit initial status via WebSocket
        socketio.emit('update_progress', {'step': 'Starting update process...', 'type': 'info'})
        
        # Start a thread to run the update
        def run_update():
            global update_in_progress
            try:
                socketio.emit('update_progress', {'step': 'Running install script in auto-update mode...', 'type': 'info'})
                socketio.sleep(0.1)  # Give time for message to send
                
                # Run install.sh with auto-update flag (non-interactive)
                # Use Popen to capture output in real-time
                import os
                env = os.environ.copy()
                env['PYTHONUNBUFFERED'] = '1'  # Disable Python output buffering
                
                # Find shell executable - try common locations
                shell_cmd = None
                for shell in ['/bin/bash', '/usr/bin/bash', '/bin/sh', '/usr/bin/sh']:
                    if os.path.exists(shell):
                        shell_cmd = shell
                        break
                
                if not shell_cmd:
                    socketio.emit('update_progress', {'step': 'Error: No shell found (bash/sh)', 'type': 'error'})
                    return
                
                socketio.emit('update_progress', {'step': f'Using shell: {shell_cmd}', 'type': 'info'})
                
                # Use found shell
                process = subprocess.Popen(
                    [shell_cmd, script_path, 'auto-update'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=0,  # Unbuffered
                    universal_newlines=True,
                    cwd=os.path.dirname(script_path),
                    env=env
                )
                
                # Read output line by line in real-time
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    
                    if line:
                        clean_line = line.strip()
                        # Skip empty lines, comment lines, and ANSI escape sequences
                        if clean_line and not clean_line.startswith('#'):
                            # Remove ANSI color codes
                            import re
                            clean_line = re.sub(r'\x1b\[[0-9;]*m', '', clean_line)
                            
                            # Determine message type
                            msg_type = 'info'
                            if '✓' in clean_line or 'success' in clean_line.lower():
                                msg_type = 'success'
                            elif '✗' in clean_line or 'error' in clean_line.lower() or 'fail' in clean_line.lower():
                                msg_type = 'error'
                            elif '⚠' in clean_line or 'warning' in clean_line.lower():
                                msg_type = 'warning'
                            
                            socketio.emit('update_progress', {'step': clean_line, 'type': msg_type})
                            socketio.sleep(0.01)  # Small delay to ensure message is sent
                
                # Wait for process to complete
                process.wait()
                
                # Exit code -15 (SIGTERM) is expected when service restarts itself
                if process.returncode == 0:
                    socketio.emit('update_progress', {'step': '✓ Update completed successfully', 'type': 'success'})
                elif process.returncode == -15:
                    socketio.emit('update_progress', {'step': '✓ Update completed - Service restarting...', 'type': 'success'})
                else:
                    socketio.emit('update_progress', {'step': f'⚠ Update exited with code {process.returncode}', 'type': 'warning'})
                    
            except Exception as e:
                socketio.emit('update_progress', {'step': f'Update error: {str(e)}', 'type': 'error'})
            finally:
                with update_lock:
                    update_in_progress = False
        
        import threading
        threading.Thread(target=run_update, daemon=True).start()
        
        return jsonify({'status': 'success', 'message': 'Update started. Watch progress below.'})
    except Exception as e:
        with update_lock:
            update_in_progress = False
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/version')
def get_version():
    """Get current version info from git."""
    import subprocess
    import os
    
    try:
        project_root = os.path.dirname(os.path.abspath(__file__))
        
        # Get current commit hash
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            print(f"Git rev-parse failed: {result.stderr}")
            return jsonify({'commit': 'unknown', 'date': 'unknown', 'error': result.stderr.strip()})
        
        commit_hash = result.stdout.strip()
        
        # Get commit date
        result = subprocess.run(
            ['git', 'log', '-1', '--format=%cd', '--date=short'],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            print(f"Git log failed: {result.stderr}")
            commit_date = 'unknown'
        else:
            commit_date = result.stdout.strip()
        
        return jsonify({
            'commit': commit_hash,
            'date': commit_date
        })
    except subprocess.TimeoutExpired as e:
        print(f"Git command timeout: {e}")
        return jsonify({'commit': 'timeout', 'date': 'timeout', 'error': 'Git command timed out'})
    except FileNotFoundError as e:
        print(f"Git not found: {e}")
        return jsonify({'commit': 'no-git', 'date': 'no-git', 'error': 'Git not installed'})
    except Exception as e:
        print(f"Version check error: {e}")
        return jsonify({'commit': 'error', 'date': 'error', 'error': str(e)})

@app.route('/api/data')
def get_data():
    """REST API endpoint to get current wind tunnel data."""
    return jsonify(generate_mock_data())

# CSV LOG FILE ENDPOINTS - DISABLED (using SQLite database instead)
# @app.route('/api/logs', methods=['GET'])
# def get_log_files():
#     """Get list of available log files."""
#     try:
#         log_files = []
#         if os.path.exists(DATA_LOG_DIR):
#             for filename in os.listdir(DATA_LOG_DIR):
#                 if filename.endswith('.csv'):
#                     filepath = os.path.join(DATA_LOG_DIR, filename)
#                     file_size = os.path.getsize(filepath)
#                     file_time = os.path.getmtime(filepath)
#                     
#                     # Count rows
#                     try:
#                         with open(filepath, 'r') as f:
#                             row_count = sum(1 for _ in f) - 1  # Subtract header
#                     except:
#                         row_count = 0
#                     
#                     log_files.append({
#                         'filename': filename,
#                         'size': file_size,
#                         'size_mb': round(file_size / 1024 / 1024, 2),
#                         'modified': datetime.fromtimestamp(file_time).isoformat(),
#                         'rows': row_count,
#                         'is_current': filepath == current_log_file
#                     })
#         
#         # Sort by modified time, newest first
#         log_files.sort(key=lambda x: x['modified'], reverse=True)
#         
#         return jsonify({
#             'status': 'success',
#             'files': log_files,
#             'logging_active': current_settings.get('dataLogging', False),
#             'current_file': os.path.basename(current_log_file) if current_log_file else None
#         })
#     except Exception as e:
#         return jsonify({'status': 'error', 'message': str(e)}), 500

# @app.route('/api/logs/<filename>', methods=['GET'])
# def download_log_file(filename):
#     """Download a specific log file."""
#     from flask import send_file
#     try:
#         # Security: ensure filename doesn't contain path traversal
#         if '..' in filename or '/' in filename or '\\' in filename:
#             return jsonify({'status': 'error', 'message': 'Invalid filename'}), 400
#         
#         filepath = os.path.join(DATA_LOG_DIR, filename)
#         
#         if not os.path.exists(filepath):
#             return jsonify({'status': 'error', 'message': 'File not found'}), 404
#         
#         return send_file(filepath, as_attachment=True, download_name=filename)
#     except Exception as e:
#         return jsonify({'status': 'error', 'message': str(e)}), 500

# @app.route('/api/logs/<filename>', methods=['DELETE'])
# def delete_log_file(filename):
#     """Delete a specific log file."""
#     try:
#         # Security: ensure filename doesn't contain path traversal
#         if '..' in filename or '/' in filename or '\\' in filename:
#             return jsonify({'status': 'error', 'message': 'Invalid filename'}), 400
#         
#         filepath = os.path.join(DATA_LOG_DIR, filename)
#         
#         # Don't allow deleting current log file
#         if filepath == current_log_file:
#             return jsonify({'status': 'error', 'message': 'Cannot delete active log file'}), 400
#         
#         if not os.path.exists(filepath):
#             return jsonify({'status': 'error', 'message': 'File not found'}), 404
#         
#         os.remove(filepath)
#         return jsonify({'status': 'success', 'message': f'Deleted {filename}'})
#     except Exception as e:
#         return jsonify({'status': 'error', 'message': str(e)}), 500
# END CSV LOG FILE ENDPOINTS

# WebSocket events
@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    global background_thread
    print('Client connected')
    
    # Update heartbeat timestamp
    import time
    fan_state['last_heartbeat'] = time.time()
    
    with thread_lock:
        if background_thread is None:
            background_thread = socketio.start_background_task(background_data_updater)
    # Don't emit immediately - background thread will send data within 200ms

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print('Client disconnected')

@socketio.on('heartbeat')
def handle_heartbeat():
    """Handle client heartbeat for fan safety monitoring"""
    import time
    fan_state['last_heartbeat'] = time.time()
    return {'status': 'ok', 'timestamp': time.time()}

@socketio.on('request_data')
def handle_data_request():
    """Handle explicit data requests from clients."""
    emit('data_update', generate_mock_data())

# Initialize background threads when module is loaded (for Gunicorn)
_threads_started = False

def init_background_threads():
    """Initialize background threads. Called once per worker."""
    global _threads_started
    
    if _threads_started:
        return  # Already started in this process
    
    import threading
    
    # Start fan safety monitoring thread
    safety_thread = threading.Thread(target=check_fan_safety, daemon=True)
    safety_thread.start()
    print("✓ Fan safety monitoring started")
    
    # Start UDP discovery listener thread
    discovery_listener_thread = threading.Thread(target=udp_discovery_listener, daemon=True)
    discovery_listener_thread.start()
    print("✓ UDP discovery listener thread started")
    logger.info("UDP discovery listener thread started")
    
    _threads_started = True

# Gunicorn server hook - called after worker processes are forked
def post_fork(server, worker):
    """Gunicorn post_fork hook - initialize threads in worker process."""
    logger.info(f"✓ Gunicorn post_fork: Worker {worker.pid} started - initializing background threads...")
    init_background_threads()

# Initialize database
init_database()

# NOTE: Background threads should NOT be started at module load when using Gunicorn
# They will be started by the post_fork() hook in worker processes
# For direct execution (python app.py), threads start in the if __name__ == '__main__' block

if __name__ == '__main__':
    # Direct Python execution - start threads manually
    logger.info("Running in direct mode - starting background threads...")
    init_background_threads()
    
    # Run on all interfaces for Raspberry Pi access
    # Use port 80 (standard HTTP port), disable debug in production
    # Note: On Linux/Raspberry Pi, running on port 80 requires sudo/root privileges
    # Using threaded mode for better WebSocket performance
    socketio.run(app, host='0.0.0.0', port=80, debug=False, allow_unsafe_werkzeug=True)
