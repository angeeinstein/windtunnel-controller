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
        updateUnitLabels(); // Update unit labels on load
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
    
    // Add data points to graphs (use converted values for display)
    addGraphDataPoint('velocity', velocity);
    addGraphDataPoint('lift', data.lift);
    addGraphDataPoint('drag', data.drag);
    addGraphDataPoint('pressure', data.pressure);
    addGraphDataPoint('temperature', temperature);
    addGraphDataPoint('rpm', data.rpm);
    addGraphDataPoint('power', data.power);
    if (liftDragRatio !== '--') {
        addGraphDataPoint('liftDragRatio', parseFloat(liftDragRatio));
    }
    
    // Update sparklines
    updateAllSparklines();
    
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

// Graph data storage (keep last 50 data points for sparklines)
const graphData = {
    velocity: [],
    lift: [],
    drag: [],
    pressure: [],
    temperature: [],
    rpm: [],
    power: [],
    liftDragRatio: []
};

const MAX_GRAPH_POINTS = 50;

// Add data point to graph
function addGraphDataPoint(key, value) {
    if (!graphData[key]) {
        graphData[key] = [];
    }
    graphData[key].push(value);
    if (graphData[key].length > MAX_GRAPH_POINTS) {
        graphData[key].shift();
    }
}

// Draw sparkline
function drawSparkline(canvasId, data, color = '#3498db') {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !data || data.length === 0) return;
    
    const ctx = canvas.getContext('2d');
    const width = canvas.width = canvas.offsetWidth * 2; // High DPI
    const height = canvas.height = canvas.offsetHeight * 2;
    ctx.scale(2, 2);
    
    const displayWidth = width / 2;
    const displayHeight = height / 2;
    
    // Clear canvas
    ctx.clearRect(0, 0, displayWidth, displayHeight);
    
    // Find min and max for scaling
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1; // Avoid division by zero
    
    // Draw line
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    
    ctx.beginPath();
    data.forEach((value, index) => {
        const x = (index / (data.length - 1 || 1)) * displayWidth;
        const y = displayHeight - ((value - min) / range) * (displayHeight - 10) - 5;
        
        if (index === 0) {
            ctx.moveTo(x, y);
        } else {
            ctx.lineTo(x, y);
        }
    });
    ctx.stroke();
    
    // Draw fill gradient
    ctx.lineTo(displayWidth, displayHeight);
    ctx.lineTo(0, displayHeight);
    ctx.closePath();
    
    const gradient = ctx.createLinearGradient(0, 0, 0, displayHeight);
    gradient.addColorStop(0, color + '40');
    gradient.addColorStop(1, color + '00');
    ctx.fillStyle = gradient;
    ctx.fill();
}

// Draw all sparklines
function updateAllSparklines() {
    drawSparkline('sparkline-velocity', graphData.velocity, '#e74c3c');
    drawSparkline('sparkline-lift', graphData.lift, '#e74c3c');
    drawSparkline('sparkline-drag', graphData.drag, '#e74c3c');
    drawSparkline('sparkline-pressure', graphData.pressure, '#3498db');
    drawSparkline('sparkline-temperature', graphData.temperature, '#3498db');
    drawSparkline('sparkline-rpm', graphData.rpm, '#3498db');
    drawSparkline('sparkline-power', graphData.power, '#3498db');
    drawSparkline('sparkline-liftDragRatio', graphData.liftDragRatio, '#27ae60');
}

// Fullscreen graph variables
let currentGraphKey = null;
let fullscreenAnimationFrame = null;

