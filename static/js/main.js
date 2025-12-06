// WebSocket connection
const socket = io();

// Current settings and sensors (loaded from server)
let currentSettings = {
    velocityUnit: 'ms',
    temperatureUnit: 'c',
    decimalPlaces: 2,
    sensors: []
};

let sensors = [];
let elements = {};

// Load settings and sensors from server
async function loadConfiguration() {
    try {
        const settingsResponse = await fetch('/api/settings');
        currentSettings = await settingsResponse.json();
        
        const sensorsResponse = await fetch('/api/sensors');
        sensors = await sensorsResponse.json();
        
        console.log('Configuration loaded:', { settings: currentSettings, sensors });
        
        // Generate sensor cards
        generateSensorCards();
        updateUnitLabels();
    } catch (error) {
        console.error('Failed to load configuration:', error);
    }
}

// Generate sensor cards dynamically
function generateSensorCards() {
    const dataGrid = document.getElementById('dataGrid');
    dataGrid.innerHTML = '';
    
    // Initialize core UI elements
    elements = {
        timestamp: document.getElementById('timestamp'),
        statusDot: document.getElementById('statusDot'),
        statusText: document.getElementById('statusText')
    };
    
    sensors.forEach(sensor => {
        if (!sensor.enabled) return;
        
        const card = document.createElement('div');
        const cardClass = sensor.type === 'calculated' ? 'data-card calculated' : 
                         (sensor.color === '#e74c3c' ? 'data-card primary' : 'data-card');
        card.className = cardClass;
        card.onclick = () => openFullscreenGraph(sensor.id, sensor.name);
        
        card.innerHTML = `
            <h2>${sensor.name}</h2>
            <div class="value-display">
                <span class="value" id="${sensor.id}">--</span>
                <span class="unit" id="${sensor.id}-unit">${sensor.unit}</span>
            </div>
            <canvas class="sparkline" id="sparkline-${sensor.id}"></canvas>
        `;
        
        dataGrid.appendChild(card);
        
        // Store element references
        elements[sensor.id] = document.getElementById(sensor.id);
        elements[sensor.id + '-unit'] = document.getElementById(sensor.id + '-unit');
        
        // Initialize graph data for this sensor
        if (!graphData[sensor.id]) {
            graphData[sensor.id] = [];
        }
    });
}

// Listen for settings updates
socket.on('settings_updated', (settings) => {
    currentSettings = settings;
    console.log('Settings updated:', settings);
    updateUnitLabels();
    // Reload sensors if needed
    loadConfiguration();
});

// Update unit labels in UI
function updateUnitLabels() {
    // Update velocity and temperature units for sensors that use them
    sensors.forEach(sensor => {
        const unitEl = document.getElementById(sensor.id + '-unit');
        if (!unitEl) return;
        
        // Check if this sensor should have unit conversion
        if (sensor.id === 'velocity' || sensor.name.toLowerCase().includes('velocity')) {
            unitEl.textContent = getVelocityUnit();
        } else if (sensor.id === 'temperature' || sensor.name.toLowerCase().includes('temp')) {
            unitEl.textContent = getTemperatureUnit();
        }
    });
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

// Update display with new data
function updateDisplay(data) {
    // Update timestamp
    if (elements.timestamp) {
        elements.timestamp.textContent = formatTimestamp(data.timestamp);
    }
    
    // Update each sensor
    sensors.forEach(sensor => {
        if (!sensor.enabled || !elements[sensor.id]) return;
        
        let value = data[sensor.id];
        if (value === undefined || value === null) return;
        
        // Apply unit conversions for specific sensor types
        if (sensor.id === 'velocity' || sensor.name.toLowerCase().includes('velocity')) {
            value = convertVelocity(value);
        } else if (sensor.id === 'temperature' || sensor.name.toLowerCase().includes('temp')) {
            value = convertTemperature(value);
        }
        
        // Determine decimal places based on sensor type
        let decimals = currentSettings.decimalPlaces || 2;
        if (sensor.id === 'rpm' || sensor.name.toLowerCase().includes('rpm')) {
            decimals = 0;
        }
        
        // Update display
        elements[sensor.id].textContent = formatNumber(value, decimals);
        
        // Add data points to graphs
        addGraphDataPoint(sensor.id, value);
    });
    
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
    loadConfiguration(); // Load sensors on connect
    
    // Update status indicators (use safe access)
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    if (statusDot) statusDot.classList.add('connected');
    if (statusText) statusText.textContent = 'Connected';
    
    // Request initial data
    socket.emit('request_data');
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    if (statusDot) statusDot.classList.remove('connected');
    if (statusText) statusText.textContent = 'Disconnected';
});

