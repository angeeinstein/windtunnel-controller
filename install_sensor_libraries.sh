#!/bin/bash
# Install all sensor libraries for wind tunnel controller
# Run this during initial setup on Raspberry Pi
# Robust version with error handling and graceful degradation

# NOTE: Do NOT use 'set -e' - we want to continue on non-critical failures

# Counters for summary
TOTAL_PACKAGES=0
SUCCESSFUL_PACKAGES=0
FAILED_PACKAGES=0

# Determine pip cache directory
PIP_CACHE_DIR="${HOME}/.cache/pip"
mkdir -p "$PIP_CACHE_DIR"

# Determine which pip to use (prefer venv if available)
if [ -n "$VIRTUAL_ENV" ] && [ -f "$VIRTUAL_ENV/bin/pip" ]; then
    PIP_CMD="$VIRTUAL_ENV/bin/pip"
    echo "Using virtual environment pip: $PIP_CMD"
elif [ -f "venv/bin/pip" ]; then
    PIP_CMD="venv/bin/pip"
    echo "Using local venv pip: $PIP_CMD"
else
    PIP_CMD="pip3"
    echo "Using system pip3 (WARNING: venv not detected)"
fi

echo "Installing sensor libraries for hardware support..."
echo ""

# Function to safely install a package with retries
install_package() {
    local package_name=$1
    local description=$2
    local max_retries=3
    local retry_count=0
    
    TOTAL_PACKAGES=$((TOTAL_PACKAGES + 1))
    
    echo -n "Installing ${description}... "
    
    while [ $retry_count -lt $max_retries ]; do
        # Try to install, caching packages for offline use
        if $PIP_CMD install --cache-dir="$PIP_CACHE_DIR" "$package_name" > /dev/null 2>&1; then
            echo "✓ Success"
            SUCCESSFUL_PACKAGES=$((SUCCESSFUL_PACKAGES + 1))
            return 0
        fi
        retry_count=$((retry_count + 1))
        if [ $retry_count -lt $max_retries ]; then
            sleep 1
        fi
    done
    
    echo "⚠ Skipped (optional)"
    FAILED_PACKAGES=$((FAILED_PACKAGES + 1))
    return 1
}

# Function to enable hardware interface safely
enable_interface() {
    local interface=$1
    local interface_name=$2
    
    if command -v raspi-config &> /dev/null; then
        echo -n "Enabling ${interface_name}... "
        if sudo raspi-config nonint "$interface" 0 &> /dev/null; then
            echo "✓ Enabled"
            return 0
        else
            echo "⚠ Skipped"
            return 1
        fi
    else
        echo "⚠ Not on Raspberry Pi - skipping ${interface_name} configuration"
        return 1
    fi
}

# Update package lists (critical)
echo "Updating package lists..."
if sudo apt-get update > /dev/null 2>&1; then
    echo "✓ Package lists updated"
else
    echo "⚠ Could not update package lists (continuing anyway)"
fi

# Install system dependencies (try each individually)
echo ""
echo "Installing system dependencies..."

SYSTEM_PACKAGES=(
    "python3-pip:Python package installer"
    "python3-smbus:I2C interface"
    "i2c-tools:I2C utilities"
    "python3-dev:Python headers"
    "build-essential:Build tools"
)

for pkg_entry in "${SYSTEM_PACKAGES[@]}"; do
    IFS=':' read -r pkg desc <<< "$pkg_entry"
    echo -n "Installing ${desc}... "
    if sudo apt-get install -y "$pkg" > /dev/null 2>&1; then
        echo "✓"
    else
        echo "⚠ Skipped"
    fi
done

# Enable I2C, SPI, and 1-Wire interfaces
echo ""
if command -v raspi-config &> /dev/null; then
    echo "Enabling hardware interfaces..."
    enable_interface "do_i2c" "I2C interface"
    enable_interface "do_spi" "SPI interface"
    enable_interface "do_onewire" "1-Wire interface"
else
    echo "⚠ Not on Raspberry Pi - hardware interface configuration skipped"
    echo "  (Sensor libraries will still be installed)"
fi

echo ""
echo "Installing sensor libraries..."

# Upgrade pip first
$PIP_CMD install --upgrade pip > /dev/null 2>&1 || true

# I2C Sensors
install_package "adafruit-circuitpython-bmp280" "BMP280 Pressure/Temp sensor"
install_package "adafruit-circuitpython-bme280" "BME280 Pressure/Temp/Humidity"
install_package "adafruit-circuitpython-ads1x15" "ADS1115 16-bit ADC"
install_package "sensirion-i2c-driver" "Sensirion I2C driver"
install_package "sensirion-i2c-sdp" "SDP811 differential pressure"
install_package "adafruit-circuitpython-mpu6050" "MPU6050 gyro/accelerometer"
install_package "smbus2" "XGZP6847A gauge pressure (smbus2)"
install_package "adafruit-circuitpython-ina219" "INA219 current/voltage sensor"
install_package "adafruit-circuitpython-vl53l0x" "VL53L0X laser distance sensor"

# SPI Sensors
install_package "hx711" "HX711 load cell amplifier"
install_package "rpi-lgpio" "RPi 5 GPIO support (lgpio)"
install_package "adafruit-circuitpython-mcp3xxx" "MCP3008 8-channel ADC"
install_package "adafruit-circuitpython-max31855" "MAX31855 thermocouple"

# 1-Wire / GPIO Sensors
install_package "w1thermsensor" "DS18B20 temperature sensor"
install_package "RPi.GPIO" "RPi GPIO library"
install_package "adafruit-circuitpython-dht" "DHT22 temp/humidity"

# Base libraries
install_package "adafruit-blinka" "CircuitPython Blinka layer"
install_package "adafruit-platformdetect" "Platform detection"

echo ""
echo "Sensor Library Installation Complete"
echo "-------------------------------------"
echo "Total: ${TOTAL_PACKAGES} packages"
echo "✓ Installed: ${SUCCESSFUL_PACKAGES}"
if [ $FAILED_PACKAGES -gt 0 ]; then
    echo "⚠ Skipped: ${FAILED_PACKAGES} (optional packages)"
fi
echo ""

if [ $SUCCESSFUL_PACKAGES -gt 0 ]; then
    echo "✓ Hardware sensor support is ready!"
    
    if [ $FAILED_PACKAGES -gt 0 ]; then
        echo "⚠ Some optional libraries were skipped - this is normal"
        echo "  Only sensors you actually connect will be used"
    fi
    
    if command -v raspi-config &> /dev/null; then
        echo "ℹ Hardware interfaces configured (reboot required)"
    fi
    
    exit 0
else
    echo "⚠ No sensor libraries could be installed"
    echo "  Check internet connection or package availability"
    echo "  Hardware sensors will not be available"
    echo "  Mock and calculated sensors will still work"
    exit 0
fi

echo ""