// Open fullscreen graph
function openFullscreenGraph(key, title) {
    currentGraphKey = key;
    document.getElementById('graphModalTitle').textContent = title;
    document.getElementById('graphModal').style.display = 'flex';
    
    // Start animation loop for fullscreen graph
    const drawFullscreenGraph = () => {
        const canvas = document.getElementById('fullscreenGraph');
        const data = graphData[key];
        
        if (!data || data.length === 0) {
            fullscreenAnimationFrame = requestAnimationFrame(drawFullscreenGraph);
            return;
        }
        
        const ctx = canvas.getContext('2d');
        const container = canvas.parentElement;
        canvas.width = container.offsetWidth;
        canvas.height = container.offsetHeight;
        
        const width = canvas.width;
        const height = canvas.height;
        const padding = 60;
        const graphWidth = width - 2 * padding;
        const graphHeight = height - 2 * padding;
        
        // Clear canvas
        ctx.fillStyle = '#f8f9fa';
        ctx.fillRect(0, 0, width, height);
        
        // Find min and max
        const min = Math.min(...data);
        const max = Math.max(...data);
        const range = max - min || 1;
        
        // Draw grid lines
        ctx.strokeStyle = '#dfe6e9';
        ctx.lineWidth = 1;
        
        // Horizontal grid lines
        for (let i = 0; i <= 5; i++) {
            const y = padding + (i / 5) * graphHeight;
            ctx.beginPath();
            ctx.moveTo(padding, y);
            ctx.lineTo(width - padding, y);
            ctx.stroke();
            
            // Y-axis labels
            const value = max - (i / 5) * range;
            ctx.fillStyle = '#2c3e50';
            ctx.font = '14px Segoe UI';
            ctx.textAlign = 'right';
            ctx.fillText(formatNumber(value, 2), padding - 10, y + 5);
        }
        
        // Vertical grid lines
        const timeStep = Math.ceil(data.length / 10);
        for (let i = 0; i <= 10; i++) {
            const x = padding + (i / 10) * graphWidth;
            ctx.beginPath();
            ctx.moveTo(x, padding);
            ctx.lineTo(x, height - padding);
            ctx.stroke();
        }
        
        // Draw data line
        ctx.strokeStyle = '#3498db';
        ctx.lineWidth = 3;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        
        ctx.beginPath();
        data.forEach((value, index) => {
            const x = padding + (index / (data.length - 1 || 1)) * graphWidth;
            const y = height - padding - ((value - min) / range) * graphHeight;
            
            if (index === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        });
        ctx.stroke();
        
        // Draw fill
        ctx.lineTo(padding + graphWidth, height - padding);
        ctx.lineTo(padding, height - padding);
        ctx.closePath();
        
        const gradient = ctx.createLinearGradient(0, padding, 0, height - padding);
        gradient.addColorStop(0, '#3498db60');
        gradient.addColorStop(1, '#3498db00');
        ctx.fillStyle = gradient;
        ctx.fill();
        
        // Draw current value marker
        if (data.length > 0) {
            const lastValue = data[data.length - 1];
            const x = padding + graphWidth;
            const y = height - padding - ((lastValue - min) / range) * graphHeight;
            
            ctx.fillStyle = '#e74c3c';
            ctx.beginPath();
            ctx.arc(x, y, 6, 0, Math.PI * 2);
            ctx.fill();
            
            // Value label
            ctx.fillStyle = '#2c3e50';
            ctx.font = 'bold 18px Segoe UI';
            ctx.textAlign = 'left';
            ctx.fillText(formatNumber(lastValue, 2), x + 15, y + 6);
        }
        
        fullscreenAnimationFrame = requestAnimationFrame(drawFullscreenGraph);
    };
    
    drawFullscreenGraph();
}

// Close fullscreen graph
function closeFullscreenGraph() {
    document.getElementById('graphModal').style.display = 'none';
    if (fullscreenAnimationFrame) {
        cancelAnimationFrame(fullscreenAnimationFrame);
        fullscreenAnimationFrame = null;
    }
    currentGraphKey = null;
}

// Make functions globally accessible
window.openFullscreenGraph = openFullscreenGraph;
window.closeFullscreenGraph = closeFullscreenGraph;