socket.on('connect_error', (error) => {
    console.error('Connection error:', error);
    const statusText = document.getElementById('statusText');
    if (statusText) statusText.textContent = 'Connection Error';
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

// Graph data storage (keep last 500 data points, display 50 by default in sparklines)
const graphData = {};

const MAX_GRAPH_POINTS = 500;
const DEFAULT_DISPLAY_POINTS = 50;

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
// Draw all sparklines
function updateAllSparklines() {
    sensors.forEach(sensor => {
        if (!sensor.enabled) return;
        const canvasId = 'sparkline-' + sensor.id;
        const allData = graphData[sensor.id] || [];
        // Only show last 50 points in sparkline
        const displayData = allData.slice(-DEFAULT_DISPLAY_POINTS);
        const color = sensor.color || '#3498db';
        drawSparkline(canvasId, displayData, color);
    });
}

// Fullscreen graph variables
let currentGraphKey = null;
let fullscreenAnimationFrame = null;
let graphZoomX = 1.0; // X-axis zoom (time)
let graphZoomY = 1.0; // Y-axis zoom (value range)
let graphStartTime = null;

// Touch handling for pinch zoom
let touchStartDistance = 0;
let touchStartAngle = 0;
let touchStartZoomX = 1.0;
let touchStartZoomY = 1.0;

// Calculate distance between two touch points
function getTouchDistance(touch1, touch2) {
    const dx = touch2.clientX - touch1.clientX;
    const dy = touch2.clientY - touch1.clientY;
    return Math.sqrt(dx * dx + dy * dy);
}

// Calculate angle of pinch gesture (0 = horizontal, 90 = vertical)
function getTouchAngle(touch1, touch2) {
    const dx = Math.abs(touch2.clientX - touch1.clientX);
    const dy = Math.abs(touch2.clientY - touch1.clientY);
    return Math.atan2(dy, dx) * (180 / Math.PI);
}

// Reset zoom to default
function resetZoom() {
    graphZoomX = 1.0;
    graphZoomY = 1.0;
    updateZoomDisplay();
}

// Update zoom level display
function updateZoomDisplay() {
    document.getElementById('zoomX').textContent = Math.round(graphZoomX * 100) + '%';
    document.getElementById('zoomY').textContent = Math.round(graphZoomY * 100) + '%';
}

// Open fullscreen graph
function openFullscreenGraph(key, title) {
    currentGraphKey = key;
    graphZoomX = 1.0;
    graphZoomY = 1.0;
    graphStartTime = Date.now();
    document.getElementById('graphModalTitle').textContent = title;
    document.getElementById('graphModal').style.display = 'flex';
    updateZoomDisplay();
    
    const canvas = document.getElementById('fullscreenGraph');
    
    // Add touch event listeners for pinch zoom
    canvas.addEventListener('touchstart', handleTouchStart, { passive: false });
    canvas.addEventListener('touchmove', handleTouchMove, { passive: false });
    canvas.addEventListener('touchend', handleTouchEnd, { passive: false });
    
    // Start animation loop for fullscreen graph
    const drawFullscreenGraph = () => {
        const allData = graphData[key];
        
        if (!allData || allData.length === 0) {
            fullscreenAnimationFrame = requestAnimationFrame(drawFullscreenGraph);
            return;
        }
        
        // Calculate how many points to show based on X zoom
        const pointsToShow = Math.max(5, Math.floor(allData.length / graphZoomX));
        const startIndex = Math.max(0, allData.length - pointsToShow);
        const data = allData.slice(startIndex);
        
        const ctx = canvas.getContext('2d');
        const container = canvas.parentElement;
        canvas.width = container.offsetWidth;
        canvas.height = container.offsetHeight;
        
        const width = canvas.width;
        const height = canvas.height;
        const padding = 80;
        const bottomPadding = 100;
        const graphWidth = width - 2 * padding;
        const graphHeight = height - padding - bottomPadding;
        
        // Clear canvas
        ctx.fillStyle = '#f8f9fa';
        ctx.fillRect(0, 0, width, height);
        
        // Find min and max
        const dataMin = Math.min(...data);
        const dataMax = Math.max(...data);
        const dataRange = dataMax - dataMin || 1;
        
        // Apply Y-axis zoom by adjusting the visible range
        const center = (dataMax + dataMin) / 2;
        const zoomedRange = dataRange / graphZoomY;
        const min = center - zoomedRange / 2;
        const max = center + zoomedRange / 2;
        const range = max - min;
        
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
            ctx.font = 'bold 16px Segoe UI';
            ctx.textAlign = 'right';
            ctx.fillText(formatNumber(value, 2), padding - 10, y + 5);
        }
        
        // Vertical grid lines and time labels
        const numTimeLabels = 6;
        for (let i = 0; i <= numTimeLabels; i++) {
            const x = padding + (i / numTimeLabels) * graphWidth;
            ctx.strokeStyle = '#dfe6e9';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x, padding);
            ctx.lineTo(x, padding + graphHeight);
            ctx.stroke();
            
            // Time labels (seconds ago)
            const dataIndex = Math.floor((i / numTimeLabels) * (data.length - 1));
            const secondsAgo = (allData.length - startIndex - dataIndex) * (currentSettings.updateInterval || 500) / 1000;
            ctx.fillStyle = '#2c3e50';
            ctx.font = '14px Segoe UI';
            ctx.textAlign = 'center';
            ctx.fillText('-' + secondsAgo.toFixed(1) + 's', x, padding + graphHeight + 25);
        }
        
        // Time axis label
        ctx.fillStyle = '#2c3e50';
        ctx.font = 'bold 18px Segoe UI';
        ctx.textAlign = 'center';
        ctx.fillText('Time (seconds ago)', width / 2, height - 20);
        
        // Draw data line
        ctx.strokeStyle = '#3498db';
        ctx.lineWidth = 3;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        
        ctx.beginPath();
        let pathStarted = false;
        data.forEach((value, index) => {
            const x = padding + (index / (data.length - 1 || 1)) * graphWidth;
            const y = padding + graphHeight - ((value - min) / range) * graphHeight;
            
            // Only draw points within visible range
            if (y >= padding && y <= padding + graphHeight) {
                if (!pathStarted) {
                    ctx.moveTo(x, y);
                    pathStarted = true;
                } else {
                    ctx.lineTo(x, y);
                }
            }
        });
        ctx.stroke();
        
        // Draw fill
        if (pathStarted) {
            ctx.lineTo(padding + graphWidth, padding + graphHeight);
            ctx.lineTo(padding, padding + graphHeight);
            ctx.closePath();
            
            const gradient = ctx.createLinearGradient(0, padding, 0, padding + graphHeight);
            gradient.addColorStop(0, '#3498db60');
            gradient.addColorStop(1, '#3498db00');
            ctx.fillStyle = gradient;
            ctx.fill();
        }
        
        // Draw current value marker
        if (data.length > 0) {
            const lastValue = data[data.length - 1];
            const x = padding + graphWidth;
            const y = padding + graphHeight - ((lastValue - min) / range) * graphHeight;
            
            // Only show marker if within visible range
            if (y >= padding && y <= padding + graphHeight) {
                ctx.fillStyle = '#e74c3c';
                ctx.beginPath();
                ctx.arc(x, y, 8, 0, Math.PI * 2);
                ctx.fill();
                
                // Value label with background
                ctx.fillStyle = '#2c3e50';
                ctx.font = 'bold 20px Segoe UI';
                ctx.textAlign = 'left';
                const valueText = formatNumber(lastValue, 2);
                const textWidth = ctx.measureText(valueText).width;
                
                // Draw background for text
                ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
                ctx.fillRect(x + 10, y - 15, textWidth + 10, 30);
                
                // Draw text
                ctx.fillStyle = '#2c3e50';
                ctx.fillText(valueText, x + 15, y + 7);
            }
        }
        
        fullscreenAnimationFrame = requestAnimationFrame(drawFullscreenGraph);
    };
    
    drawFullscreenGraph();
}

