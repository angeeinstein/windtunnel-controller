from flask import Flask, render_template, jsonify, Response
from flask_socketio import SocketIO, emit
import random
import time
import json
import os
import re
import csv
from datetime import datetime
from threading import Lock

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching in development

# Settings file path
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')

# Data logging directory
DATA_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data_logs')
if not os.path.exists(DATA_LOG_DIR):
    os.makedirs(DATA_LOG_DIR)

# Log management configuration
MAX_LOG_FILE_SIZE_MB = 50  # Rotate log file when it exceeds 50MB
MAX_LOG_FILES = 100  # Keep maximum 100 log files (oldest deleted automatically)
MAX_TOTAL_LOG_SIZE_MB = 2000  # Maximum total size of all logs (2GB)

# Historical data buffer configuration
# Keep last 6 hours of data in memory for analysis (at 500ms intervals = 43,200 points)
# Since running on localhost (Raspberry Pi), network bandwidth is not a concern
MAX_HISTORICAL_POINTS = 43200  # 6 hours at 500ms intervals (~5-10MB RAM)
historical_data_buffer = []  # List of {timestamp, data_dict} entries

# Current log file (set when logging starts)
current_log_file = None
log_session_start = None
log_rows_written = 0

# Default settings
DEFAULT_SETTINGS = {
    'updateInterval': 500,
    'darkMode': False,
    'decimalPlaces': 2,
    'velocityUnit': 'ms',
    'temperatureUnit': 'c',
    'dataLogging': False,
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
    'gpio_analog': {
        'name': 'GPIO Analog Input',
        'fields': [
            {'name': 'pin', 'label': 'GPIO Pin', 'type': 'number', 'placeholder': 'e.g., 17'}
        ]
    },
    'i2c': {
        'name': 'I2C Sensor',
        'fields': [
            {'name': 'address', 'label': 'I2C Address (hex)', 'type': 'text', 'placeholder': 'e.g., 0x48'},
            {'name': 'bus', 'label': 'I2C Bus', 'type': 'number', 'placeholder': 'e.g., 1'}
        ]
    },
    'spi': {
        'name': 'SPI Sensor',
        'fields': [
            {'name': 'bus', 'label': 'SPI Bus', 'type': 'number', 'placeholder': 'e.g., 0'},
            {'name': 'device', 'label': 'SPI Device', 'type': 'number', 'placeholder': 'e.g., 0'},
            {'name': 'cs_pin', 'label': 'Chip Select Pin', 'type': 'number', 'placeholder': 'e.g., 8'}
        ]
    },
    'uart': {
        'name': 'UART/Serial Sensor',
        'fields': [
            {'name': 'port', 'label': 'Serial Port', 'type': 'text', 'placeholder': 'e.g., /dev/ttyUSB0'},
            {'name': 'baudrate', 'label': 'Baud Rate', 'type': 'number', 'placeholder': 'e.g., 9600'}
        ]
    }
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

# Simulated wind tunnel data (replace with actual sensor readings later)
wind_tunnel_data = {
    'velocity': 0.0,
    'lift': 0.0,
    'drag': 0.0,
    'pressure': 101.325,
    'temperature': 20.0,
    'rpm': 0,
    'power': 0.0,
    'timestamp': time.time()
}

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
    
    data = {'timestamp': time.time()}
    sensor_values = {}
    
    # Generate data for each enabled sensor
    for sensor in sensors:
        if not sensor.get('enabled', True):
            continue
            
        sensor_id = sensor['id']
        sensor_type = sensor['type']
        
        if sensor_type == 'mock':
            # Generate random mock data based on sensor ID
            if sensor_id == 'velocity' or 'velocity' in sensor['name'].lower():
                value = 15.5 + random.uniform(-2, 2)
            elif sensor_id == 'lift' or 'lift' in sensor['name'].lower():
                value = 125.3 + random.uniform(-10, 10)
            elif sensor_id == 'drag' or 'drag' in sensor['name'].lower():
                value = 45.2 + random.uniform(-5, 5)
            elif sensor_id == 'pressure' or 'pressure' in sensor['name'].lower():
                value = 101.3 + random.uniform(-0.5, 0.5)
            elif sensor_id == 'temperature' or 'temp' in sensor['name'].lower():
                value = 22.5 + random.uniform(-1, 1)
            elif sensor_id == 'rpm' or 'rpm' in sensor['name'].lower():
                value = 3500 + random.randint(-100, 100)
            elif sensor_id == 'power' or 'power' in sensor['name'].lower():
                value = 850 + random.uniform(-50, 50)
            else:
                value = random.uniform(0, 100)
            
            sensor_values[sensor_id] = value
            data[sensor_id] = value
            
        elif sensor_type == 'calculated':
            # Handle calculated values (will be computed after all sensors)
            pass
        else:
            # Real sensor reading (to be implemented)
            # For now, generate mock data
            value = random.uniform(0, 100)
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

def cleanup_old_logs():
    """
    Remove old log files to prevent disk space issues.
    Deletes oldest files first when limits are exceeded.
    """
    try:
        if not os.path.exists(DATA_LOG_DIR):
            return
        
        # Get all log files with their modification times
        log_files = []
        for filename in os.listdir(DATA_LOG_DIR):
            if filename.endswith('.csv'):
                filepath = os.path.join(DATA_LOG_DIR, filename)
                if filepath != current_log_file:  # Don't delete current log
                    log_files.append({
                        'path': filepath,
                        'size': os.path.getsize(filepath),
                        'mtime': os.path.getmtime(filepath)
                    })
        
        # Sort by modification time (oldest first)
        log_files.sort(key=lambda x: x['mtime'])
        
        # Check if we exceed file count limit
        while len(log_files) >= MAX_LOG_FILES:
            oldest = log_files.pop(0)
            print(f"Deleting old log file (file count limit): {os.path.basename(oldest['path'])}")
            os.remove(oldest['path'])
        
        # Check if we exceed total size limit
        total_size_mb = sum(f['size'] for f in log_files) / (1024 * 1024)
        if current_log_file:
            total_size_mb += os.path.getsize(current_log_file) / (1024 * 1024)
        
        while total_size_mb > MAX_TOTAL_LOG_SIZE_MB and log_files:
            oldest = log_files.pop(0)
            file_size_mb = oldest['size'] / (1024 * 1024)
            print(f"Deleting old log file (size limit): {os.path.basename(oldest['path'])} ({file_size_mb:.2f} MB)")
            os.remove(oldest['path'])
            total_size_mb -= file_size_mb
        
        print(f"Log cleanup complete. Total log size: {total_size_mb:.2f} MB, File count: {len(log_files) + (1 if current_log_file else 0)}")
    
    except Exception as e:
        print(f"Error during log cleanup: {e}")

def rotate_log_file():
    """
    Rotate to a new log file.
    Called when current file exceeds size limit.
    """
    global current_log_file, log_session_start, log_rows_written
    
    print(f"Rotating log file (size limit reached)...")
    
    # Close current log (already closed in append mode)
    old_file = current_log_file
    
    # Create new log file
    log_session_start = datetime.now()
    filename = f"windtunnel_{log_session_start.strftime('%Y%m%d_%H%M%S')}.csv"
    current_log_file = os.path.join(DATA_LOG_DIR, filename)
    log_rows_written = 0
    
    # Write header
    sensors = current_settings.get('sensors', DEFAULT_SENSORS)
    headers = ['timestamp', 'datetime'] + [s['id'] for s in sensors if s.get('enabled', True)]
    
    with open(current_log_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
    
    print(f"Rotated to new log file: {current_log_file}")
    
    # Cleanup old logs
    cleanup_old_logs()

def log_data_to_csv(data):
    """
    Log data to CSV file if logging is enabled.
    Automatically rotates files when size limit is reached.
    All data logged in SI units.
    """
    global current_log_file, log_session_start, log_rows_written
    
    if not current_settings.get('dataLogging', False):
        return
    
    # Create new log file if needed
    if current_log_file is None:
        log_session_start = datetime.now()
        filename = f"windtunnel_{log_session_start.strftime('%Y%m%d_%H%M%S')}.csv"
        current_log_file = os.path.join(DATA_LOG_DIR, filename)
        log_rows_written = 0
        
        # Write header
        sensors = current_settings.get('sensors', DEFAULT_SENSORS)
        headers = ['timestamp', 'datetime'] + [s['id'] for s in sensors if s.get('enabled', True)]
        
        with open(current_log_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
        
        print(f"Started logging to {current_log_file}")
        
        # Run cleanup on startup
        cleanup_old_logs()
    
    # Check if file size exceeds limit (check every 100 rows for performance)
    if log_rows_written % 100 == 0 and os.path.exists(current_log_file):
        file_size_mb = os.path.getsize(current_log_file) / (1024 * 1024)
        if file_size_mb > MAX_LOG_FILE_SIZE_MB:
            rotate_log_file()
    
    # Append data
    try:
        sensors = current_settings.get('sensors', DEFAULT_SENSORS)
        enabled_sensor_ids = [s['id'] for s in sensors if s.get('enabled', True)]
        
        row = [
            data.get('timestamp', time.time()),
            datetime.fromtimestamp(data.get('timestamp', time.time())).isoformat()
        ]
        row.extend([data.get(sid, '') for sid in enabled_sensor_ids])
        
        with open(current_log_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(row)
        
        log_rows_written += 1
    except Exception as e:
        print(f"Error logging data: {e}")

def stop_logging():
    """Stop current logging session."""
    global current_log_file, log_session_start, log_rows_written
    if current_log_file:
        print(f"Stopped logging to {current_log_file} ({log_rows_written} rows written)")
    current_log_file = None
    log_session_start = None
    log_rows_written = 0

def background_data_updater():
    """
    Background thread to send data updates to all connected clients.
    Uses configurable update interval from settings.
    All data transmitted in SI units.
    """
    global current_log_file, historical_data_buffer
    
    while True:
        data = generate_mock_data()
        socketio.emit('data_update', data)
        
        # Store in historical buffer for time-based scrolling
        historical_data_buffer.append({
            'timestamp': data.get('timestamp', time.time()),
            'data': data.copy()
        })
        
        # Keep buffer size limited (rolling window)
        if len(historical_data_buffer) > MAX_HISTORICAL_POINTS:
            historical_data_buffer.pop(0)
        
        # Log data if enabled
        if current_settings.get('dataLogging', False):
            log_data_to_csv(data)
        else:
            # Stop logging if it was previously enabled
            if current_log_file is not None:
                stop_logging()
        
        # Use configurable update interval (convert ms to seconds)
        time.sleep(current_settings.get('updateInterval', 500) / 1000)

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
    return jsonify(SENSOR_TYPES)

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
    Get historical data for time-based analysis.
    Query parameters:
    - sensor: sensor ID to retrieve (required)
    - start_time: Unix timestamp for start (optional, default: beginning of buffer)
    - end_time: Unix timestamp for end (optional, default: now)
    - max_points: Maximum number of points to return (optional, default: all in range)
    """
    from flask import request
    
    try:
        sensor_id = request.args.get('sensor')
        if not sensor_id:
            return jsonify({'status': 'error', 'message': 'sensor parameter required'}), 400
        
        start_time = float(request.args.get('start_time', 0))
        end_time = float(request.args.get('end_time', time.time() + 1000))
        max_points = int(request.args.get('max_points', 10000))  # Localhost: allow up to 10k points
        
        # Filter data by time range
        filtered_data = []
        for entry in historical_data_buffer:
            ts = entry['timestamp']
            if start_time <= ts <= end_time:
                value = entry['data'].get(sensor_id)
                if value is not None:
                    filtered_data.append({
                        'timestamp': ts,
                        'value': value
                    })
        
        # Downsample if too many points
        if len(filtered_data) > max_points:
            # Simple decimation - take every nth point
            step = len(filtered_data) // max_points
            filtered_data = filtered_data[::step]
        
        return jsonify({
            'status': 'success',
            'sensor': sensor_id,
            'data': filtered_data,
            'buffer_start': historical_data_buffer[0]['timestamp'] if historical_data_buffer else None,
            'buffer_end': historical_data_buffer[-1]['timestamp'] if historical_data_buffer else None,
            'buffer_size': len(historical_data_buffer),
            'max_buffer_size': MAX_HISTORICAL_POINTS
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
            # Validate numeric fields
            if 'updateInterval' in new_settings:
                new_settings['updateInterval'] = max(100, min(5000, int(new_settings['updateInterval'])))
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
        commit_hash = result.stdout.strip()
        
        # Get commit date
        result = subprocess.run(
            ['git', 'log', '-1', '--format=%cd', '--date=short'],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        commit_date = result.stdout.strip()
        
        return jsonify({
            'commit': commit_hash,
            'date': commit_date
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/data')
def get_data():
    """REST API endpoint to get current wind tunnel data."""
    return jsonify(wind_tunnel_data)

@app.route('/api/logs', methods=['GET'])
def get_log_files():
    """Get list of available log files."""
    try:
        log_files = []
        if os.path.exists(DATA_LOG_DIR):
            for filename in os.listdir(DATA_LOG_DIR):
                if filename.endswith('.csv'):
                    filepath = os.path.join(DATA_LOG_DIR, filename)
                    file_size = os.path.getsize(filepath)
                    file_time = os.path.getmtime(filepath)
                    
                    # Count rows
                    try:
                        with open(filepath, 'r') as f:
                            row_count = sum(1 for _ in f) - 1  # Subtract header
                    except:
                        row_count = 0
                    
                    log_files.append({
                        'filename': filename,
                        'size': file_size,
                        'size_mb': round(file_size / 1024 / 1024, 2),
                        'modified': datetime.fromtimestamp(file_time).isoformat(),
                        'rows': row_count,
                        'is_current': filepath == current_log_file
                    })
        
        # Sort by modified time, newest first
        log_files.sort(key=lambda x: x['modified'], reverse=True)
        
        return jsonify({
            'status': 'success',
            'files': log_files,
            'logging_active': current_settings.get('dataLogging', False),
            'current_file': os.path.basename(current_log_file) if current_log_file else None
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/logs/<filename>', methods=['GET'])
def download_log_file(filename):
    """Download a specific log file."""
    from flask import send_file
    try:
        # Security: ensure filename doesn't contain path traversal
        if '..' in filename or '/' in filename or '\\' in filename:
            return jsonify({'status': 'error', 'message': 'Invalid filename'}), 400
        
        filepath = os.path.join(DATA_LOG_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'status': 'error', 'message': 'File not found'}), 404
        
        return send_file(filepath, as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/logs/<filename>', methods=['DELETE'])
def delete_log_file(filename):
    """Delete a specific log file."""
    try:
        # Security: ensure filename doesn't contain path traversal
        if '..' in filename or '/' in filename or '\\' in filename:
            return jsonify({'status': 'error', 'message': 'Invalid filename'}), 400
        
        filepath = os.path.join(DATA_LOG_DIR, filename)
        
        # Don't allow deleting current log file
        if filepath == current_log_file:
            return jsonify({'status': 'error', 'message': 'Cannot delete active log file'}), 400
        
        if not os.path.exists(filepath):
            return jsonify({'status': 'error', 'message': 'File not found'}), 404
        
        os.remove(filepath)
        return jsonify({'status': 'success', 'message': f'Deleted {filename}'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# WebSocket events
def handle_connect():
    """Handle client connection."""
    global background_thread
    print('Client connected')
    with thread_lock:
        if background_thread is None:
            background_thread = socketio.start_background_task(background_data_updater)
    emit('data_update', wind_tunnel_data)

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print('Client disconnected')

@socketio.on('request_data')
def handle_data_request():
    """Handle explicit data requests from clients."""
    emit('data_update', wind_tunnel_data)

if __name__ == '__main__':
    # Run on all interfaces for Raspberry Pi access
    # Use port 80 (standard HTTP port), disable debug in production
    # Note: On Linux/Raspberry Pi, running on port 80 requires sudo/root privileges
    # Using threaded mode for better WebSocket performance
    socketio.run(app, host='0.0.0.0', port=80, debug=False, allow_unsafe_werkzeug=True)
