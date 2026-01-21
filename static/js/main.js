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

// Local sparkline buffer - keeps last 50 points for each sensor for dashboard sparklines
const sparklineData = {}; // {sensorId: [value1, value2, ...]}
const MAX_SPARKLINE_POINTS = 50;

// Graph data cache - stores fetched data from server API
// Data is lazy-loaded and cached when viewing graphs
const graphDataCache = {}; // {sensorId: [{timestamp, value}, ...]}
const UPDATE_INTERVAL_MS = 200; // Fixed at 200ms (5Hz)

// Fullscreen graph variables
let currentGraphKey = null;
let fullscreenAnimationFrame = null;
let graphZoomX = 0.01; // X-axis zoom (time) - default to 10 seconds (20 points at 500ms)
let graphZoomY = 1.0; // Y-axis zoom (value range)
let graphStartTime = null;
let graphScrollOffset = 0; // Scroll back in time (0 = live, positive = seconds in past)
let historicalData = null; // Cache for historical data
let isLoadingHistorical = false;

// Heartbeat for fan safety monitoring
let heartbeatInterval = null;
const HEARTBEAT_INTERVAL_MS = 3000; // Send heartbeat every 3 seconds

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
    
    console.log('Generating sensor cards for:', sensors.length, 'sensors');
    
    // Initialize core UI elements
    elements = {
        timestamp: document.getElementById('timestamp'),
        statusDot: document.getElementById('statusDot'),
        statusText: document.getElementById('statusText')
    };
    
    sensors.forEach(sensor => {
        console.log('Processing sensor:', sensor.id, 'enabled:', sensor.enabled, 'showOnDashboard:', sensor.showOnDashboard);
        if (!sensor.enabled) return;
        if (sensor.showOnDashboard === false) return;  // Skip sensors hidden from dashboard
        
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
        console.log('Created card for sensor:', sensor.id);
        
        // Store element references
        elements[sensor.id] = document.getElementById(sensor.id);
        elements[sensor.id + '-unit'] = document.getElementById(sensor.id + '-unit');
        
        // Initialize sparkline data for this sensor
        if (!sparklineData[sensor.id]) {
            sparklineData[sensor.id] = [];
        }
    });
    
    console.log('Total cards created:', dataGrid.children.length);
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
    
    const timestamp = data.timestamp;
    
    // Update each sensor
    sensors.forEach(sensor => {
        if (!sensor.enabled || !elements[sensor.id]) return;
        
        let value = data[sensor.id];
        if (value === undefined || value === null) return;
        
        // Apply unit conversions for display
        let displayValue = value;
        if (sensor.id === 'velocity' || sensor.name.toLowerCase().includes('velocity')) {
            displayValue = convertVelocity(value);
        } else if (sensor.id === 'temperature' || sensor.name.toLowerCase().includes('temp')) {
            displayValue = convertTemperature(value);
        }
        
        // Determine decimal places based on sensor type
        let decimals = currentSettings.decimalPlaces || 2;
        if (sensor.id === 'rpm' || sensor.name.toLowerCase().includes('rpm')) {
            decimals = 0;
        }
        
        // Update display
        elements[sensor.id].textContent = formatNumber(displayValue, decimals);
        
        // Add to sparkline data buffer (keep last MAX_SPARKLINE_POINTS)
        if (!sparklineData[sensor.id]) {
            sparklineData[sensor.id] = [];
        }
        sparklineData[sensor.id].push(displayValue);
        if (sparklineData[sensor.id].length > MAX_SPARKLINE_POINTS) {
            sparklineData[sensor.id].shift();
        }
        
        // Add to graph cache for fullscreen graphs (use raw SI value, not converted)
        if (!graphDataCache[sensor.id]) {
            graphDataCache[sensor.id] = [];
        }
        graphDataCache[sensor.id].push({ timestamp: timestamp, value: value });
        
        // Keep only last 2 hours of data in cache (36000 points at 200ms = 2 hours)
        const MAX_CACHE_POINTS = 36000;
        if (graphDataCache[sensor.id].length > MAX_CACHE_POINTS) {
            graphDataCache[sensor.id].shift();
        }
    });
    
    // Update sparklines with new data
    updateAllSparklines()
    
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
    
    // Start heartbeat for fan safety monitoring
    startHeartbeat();
    
    // Request initial data
    socket.emit('request_data');
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
    
    // Stop heartbeat
    stopHeartbeat();
    
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

