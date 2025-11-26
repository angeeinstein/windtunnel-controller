# Wind Tunnel Controller

A Flask-based web application for controlling and monitoring a university wind tunnel system. Designed for Raspberry Pi 5 with a 1080p touchscreen display.

## Features

- **Real-time Data Display**: WebSocket-based live updates of wind tunnel parameters
- **Touchscreen Optimized**: Large buttons and responsive design for touch interaction
- **Key Measurements**:
  - Air velocity (m/s)
  - Lift force (N)
  - Drag force (N)
  - Pressure (kPa)
  - Temperature (°C)
  - Fan RPM
  - Power consumption (W)
  - Lift/Drag ratio (calculated)

## Project Structure

```
windtunnel-controller/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── templates/
│   └── index.html        # Main control screen HTML
└── static/
    ├── css/
    │   └── style.css     # Styling
    └── js/
        └── main.js       # WebSocket and UI logic
```

## Installation

### Automated Installation (Recommended)

The easiest way to install on your Raspberry Pi 5:

1. **Download the installation script**:
   ```bash
   wget https://raw.githubusercontent.com/angeeinstein/windtunnel-controller/main/install.sh
   ```

2. **Make it executable**:
   ```bash
   chmod +x install.sh
   ```

3. **Run the installer**:
   ```bash
   sudo bash install.sh
   ```

The script will automatically:
- ✓ Clone the repository
- ✓ Install all system dependencies
- ✓ Set up Python virtual environment
- ✓ Install Python packages
- ✓ Create and enable systemd service
- ✓ Configure firewall (if UFW is enabled)
- ✓ Start the application

**Running the script again** will detect the existing installation and offer to update or reinstall.

### Manual Installation

If you prefer manual installation:

1. **Update system packages**:
   ```bash
   sudo apt update
   sudo apt upgrade -y
   ```

2. **Install system dependencies**:
   ```bash
   sudo apt install git python3 python3-pip python3-venv python3-dev build-essential libssl-dev libffi-dev -y
   ```

3. **Clone the repository**:
   ```bash
   cd ~
   git clone https://github.com/angeeinstein/windtunnel-controller.git
   cd windtunnel-controller
   ```

4. **Create virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

5. **Install Python packages**:
   ```bash
   pip install -r requirements.txt
   ```

## Running the Application

### After Automated Installation

The application runs automatically as a systemd service. Use these commands:

```bash
# Check status
sudo systemctl status windtunnel

# View real-time logs
sudo journalctl -u windtunnel -f

# Restart service
sudo systemctl restart windtunnel

# Stop service
sudo systemctl stop windtunnel

# Start service
sudo systemctl start windtunnel
```

### Development Mode (Manual)

**Option 1: Using Flask development server**
```bash
cd ~/windtunnel-controller
source venv/bin/activate
sudo python3 app.py
```

**Option 2: Using Gunicorn (production)**
```bash
cd ~/windtunnel-controller
source venv/bin/activate
sudo gunicorn --worker-class gthread --workers 1 --threads 4 --bind 0.0.0.0:80 app:app
```

> **Note**: `sudo` is required to run on port 80. The server will be accessible at `http://<raspberry-pi-ip>/`
> The systemd service uses Gunicorn with threaded workers for production deployment.
> WebSocket support is provided by Flask-SocketIO with simple-websocket backend.

## Configuration

### Port Configuration

By default, the server runs on port 80 (standard HTTP). To change this, edit `app.py`:

```python
socketio.run(app, host='0.0.0.0', port=80, debug=True)
```

### Debug Mode

For production, set `debug=False` in `app.py`:

```python
socketio.run(app, host='0.0.0.0', port=80, debug=False)
```

## Connecting to Sensors

The current implementation uses **mock data** for testing. To connect real sensors:

1. **Replace the `generate_mock_data()` function** in `app.py` with actual sensor readings
2. **Import sensor libraries** (e.g., for ADC, I2C, GPIO)
3. **Update the data structure** to match your sensor outputs

Example sensor integration:

```python
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# Initialize I2C and ADC
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)

def read_sensor_data():
    """Read actual sensor data."""
    chan = AnalogIn(ads, ADS.P0)
    return {
        'velocity': calculate_velocity(chan.voltage),
        'lift': read_load_cell_lift(),
        'drag': read_load_cell_drag(),
        # ... other sensors
    }
```

## Accessing the Web Interface

1. **Find your Raspberry Pi's IP address**:
   ```bash
   hostname -I
   ```

2. **Open a browser** on any device on the same network and navigate to:
   ```
   http://<raspberry-pi-ip>/
   ```

3. For local access on the Pi itself:
   ```
   http://localhost/
   ```

## Updating

To update to the latest version:

```bash
sudo bash install.sh
```

Select option 1 (Update to latest version) when prompted.

## Uninstalling

To completely remove the Wind Tunnel Controller:

**Option 1: Using the install script**
```bash
sudo bash install.sh uninstall
```

**Option 2: Interactive menu**
```bash
sudo bash install.sh
```
Then select option 3 (Uninstall completely)

**Option 3: Manual uninstall**
```bash
sudo systemctl stop windtunnel
sudo systemctl disable windtunnel
sudo rm /etc/systemd/system/windtunnel.service
sudo systemctl daemon-reload
rm -rf ~/windtunnel-controller
```

## Troubleshooting

### Installation Script Issues

If the installation script fails:
1. Check internet connection
2. Ensure you're running with `sudo`
3. Check the error message in the output
4. Try manual installation instead

### Port 80 Permission Denied

If you get a permission error on port 80, either:
- Run with `sudo`
- Use a different port (e.g., 8080)
- Configure port forwarding with nginx

### Service Won't Start

Check the logs for errors:
```bash
sudo journalctl -u windtunnel -f
```

Common issues:
- Port 80 already in use: `sudo lsof -i :80`
- Missing dependencies: Re-run `install.sh`
- Python errors: Check app.py syntax

### WebSocket Connection Issues

- Check firewall settings: `sudo ufw allow 80/tcp`
- Verify the server is running: `sudo systemctl status windtunnel`
- Check browser console for errors
- Ensure you're accessing the correct IP address

### Dependencies Installation Fails

The install script handles this automatically, but if installing manually:
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Future Enhancements

- [ ] Connect actual wind tunnel sensors
- [ ] Add data logging and export (CSV/JSON)
- [ ] Implement control buttons (start/stop/reset)
- [ ] Add graphical plots for historical data
- [ ] User authentication
- [ ] Configuration file for settings
- [ ] Emergency stop functionality

## License

This project is for educational use at your university.

## Contributing

For issues or improvements, please contact your development team.
