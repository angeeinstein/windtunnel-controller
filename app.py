from flask import Flask, render_template, jsonify, Response
from flask_socketio import SocketIO, emit
import random
import time
import json
import os
import re
import csv
import sqlite3
from datetime import datetime
from threading import Lock, Thread

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
    """Remove sensor data older than DATA_RETENTION_HOURS."""
    cutoff_time = time.time() - (DATA_RETENTION_HOURS * 3600)
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM sensor_data WHERE timestamp < ?', (cutoff_time,))
        deleted_rows = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted_rows > 0:
            print(f"Cleaned up {deleted_rows} old sensor data rows")
    except Exception as e:
        print(f"Error cleaning up database: {e}")

# Initialize database on startup
init_database()

# Default settings
DEFAULT_SETTINGS = {
    'updateInterval': UPDATE_INTERVAL_MS,  # Fixed at 500ms
    'darkMode': False,
    'decimalPlaces': 2,
    'velocityUnit': 'ms',
    'temperatureUnit': 'c',
    'systemName': 'Wind Tunnel Alpha',
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
    'HX711': {
        'name': 'HX711 Load Cell Amplifier',
        'category': 'hardware',
        'description': 'For measuring force/weight with load cells',
        'fields': [
            {'name': 'dout_pin', 'label': 'DOUT Pin (BCM)', 'type': 'number', 'default': 5, 'min': 2, 'max': 27},
            {'name': 'pd_sck_pin', 'label': 'PD_SCK Pin (BCM)', 'type': 'number', 'default': 6, 'min': 2, 'max': 27},
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
            {'name': 'address', 'label': 'I2C Address', 'type': 'select', 'options': ['0x76', '0x77'], 'default': '0x76'},
            {'name': 'sea_level_pressure', 'label': 'Sea Level Pressure (hPa)', 'type': 'number', 'default': 1013.25, 'step': 0.01}
        ]
    },
    'SDP811': {
        'name': 'Sensirion SDP811-500Pa',
        'category': 'hardware',
        'description': 'Differential pressure sensor for pitot tube airspeed',
        'fields': [
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
            {'name': 'pin', 'label': 'Data Pin (BCM)', 'type': 'number', 'default': 4, 'min': 2, 'max': 27}
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
            {'name': 'channel', 'label': 'Channel', 'type': 'select', 'options': ['0', '1', '2', '3', '4', '5', '6', '7'], 'default': '0'},
            {'name': 'vref', 'label': 'Reference Voltage', 'type': 'number', 'default': 3.3, 'step': 0.1}
        ]
    },
    'MPU6050': {
        'name': 'MPU6050 Gyro/Accelerometer',
        'category': 'hardware',
        'description': '6-axis motion tracking sensor',
        'fields': [
            {'name': 'address', 'label': 'I2C Address', 'type': 'select', 'options': ['0x68', '0x69'], 'default': '0x68'},
            {'name': 'output', 'label': 'Output Value', 'type': 'select', 
             'options': ['accel_x', 'accel_y', 'accel_z', 'gyro_x', 'gyro_y', 'gyro_z', 'temperature'], 
             'default': 'accel_x'}
        ]
    }
}

# Sensor initialization cache and handlers
sensor_instances = {}  # Cache initialized sensors {sensor_id: instance}
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
        'MPU6050': 'adafruit_mpu6050'
    }
    
    for sensor_type, module_name in library_checks.items():
        try:
            importlib.import_module(module_name)
            available_sensor_libraries[sensor_type] = True
            print(f"✓ {sensor_type} library available")
        except ImportError:
            available_sensor_libraries[sensor_type] = False
            print(f"✗ {sensor_type} library not available")
    
    return available_sensor_libraries

# Check sensor library availability at startup
check_sensor_library_availability()

# Sensor handler functions
def init_hx711(config):
    """Initialize HX711 load cell amplifier"""
    try:
        from hx711 import HX711
        
        hx = HX711(
            dout_pin=int(config.get('dout_pin', 5)),
            pd_sck_pin=int(config.get('pd_sck_pin', 6))
        )
        
        hx.set_reference_unit(float(config.get('reference_unit', 1)))
        hx.set_offset(int(config.get('offset', 0)))
        
        # Set channel/gain
        channel_map = {'A-128': ('A', 128), 'A-64': ('A', 64), 'B-32': ('B', 32)}
        channel, gain = channel_map.get(config.get('channel', 'A-128'), ('A', 128))
        hx.select_channel(channel)
        hx.set_gain(gain)
        
        hx.reset()
        print(f"HX711 initialized on DOUT={config.get('dout_pin')}, SCK={config.get('pd_sck_pin')}")
        return hx
    except Exception as e:
        print(f"Error initializing HX711: {e}")
        return None

def read_hx711(sensor, config):
    """Read force from HX711"""
    try:
        if sensor is None:
            return 0
        value = sensor.get_weight_mean(3)  # Average 3 readings
        return value if value is not None else 0
    except Exception as e:
        print(f"Error reading HX711: {e}")
        return 0

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