// Fan safety: emergency stop notification from server
socket.on('fan_emergency_stop', (data) => {
    console.warn('⚠️ Fan emergency stop:', data);
    alert(`⚠️ Fan Emergency Stop\n\nReason: ${data.reason}\nThe fan was automatically stopped after ${data.timeout} seconds without client connection.`);
    loadFanStatus(); // Update UI
});

// Heartbeat functions for fan safety monitoring
function startHeartbeat() {
    if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
    }
    heartbeatInterval = setInterval(() => {
        socket.emit('heartbeat');
    }, HEARTBEAT_INTERVAL_MS);
    console.log('Heartbeat started (every 3s for fan safety)');
}

function stopHeartbeat() {
    if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
        heartbeatInterval = null;
        console.log('Heartbeat stopped');
    }
}

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

// Initial connection message
console.log('Wind Tunnel Control System initialized');

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
    sensors.forEach(sensor => {
        if (!sensor.enabled) return;
        if (sensor.showOnDashboard === false) return;  // Skip hidden sensors
        const canvasId = 'sparkline-' + sensor.id;
        const data = sparklineData[sensor.id] || [];
        const color = sensor.color || '#3498db';
        drawSparkline(canvasId, data, color);
    });
}

// Touch handling for pinch zoom
let touchStartDistance = 0;
let touchStartAngle = 0;
let touchStartZoomX = 1.0;
let touchStartZoomY = 1.0;
let touchStartX = 0;
let touchStartY = 0;
let touchStartScrollOffset = 0;
let isPanning = false;

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
    graphZoomX = 0.01; // Show 10 seconds (20 points at 500ms intervals)
    graphZoomY = 1.0;
    graphScrollOffset = 0;
    updateZoomDisplay();
}

// Update zoom level display
function updateZoomDisplay() {
    document.getElementById('zoomX').textContent = Math.round(graphZoomX * 100) + '%';
    document.getElementById('zoomY').textContent = Math.round(graphZoomY * 100) + '%';
    
    // Update scroll indicator
    const scrollIndicator = document.getElementById('scrollIndicator');
    if (scrollIndicator) {
        if (graphScrollOffset > 0) {
            scrollIndicator.textContent = `📜 -${Math.round(graphScrollOffset)}s`;
            scrollIndicator.style.display = 'block';
        } else {
            scrollIndicator.style.display = 'none';
        }
    }
}

// Load historical data from server
async function loadHistoricalData(sensorId, startTime = null, endTime = null) {
    if (isLoadingHistorical) return null;
    
    isLoadingHistorical = true;
    try {
        // Default to last 30 minutes if not specified
        if (!endTime) endTime = Date.now() / 1000;
        if (!startTime) startTime = endTime - 1800; // 30 minutes
        
        const response = await fetch(
            `/api/historical-data?sensor=${sensorId}&start_time=${startTime}&end_time=${endTime}&max_points=100000`
        );
        const result = await response.json();
        
        if (result.status === 'success') {
            console.log(`Loaded ${result.data.length} points for ${sensorId} (${((endTime - startTime) / 60).toFixed(1)} minutes)`);
            return result.data; // [{timestamp, value}, ...]
        }
    } catch (error) {
        console.error('Failed to load historical data:', error);
    } finally {
        isLoadingHistorical = false;
    }
    return null;
}