// Touch event handlers
function handleTouchStart(e) {
    if (e.touches.length === 2) {
        e.preventDefault();
        touchStartDistance = getTouchDistance(e.touches[0], e.touches[1]);
        touchStartAngle = getTouchAngle(e.touches[0], e.touches[1]);
        touchStartZoomX = graphZoomX;
        touchStartZoomY = graphZoomY;
    }
}

function handleTouchMove(e) {
    if (e.touches.length === 2) {
        e.preventDefault();
        
        const currentDistance = getTouchDistance(e.touches[0], e.touches[1]);
        const currentAngle = getTouchAngle(e.touches[0], e.touches[1]);
        const zoomFactor = currentDistance / touchStartDistance;
        
        // Determine if pinch is more horizontal or vertical
        // angle near 0° = horizontal = zoom X
        // angle near 90° = vertical = zoom Y
        // angle near 45° = diagonal = zoom both
        
        const horizontalWeight = Math.cos(currentAngle * Math.PI / 180);
        const verticalWeight = Math.sin(currentAngle * Math.PI / 180);
        
        // Apply zoom based on gesture direction
        if (horizontalWeight > 0.7) {
            // Mostly horizontal - zoom X axis
            graphZoomX = Math.max(0.2, Math.min(10.0, touchStartZoomX * zoomFactor));
        } else if (verticalWeight > 0.7) {
            // Mostly vertical - zoom Y axis
            graphZoomY = Math.max(0.2, Math.min(10.0, touchStartZoomY * zoomFactor));
        } else {
            // Diagonal - zoom both axes
            graphZoomX = Math.max(0.2, Math.min(10.0, touchStartZoomX * zoomFactor));
            graphZoomY = Math.max(0.2, Math.min(10.0, touchStartZoomY * zoomFactor));
        }
        
        updateZoomDisplay();
    }
}

function handleTouchEnd(e) {
    if (e.touches.length < 2) {
        touchStartDistance = 0;
        touchStartAngle = 0;
    }
}

// Close fullscreen graph
function closeFullscreenGraph() {
    const canvas = document.getElementById('fullscreenGraph');
    
    // Remove touch event listeners
    canvas.removeEventListener('touchstart', handleTouchStart);
    canvas.removeEventListener('touchmove', handleTouchMove);
    canvas.removeEventListener('touchend', handleTouchEnd);
    
    document.getElementById('graphModal').style.display = 'none';
    if (fullscreenAnimationFrame) {
        cancelAnimationFrame(fullscreenAnimationFrame);
        fullscreenAnimationFrame = null;
    }
    currentGraphKey = null;
    graphZoomX = 1.0;
    graphZoomY = 1.0;
}

// Make functions globally accessible
window.openFullscreenGraph = openFullscreenGraph;
window.closeFullscreenGraph = closeFullscreenGraph;
window.resetZoom = resetZoom;

// Initialize on page load
loadConfiguration();
