from flask import Flask, render_template, jsonify, Response
from flask_socketio import SocketIO, emit
import random
import time
from threading import Lock

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching in development

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

@app.route('/settings')
def settings():
    """Settings page."""
    return render_template('settings.html')

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
    # Using threaded mode for better WebSocket performance
    socketio.run(app, host='0.0.0.0', port=80, debug=False, allow_unsafe_werkzeug=True)