// Open fullscreen graph
async function openFullscreenGraph(key, title) {
    currentGraphKey = key;
    graphZoomX = 0.01; // Show 10 seconds by default
    graphZoomY = 1.0;
    graphScrollOffset = 0;
    graphStartTime = Date.now();
    document.getElementById('graphModalTitle').textContent = title;
    document.getElementById('graphModal').style.display = 'flex';
    updateZoomDisplay();
    
    // Load initial 30 minutes of data from server
    const now = Date.now() / 1000;
    const data = await loadHistoricalData(key, now - 1800, now);
    if (data) {
        // Merge with any existing live data in cache
        const existingCache = graphDataCache[key] || [];
        
        // If we have existing data, merge intelligently
        if (existingCache.length > 0) {
            // Find the newest timestamp in loaded historical data
            const histNewest = data.length > 0 ? data[data.length - 1].timestamp : 0;
            
            // Keep only live data that's newer than historical data
            const newerLiveData = existingCache.filter(d => d.timestamp > histNewest);
            
            // Merge: historical + newer live data
            graphDataCache[key] = [...data, ...newerLiveData];
        } else {
            graphDataCache[key] = data;
        }
    } else {
        graphDataCache[key] = [];
    }
    
    const canvas = document.getElementById('fullscreenGraph');
    
    // Add mouse wheel listener for scrolling
    canvas.addEventListener('wheel', handleWheel, { passive: false });
    
    // Add touch event listeners for pinch zoom
    canvas.addEventListener('touchstart', handleTouchStart, { passive: false });
    canvas.addEventListener('touchmove', handleTouchMove, { passive: false });
    canvas.addEventListener('touchend', handleTouchEnd, { passive: false });
    
    // Start animation loop for fullscreen graph
    const drawFullscreenGraph = async () => {
        // Get cached data for this sensor
        let cachedData = graphDataCache[key] || [];
        
        if (cachedData.length === 0) {
            fullscreenAnimationFrame = requestAnimationFrame(drawFullscreenGraph);
            return;
        }
        
        // Check if we need to load more historical data
        const now = Date.now() / 1000;
        const oldestCached = cachedData.length > 0 ? cachedData[0].timestamp : now;
        const newestCached = cachedData.length > 0 ? cachedData[cachedData.length - 1].timestamp : now;
        const requestedOldest = now - graphScrollOffset - (graphZoomX * 1000);
        
        // If scrolled beyond cached data, fetch more
        if (requestedOldest < oldestCached - 60 && !isLoadingHistorical) {
            console.log('Loading more historical data...');
            const moreData = await loadHistoricalData(key, requestedOldest - 1800, oldestCached);
            if (moreData && moreData.length > 0) {
                // Prepend older data to cache
                graphDataCache[key] = [...moreData, ...cachedData];
                cachedData = graphDataCache[key];
                console.log(`Added ${moreData.length} older points`);
            }
        }
        
        // Calculate time window
        const timeWindowSeconds = graphZoomX * 1000; // Convert zoom to seconds (0.01 = 10s)
        
        // When viewing live data (scrollOffset = 0), use the actual latest data point as end time
        // This prevents the graph from appearing to move left as new data arrives
        let viewEndTime, viewStartTime;
        if (graphScrollOffset === 0 && cachedData.length > 0) {
            // Live mode: show up to the newest data point
            viewEndTime = cachedData[cachedData.length - 1].timestamp;
            viewStartTime = viewEndTime - timeWindowSeconds;
        } else {
            // Scrolled back in time
            viewEndTime = now - graphScrollOffset;
            viewStartTime = viewEndTime - timeWindowSeconds;
        }
        
        // Filter data to only points within the visible time window
        const visibleData = cachedData.filter(d => d.timestamp >= viewStartTime && d.timestamp <= viewEndTime);
        
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
        
        // Handle empty data
        if (visibleData.length === 0) {
            ctx.fillStyle = '#7f8c8d';
            ctx.font = '20px Segoe UI';
            ctx.textAlign = 'center';
            ctx.fillText('No data available for this time range', width / 2, height / 2);
            fullscreenAnimationFrame = requestAnimationFrame(drawFullscreenGraph);
            return;
        }
        
        // Extract values for min/max calculation
        const values = visibleData.map(d => d.value);
        
        // Find min and max
        const dataMin = Math.min(...values);
        const dataMax = Math.max(...values);
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
            
            // Time labels - calculate actual time for this position
            const timeAtPosition = viewStartTime + (i / numTimeLabels) * timeWindowSeconds;
            const secondsAgo = now - timeAtPosition;
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
        visibleData.forEach((dataPoint, index) => {
            // Position points based on actual timestamp within the visible time window
            const timeSinceStart = dataPoint.timestamp - viewStartTime;
            const xPosition = (timeSinceStart / timeWindowSeconds) * graphWidth;
            const x = padding + xPosition;
            const y = padding + graphHeight - ((dataPoint.value - min) / range) * graphHeight;
            
            // Only draw points within visible range
            if (y >= padding && y <= padding + graphHeight && x >= padding && x <= padding + graphWidth) {
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
        
        // Draw current value marker (only when viewing live data)
        if (visibleData.length > 0 && graphScrollOffset < 0.1) {
            const lastDataPoint = visibleData[visibleData.length - 1];
            const timeSinceStart = lastDataPoint.timestamp - viewStartTime;
            const xPosition = (timeSinceStart / timeWindowSeconds) * graphWidth;
            const x = padding + xPosition;
            const y = padding + graphHeight - ((lastDataPoint.value - min) / range) * graphHeight;
            
            // Only show marker if within visible range
            if (y >= padding && y <= padding + graphHeight && x >= padding && x <= padding + graphWidth) {
                ctx.fillStyle = '#e74c3c';
                ctx.beginPath();
                ctx.arc(x, y, 8, 0, Math.PI * 2);
                ctx.fill();
                
                // Value label with background
                ctx.fillStyle = '#2c3e50';
                ctx.font = 'bold 20px Segoe UI';
                ctx.textAlign = 'left';
                const valueText = formatNumber(lastDataPoint.value, 2);
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
        isPanning = false;
        touchStartDistance = getTouchDistance(e.touches[0], e.touches[1]);
        touchStartAngle = getTouchAngle(e.touches[0], e.touches[1]);
        touchStartZoomX = graphZoomX;
        touchStartZoomY = graphZoomY;
    } else if (e.touches.length === 1) {
        // Single touch - prepare for panning
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
        touchStartScrollOffset = graphScrollOffset;
        isPanning = true;
    }
}

function handleTouchMove(e) {
    if (e.touches.length === 2) {
        e.preventDefault();
        isPanning = false;
        
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
            graphZoomX = Math.max(0.01, Math.min(1.0, touchStartZoomX / zoomFactor));
        } else if (verticalWeight > 0.7) {
            // Mostly vertical - zoom Y axis
            graphZoomY = Math.max(0.01, Math.min(10.0, touchStartZoomY * zoomFactor));
        } else {
            // Diagonal - zoom both axes
            graphZoomX = Math.max(0.01, Math.min(1.0, touchStartZoomX / zoomFactor));
            graphZoomY = Math.max(0.01, Math.min(10.0, touchStartZoomY * zoomFactor));
        }
        
        updateZoomDisplay();
    } else if (e.touches.length === 1 && isPanning) {
        e.preventDefault();
        
        // Single touch - pan through time like dragging the graph
        const deltaX = e.touches[0].clientX - touchStartX;
        const canvas = document.getElementById('fullscreenGraph');
        const canvasWidth = canvas.width - 160; // Account for padding
        
        // Convert pixel movement to time offset with 1:1 sensitivity
        // Swipe right (positive deltaX) = graph moves right = see older data (increase offset)
        // Swipe left (negative deltaX) = graph moves left = see newer data (decrease offset)
        const updateIntervalSec = UPDATE_INTERVAL_MS / 1000;
        const timeWindowSeconds = graphZoomX * 1000; // Current time window
        const timePerPixel = timeWindowSeconds / canvasWidth;
        
        const deltaTime = deltaX * timePerPixel; // Direct 1:1 mapping
        const newOffset = Math.max(0, touchStartScrollOffset + deltaTime);
        
        // Limit scrolling to available data (use cached data)
        const cachedData = graphDataCache[currentGraphKey] || [];
        const oldestTime = cachedData.length > 0 ? cachedData[0].timestamp : 0;
        const newestTime = cachedData.length > 0 ? cachedData[cachedData.length - 1].timestamp : Date.now() / 1000;
        const maxOffset = Math.max(0, (Date.now() / 1000) - newestTime + (newestTime - oldestTime) - timeWindowSeconds);
        graphScrollOffset = Math.min(newOffset, maxOffset);
    }
}

function handleTouchEnd(e) {
    if (e.touches.length < 2) {
        touchStartDistance = 0;
        touchStartAngle = 0;
    }
    if (e.touches.length === 0) {
        isPanning = false;
    }
}

// Close fullscreen graph
function closeFullscreenGraph() {
    const canvas = document.getElementById('fullscreenGraph');
    
    // Remove event listeners
    canvas.removeEventListener('wheel', handleWheel);
    canvas.removeEventListener('touchstart', handleTouchStart);
    canvas.removeEventListener('touchmove', handleTouchMove);
    canvas.removeEventListener('touchend', handleTouchEnd);
    
    document.getElementById('graphModal').style.display = 'none';
    if (fullscreenAnimationFrame) {
        cancelAnimationFrame(fullscreenAnimationFrame);
        fullscreenAnimationFrame = null;
    }
    currentGraphKey = null;
    graphZoomX = 0.01;
    graphZoomY = 1.0;
    graphScrollOffset = 0;
    historicalData = null;
}

// Handle mouse wheel for scrolling through time
function handleWheel(e) {
    e.preventDefault();
    
    const updateIntervalSec = UPDATE_INTERVAL_MS / 1000;
    const maxScrollSeconds = 24 * 3600; // 24 hours max
    
    // Scroll speed: 1 second per wheel tick
    const scrollDelta = e.deltaY > 0 ? 1 : -1;
    
    graphScrollOffset = Math.max(0, Math.min(maxScrollSeconds, graphScrollOffset + scrollDelta));
    updateZoomDisplay();
}

// Make functions globally accessible
window.openFullscreenGraph = openFullscreenGraph;
window.closeFullscreenGraph = closeFullscreenGraph;
window.resetZoom = resetZoom;

// WiFi Status Updates
async function updateWiFiStatus() {
    try {
        const response = await fetch('/api/wifi/status');
        const data = await response.json();
        const wifiIcon = document.getElementById('wifiIcon');
        const wifiIndicator = document.getElementById('wifiIndicator');
        
        if (data.no_adapter) {
            // Hide WiFi indicator if no adapter
            wifiIndicator.style.display = 'none';
            return;
        }
        
        wifiIndicator.style.display = 'flex';
        
        if (data.connected) {
            // Update icon based on signal strength
            if (data.signal_percent >= 75) {
                wifiIcon.textContent = '📶'; // Full signal
                wifiIndicator.style.opacity = '1';
            } else if (data.signal_percent >= 50) {
                wifiIcon.textContent = '📶'; // Good signal
                wifiIndicator.style.opacity = '0.9';
            } else if (data.signal_percent >= 25) {
                wifiIcon.textContent = '📶'; // Fair signal
                wifiIndicator.style.opacity = '0.7';
            } else {
                wifiIcon.textContent = '📶'; // Weak signal
                wifiIndicator.style.opacity = '0.5';
            }
            wifiIndicator.title = `WiFi: ${data.ssid} (${data.signal_percent}%)`;
        } else {
            wifiIcon.textContent = '📵'; // No WiFi
            wifiIndicator.style.opacity = '0.4';
            wifiIndicator.title = 'WiFi: Not connected';
        }
    } catch (error) {
        console.error('Error updating WiFi status:', error);
        const wifiIndicator = document.getElementById('wifiIndicator');
        wifiIndicator.style.display = 'none'; // Hide on error
    }
}

// Update WiFi status every 10 seconds
setInterval(updateWiFiStatus, 10000);

// Export Modal Functions
let selectedUSBDrive = null;
let exportAbortController = null;

async function openExportModal() {
    const modal = document.getElementById('exportModal');
    modal.style.display = 'flex';
    selectedUSBDrive = null;
    await loadUSBDrives();
}

function closeExportModal() {
    const modal = document.getElementById('exportModal');
    modal.style.display = 'none';
    selectedUSBDrive = null;
    
    // Reset export UI
    document.getElementById('exportProgress').style.display = 'none';
    document.getElementById('exportButtons').style.display = 'flex';
    document.getElementById('usbDrivesList').style.display = 'block';
    document.getElementById('exportProgressBar').style.width = '0%';
    
    // Remove progress listener if still active
    socket.off('export_progress');
}

async function loadUSBDrives() {
    const usbList = document.getElementById('usbDrivesList');
    const exportStatus = document.getElementById('exportStatus');
    
    usbList.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">Loading USB drives...</p>';
    exportStatus.innerHTML = '<p style="color: var(--text-secondary); text-align: center;">Select a USB drive to export sensor data</p>';
    
    try {
        const response = await fetch('/api/export/usb-drives');
        const data = await response.json();
        
        if (data.drives && data.drives.length > 0) {
            usbList.innerHTML = data.drives.map(drive => `
                <div class="usb-drive-item" onclick="selectUSBDrive('${drive.path}', '${drive.name}')">
                    <div class="usb-drive-icon">💾</div>
                    <div class="usb-drive-info">
                        <div class="usb-drive-name">${drive.name}</div>
                        <div class="usb-drive-details">${drive.path} • ${drive.size}</div>
                    </div>
                </div>
            `).join('');
        } else {
            usbList.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 32px;">No USB drives detected. Please insert a USB drive and click Refresh.</p>';
        }
    } catch (error) {
        console.error('Error loading USB drives:', error);
        usbList.innerHTML = '<p style="text-align: center; color: var(--accent-color); padding: 32px;">Error loading USB drives</p>';
    }
}

function selectUSBDrive(path, name) {
    selectedUSBDrive = { path, name };
    
    // Update visual selection
    document.querySelectorAll('.usb-drive-item').forEach(item => {
        item.classList.remove('selected');
    });
    event.target.closest('.usb-drive-item').classList.add('selected');
    
    // Start export
    exportToUSB(path, name);
}

function cancelExport() {
    if (exportAbortController) {
        exportAbortController.abort();
        exportAbortController = null;
    }
    
    const progressDiv = document.getElementById('exportProgress');
    const statusDiv = document.getElementById('exportStatus');
    const usbList = document.getElementById('usbDrivesList');
    
    statusDiv.innerHTML = '<p style="color: var(--text-secondary); text-align: center;">Export cancelled</p>';
    progressDiv.style.display = 'none';
    usbList.style.display = 'block';
    
    // Reload drives list
    setTimeout(() => loadUSBDrives(), 1000);
}

async function exportToUSB(path, name) {
    const progressDiv = document.getElementById('exportProgress');
    const statusDiv = document.getElementById('exportStatus');
    const progressText = document.getElementById('exportProgressText');
    const progressDetail = document.getElementById('exportProgressDetail');
    const progressBar = document.getElementById('exportProgressBar');
    const usbList = document.getElementById('usbDrivesList');
    
    // Get time range selection
    const timeRange = document.querySelector('input[name="timeRange"]:checked').value;
    let timeValue = 0;
    if (timeRange === 'last_minutes') {
        timeValue = parseInt(document.getElementById('minutesValue').value) || 60;
    } else if (timeRange === 'last_hours') {
        timeValue = parseInt(document.getElementById('hoursValue').value) || 24;
    }
    
    progressDiv.style.display = 'block';
    usbList.style.display = 'none';
    document.getElementById('exportButtons').style.display = 'none';
    progressText.textContent = 'Preparing export...';
    progressDetail.textContent = `Exporting to ${name}`;
    progressBar.style.width = '0%';
    
    // Listen for progress updates via socket
    socket.on('export_progress', (data) => {
        progressBar.style.width = data.progress + '%';
        progressText.textContent = `Exporting data... ${data.progress}%`;
        progressDetail.textContent = `Processing ${data.current.toLocaleString()} of ${data.total.toLocaleString()} rows`;
    });
    
    // Create new abort controller for this export
    exportAbortController = new AbortController();
    
    try {
        const response = await fetch('/api/export/data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                drive_path: path,
                time_range: timeRange,
                time_value: timeValue
            }),
            signal: exportAbortController.signal
        });
        
        const data = await response.json();
        
        // Remove progress listener
        socket.off('export_progress');
        
        if (data.status === 'success') {
            progressBar.style.width = '100%';
            progressText.textContent = '✓ Export Complete!';
            progressDetail.textContent = `${data.rows_exported.toLocaleString()} rows × ${data.columns} columns exported to ${data.filename}`;
            setTimeout(() => {
                closeExportModal();
            }, 3000);
        } else {
            statusDiv.innerHTML = `<p style="color: var(--accent-color); text-align: center;">Export failed: ${data.message}</p>`;
            progressDiv.style.display = 'none';
            usbList.style.display = 'block';
            document.getElementById('exportButtons').style.display = 'flex';
        }
    } catch (error) {
        socket.off('export_progress');
        if (error.name === 'AbortError') {
            // Request was cancelled, already handled by cancelExport()
            console.log('Export cancelled by user');
            return;
        }
        console.error('Export error:', error);
        statusDiv.innerHTML = '<p style="color: var(--accent-color); text-align: center;">Export failed. Please try again.</p>';
        progressDiv.style.display = 'none';
        usbList.style.display = 'block';
        document.getElementById('exportButtons').style.display = 'flex';
    } finally {
        exportAbortController = null;
    }
}

function updateTimeRangeUI() {
    // Optional: Could disable/enable input fields based on selection
    // For now, radio buttons handle the logic
}

function cancelExport() {
    if (exportAbortController) {
        exportAbortController.abort();
        socket.off('export_progress');
        closeExportModal();
    }
}

async function refreshUSBDrives() {
    await loadUSBDrives();
}

// Fan Control Functions
function updateSpeedDisplay(value) {
    document.getElementById('speedValue').textContent = value;
}

async function startFan() {
    const speed = document.getElementById('fanSpeed').value;
    try {
        const response = await fetch('/api/fan/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ speed: parseInt(speed) })
        });
        const data = await response.json();
        
        if (data.status === 'success') {
            updateFanStatus(true, speed);
        } else {
            alert('Failed to start fan: ' + data.message);
        }
    } catch (error) {
        console.error('Error starting fan:', error);
        alert('Failed to start fan: ' + error.message);
    }
}

