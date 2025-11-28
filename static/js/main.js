// WebSocket connection
const socket = io();

// Current settings (loaded from server)
let currentSettings = {
    velocityUnit: 'ms',
    temperatureUnit: 'c',
    decimalPlaces: 2
};

// Load settings from server
fetch('/api/settings')
    .then(response => response.json())
    .then(settings => {
        currentSettings = settings;
        console.log('Settings loaded:', settings);
    })
    .catch(error => console.error('Failed to load settings:', error));

// Listen for settings updates
socket.on('settings_updated', (settings) => {
    currentSettings = settings;
    console.log('Settings updated:', settings);
    updateUnitLabels();
});

// Update unit labels in UI
function updateUnitLabels() {
    const velocityUnitEl = document.getElementById('velocity-unit');
    const tempUnitEl = document.getElementById('temperature-unit');
    
    if (velocityUnitEl) velocityUnitEl.textContent = getVelocityUnit();
    if (tempUnitEl) tempUnitEl.textContent = getTemperatureUnit();
}

// Unit conversion functions
function convertVelocity(ms) {
    switch(currentSettings.velocityUnit) {
        case 'kmh': return ms * 3.6;
        case 'mph': return ms * 2.237;
        case 'knots': return ms * 1.944;
        default: return ms; // m/s
    }
}

function convertTemperature(celsius) {
    switch(currentSettings.temperatureUnit) {
        case 'f': return celsius * 9/5 + 32;
        case 'k': return celsius + 273.15;
        default: return celsius; // °C
    }
}

function getVelocityUnit() {
    const units = { 'ms': 'm/s', 'kmh': 'km/h', 'mph': 'mph', 'knots': 'knots' };
    return units[currentSettings.velocityUnit] || 'm/s';
}

function getTemperatureUnit() {
    const units = { 'c': '°C', 'f': '°F', 'k': 'K' };
    return units[currentSettings.temperatureUnit] || '°C';
}

// DOM elements
const elements = {
    velocity: document.getElementById('velocity'),
    lift: document.getElementById('lift'),
    drag: document.getElementById('drag'),
    pressure: document.getElementById('pressure'),
    temperature: document.getElementById('temperature'),
    rpm: document.getElementById('rpm'),
    power: document.getElementById('power'),
    liftDragRatio: document.getElementById('liftDragRatio'),
    timestamp: document.getElementById('timestamp'),
    statusDot: document.getElementById('statusDot'),
    statusText: document.getElementById('statusText')
};

// Format timestamp
function formatTimestamp(timestamp) {
    const date = new Date(timestamp * 1000);
    return date.toLocaleTimeString('en-US', { 
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

// Format number with European formatting (comma for decimal, space for thousands)
function formatNumber(number, decimals) {
    // Use settings decimal places if not specified
    if (decimals === undefined) {
        decimals = currentSettings.decimalPlaces || 2;
    }
    
    if (number === null || number === undefined || isNaN(number)) {
        return '--';
    }
    
    // Format with fixed decimals
    const fixed = Number(number).toFixed(decimals);
    
    // Split into integer and decimal parts
    const parts = fixed.split('.');
    const integerPart = parts[0];
    const decimalPart = parts[1];
    
    // Add space for thousands separator
    const withSpaces = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    
    // Join with comma as decimal separator
    return decimalPart ? `${withSpaces},${decimalPart}` : withSpaces;
}

// Calculate lift/drag ratio
function calculateLiftDragRatio(lift, drag) {
    if (drag === 0 || drag === null || drag === undefined) {
        return '--';
    }
    const ratio = lift / drag;
    return ratio.toFixed(2);
}

// Update display with new data
function updateDisplay(data) {
    // Apply unit conversions
    const velocity = convertVelocity(data.velocity);
    const temperature = convertTemperature(data.temperature);
    
    // Update primary measurements with conversions
    elements.velocity.textContent = formatNumber(velocity);
    elements.lift.textContent = formatNumber(data.lift);
    elements.drag.textContent = formatNumber(data.drag);
    
    // Update secondary measurements
    elements.pressure.textContent = formatNumber(data.pressure);
    elements.temperature.textContent = formatNumber(temperature);
    elements.rpm.textContent = formatNumber(data.rpm, 0);
    elements.power.textContent = formatNumber(data.power);
    
    // Update calculated values
    const liftDragRatio = calculateLiftDragRatio(data.lift, data.drag);
    elements.liftDragRatio.textContent = liftDragRatio === '--' ? '--' : formatNumber(parseFloat(liftDragRatio));
    
    // Update timestamp
    elements.timestamp.textContent = formatTimestamp(data.timestamp);
    
    // Add brief highlight animation to updated values
    Object.values(elements).forEach(el => {
        if (el && el.classList) {
            el.classList.remove('updated');
            void el.offsetWidth; // Trigger reflow
            el.classList.add('updated');
        }
    });
}

// Connection status handlers
socket.on('connect', () => {
    console.log('Connected to server');
    elements.statusDot.classList.add('connected');
    elements.statusText.textContent = 'Connected';
    
    // Request initial data
    socket.emit('request_data');
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
    elements.statusDot.classList.remove('connected');
    elements.statusText.textContent = 'Disconnected';
});

socket.on('connect_error', (error) => {
    console.error('Connection error:', error);
    elements.statusText.textContent = 'Connection Error';
});

// Data update handler
socket.on('data_update', (data) => {
    console.log('Received data:', data);
    updateDisplay(data);
});

// Periodic data request (backup in case WebSocket updates fail)
setInterval(() => {
    if (socket.connected) {
        socket.emit('request_data');
    }
}, 5000); // Request every 5 seconds as backup

// Control button handlers (for future implementation)
document.getElementById('startBtn').addEventListener('click', () => {
    console.log('Start button clicked');
    // TODO: Implement start functionality
});

document.getElementById('stopBtn').addEventListener('click', () => {
    console.log('Stop button clicked');
    // TODO: Implement stop functionality
});

document.getElementById('resetBtn').addEventListener('click', () => {
    console.log('Reset button clicked');
    // TODO: Implement reset functionality
});

// Initial connection message
console.log('Wind Tunnel Control System initialized');