# Sensor handler registry
SENSOR_HANDLERS = {
    'HX711': {'init': init_hx711, 'read': read_hx711},
    'ADS1115': {'init': init_ads1115, 'read': read_ads1115},
    'BMP280': {'init': init_bmp280, 'read': read_bmp280},
    'SDP811': {'init': init_sdp811, 'read': read_sdp811},
    'DHT22': {'init': init_dht22, 'read': read_dht22},
    'DS18B20': {'init': init_ds18b20, 'read': read_ds18b20},
    'MCP3008': {'init': init_mcp3008, 'read': read_mcp3008},
    'MPU6050': {'init': init_mpu6050, 'read': read_mpu6050}
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

# Add cache control headers
@app.after_request
def add_header(response):
    """Add headers to prevent caching of static files."""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

socketio = SocketIO(app, cors_allowed_origins="*")

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
    
    # Generate data for each enabled sensor
    for sensor in sensors:
        if not sensor.get('enabled', True):
            continue
            
        sensor_id = sensor['id']
        sensor_type = sensor['type']
        
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
                    
                    # Validate the formula only contains safe characters
                    if re.match(r'^[\d\s\.\+\-\*/\(\)\*]+$', eval_formula):
                        result = eval(eval_formula)
                        
                        # Check for invalid results
                        if result is None or (isinstance(result, float) and (result != result or abs(result) == float('inf'))):
                            print(f"Warning: Invalid result for sensor {sensor_id}: {result}")
                            data[sensor_id] = 0
                        else:
                            data[sensor_id] = float(result)
                            sensor_values[sensor_id] = float(result)  # Make available for other calculated sensors
                    else:
                        print(f"Warning: Invalid formula for sensor {sensor_id}: {formula}")
                        data[sensor_id] = 0
                    
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
    """
    global current_log_file
    
    last_db_flush = time.time()
    last_cleanup = time.time()
    DB_FLUSH_INTERVAL = 10  # Flush database writes every 10 seconds
    CLEANUP_INTERVAL = 3600  # Cleanup old data every hour
    
    while True:
        data = generate_mock_data()
        timestamp = data.get('timestamp', time.time())
        
        # Send to connected clients via WebSocket
        socketio.emit('data_update', data)
        
        # Write to database (queued for batch processing)
        write_sensor_data_to_db(timestamp, data)
        
        # Periodically flush database writes
        current_time = time.time()
        if current_time - last_db_flush >= DB_FLUSH_INTERVAL:
            flush_db_write_queue()
            last_db_flush = current_time
        
        # Periodically cleanup old data
        if current_time - last_cleanup >= CLEANUP_INTERVAL:
            cleanup_old_data()
            last_cleanup = current_time
        
        # Fixed update interval (200ms = 5Hz)
        time.sleep(UPDATE_INTERVAL_MS / 1000)

@app.route('/')
def index():
    """Main control screen page."""
    return render_template('index.html')

@app.route('/settings')
def settings():
    """Settings page."""
    return render_template('settings.html')

@app.route('/api/sensor-types', methods=['GET'])
def get_sensor_types():
    """Get available sensor types and their configuration requirements."""
    # Add availability information to sensor types
    sensor_types_with_availability = {}
    for type_id, type_info in SENSOR_TYPES.items():
        sensor_types_with_availability[type_id] = type_info.copy()
        # Mark hardware sensors as available/unavailable based on library presence
        if type_info.get('category') == 'hardware':
            sensor_types_with_availability[type_id]['available'] = available_sensor_libraries.get(type_id, False)
        else:
            # Mock and calculated are always available
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
    global current_settings
    
    try:
        new_settings = request.get_json()
        
        # Validate and update settings
        if new_settings:
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

@app.route('/api/settings/reset', methods=['POST'])
def reset_settings():
    """Reset settings to defaults."""
    global current_settings
    
    current_settings = DEFAULT_SETTINGS.copy()
    
    if save_settings_to_file(current_settings):
        # Emit settings update to all connected clients
        socketio.emit('settings_updated', current_settings)
        return jsonify({'status': 'success', 'message': 'Settings reset to defaults'})
    else:
        return jsonify({'status': 'error', 'message': 'Failed to save settings'}), 500

@app.route('/api/test-sensor', methods=['POST'])
def test_sensor():
    """Test hardware sensor connection and initialization."""
    try:
        data = request.get_json()
        sensor_type = data.get('sensor_type')
        config = data.get('config', {})
        
        if not sensor_type:
            return jsonify({'status': 'error', 'message': 'sensor_type is required'}), 400
        
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
        try:
            sensor_instance = handler['init'](config)
            
            # Try to read a value
            value = handler['read'](sensor_instance, config)
            
            return jsonify({
                'status': 'success',
                'message': f'Sensor initialized successfully. Current reading: {value:.2f}',
                'value': value
            })
        except Exception as init_error:
            return jsonify({
                'status': 'error',
                'message': f'Failed to initialize sensor: {str(init_error)}'
            }), 500
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Test failed: {str(e)}'
        }), 500

@app.route('/api/refresh-sensor-libraries', methods=['POST'])
def refresh_sensor_libraries():
    """Recheck which sensor libraries are available (call after installation)."""
    check_sensor_library_availability()
    return jsonify({
        'status': 'success',
        'libraries': available_sensor_libraries
    })

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
    with thread_lock:
        if background_thread is None:
            background_thread = socketio.start_background_task(background_data_updater)
    emit('data_update', generate_mock_data())

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print('Client disconnected')

@socketio.on('request_data')
def handle_data_request():
    """Handle explicit data requests from clients."""
    emit('data_update', generate_mock_data())

if __name__ == '__main__':
    # Initialize database on startup
    init_database()
    
    # Run on all interfaces for Raspberry Pi access
    # Use port 80 (standard HTTP port), disable debug in production
    # Note: On Linux/Raspberry Pi, running on port 80 requires sudo/root privileges
    # Using threaded mode for better WebSocket performance
    socketio.run(app, host='0.0.0.0', port=80, debug=False, allow_unsafe_werkzeug=True)