async function stopFan() {
    try {
        const response = await fetch('/api/fan/stop', {
            method: 'POST'
        });
        const data = await response.json();
        
        if (data.status === 'success') {
            updateFanStatus(false, 0);
        } else {
            alert('Failed to stop fan: ' + data.message);
        }
    } catch (error) {
        console.error('Error stopping fan:', error);
        alert('Failed to stop fan: ' + error.message);
    }
}

function updateFanStatus(isRunning, speed) {
    const indicator = document.getElementById('fanIndicator');
    const statusText = document.getElementById('fanStatusText');
    
    if (isRunning) {
        indicator.style.background = 'var(--success-color)';
        indicator.style.animation = 'pulse 1.5s ease-in-out infinite';
        statusText.style.color = 'var(--success-color)';
        statusText.textContent = `Fan: ON (${speed}%)`;
    } else {
        indicator.style.background = 'var(--text-secondary)';
        indicator.style.animation = 'none';
        statusText.style.color = 'var(--text-secondary)';
        statusText.textContent = 'Fan: OFF';
    }
}

// Load fan status on page load
async function loadFanStatus() {
    try {
        const response = await fetch('/api/fan/status');
        const data = await response.json();
        
        if (data.running) {
            document.getElementById('fanSpeed').value = data.speed;
            document.getElementById('speedValue').textContent = data.speed;
            updateFanStatus(true, data.speed);
        } else {
            updateFanStatus(false, 0);
        }
    } catch (error) {
        console.error('Error loading fan status:', error);
    }
}

