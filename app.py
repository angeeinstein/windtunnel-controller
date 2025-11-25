from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO, emit
import random
import time
from threading import Lock

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

# Thread lock for data updates
thread_lock = Lock()
background_thread = None

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
    """Generate mock sensor data for testing. Replace with actual sensor readings."""
    return {
        'velocity': round(random.uniform(0, 50), 2),
        'lift': round(random.uniform(-5, 15), 2),
        'drag': round(random.uniform(0, 10), 2),
        'pressure': round(random.uniform(100, 102), 3),
        'temperature': round(random.uniform(18, 25), 1),
        'rpm': random.randint(0, 3000),
        'power': round(random.uniform(0, 500), 1),
        'timestamp': time.time()
    }

def background_data_updater():
    """Background thread to continuously send data updates to connected clients."""
    while True:
        socketio.sleep(0.5)  # Update every 500ms
        global wind_tunnel_data
        wind_tunnel_data = generate_mock_data()
        socketio.emit('data_update', wind_tunnel_data, namespace='/')

@app.route('/')
def index():
    """Main control screen page."""
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    """REST API endpoint to get current wind tunnel data."""
    return jsonify(wind_tunnel_data)

@socketio.on('connect')
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
    # When using gunicorn, this block won't be executed
    socketio.run(app, host='0.0.0.0', port=80, debug=True)