// ==== PID CONTROL ====

function updateTargetDisplay(value) {
    document.getElementById('targetValue').textContent = parseFloat(value).toFixed(1);
}

async function startPID() {
    const targetSpeed = parseFloat(document.getElementById('targetAirspeed').value);
    
    if (isNaN(targetSpeed) || targetSpeed < 0) {
        alert('Please enter a valid target airspeed');
        return;
    }
    
    try {
        const response = await fetch('/api/pid/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_airspeed: targetSpeed })
        });
        const data = await response.json();
        
        if (data.status === 'success') {
            updatePIDStatus(true);
        } else {
            alert('Failed to start PID control: ' + (data.message || data.error || 'Unknown error'));
        }
    } catch (error) {
        console.error('Error starting PID:', error);
        alert('Failed to start PID control: ' + error.message);
    }
}

async function stopPID() {
    try {
        const response = await fetch('/api/pid/stop', {
            method: 'POST'
        });
        const data = await response.json();
        
        if (data.status === 'success') {
            updatePIDStatus(false);
        } else {
            alert('Failed to stop PID control: ' + (data.message || data.error || 'Unknown error'));
        }
    } catch (error) {
        console.error('Error stopping PID:', error);
        alert('Failed to stop PID control: ' + error.message);
    }
}

function updatePIDStatus(isRunning, data = {}) {
    const indicator = document.getElementById('pidIndicator');
    const statusText = document.getElementById('pidStatusText');
    
    if (isRunning) {
        indicator.style.background = 'var(--accent-color)';
        indicator.style.animation = 'pulse 1.5s ease-in-out infinite';
        statusText.style.color = 'var(--accent-color)';
        statusText.textContent = 'PID: ACTIVE';
        
        // Update values if provided
        if (data.target_speed !== undefined) {
            document.getElementById('pidTarget').textContent = data.target_speed.toFixed(1) + ' m/s';
        }
        if (data.current_speed !== undefined) {
            document.getElementById('pidActual').textContent = data.current_speed.toFixed(1) + ' m/s';
        }
        if (data.fan_speed !== undefined) {
            document.getElementById('pidFanSpeed').textContent = data.fan_speed.toFixed(0) + '%';
        }
    } else {
        indicator.style.background = 'var(--text-secondary)';
        indicator.style.animation = 'none';
        statusText.style.color = 'var(--text-secondary)';
        statusText.textContent = 'PID: OFF';
        
        // Reset values
        document.getElementById('pidTarget').textContent = '--';
        document.getElementById('pidActual').textContent = '--';
        document.getElementById('pidFanSpeed').textContent = '--';
    }
}

// Load PID status on page load
async function loadPIDStatus() {
    try {
        const response = await fetch('/api/pid/status');
        const data = await response.json();
        
        if (data.running) {
            updatePIDStatus(true, data);
            
            // Update target slider
            if (data.target_speed !== undefined) {
                document.getElementById('targetAirspeed').value = data.target_speed;
                document.getElementById('targetValue').textContent = data.target_speed.toFixed(1);
            }
            
            // Update sensor name if available
            if (data.sensor_id) {
                document.getElementById('pidSensorName').textContent = 'Sensor: ' + data.sensor_id;
            }
        } else {
            updatePIDStatus(false);
        }
    } catch (error) {
        console.error('Error loading PID status:', error);
    }
}

// Listen for PID status updates via SocketIO
socket.on('pid_status', function(data) {
    if (data.running) {
        updatePIDStatus(true, data);
        
        // Update sensor name
        if (data.sensor_id) {
            document.getElementById('pidSensorName').textContent = 'Sensor: ' + data.sensor_id;
        }
    } else {
        updatePIDStatus(false);
    }
});

// Close modal when clicking outside
document.addEventListener('click', function(event) {
    const exportModal = document.getElementById('exportModal');
    if (event.target === exportModal) {
        closeExportModal();
    }
});

// Initialize on page load
loadConfiguration();
updateWiFiStatus();
loadFanStatus();
loadPIDStatus();
