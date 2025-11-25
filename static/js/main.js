// WebSocket connection
const socket = io();

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
    // Update primary measurements
    elements.velocity.textContent = data.velocity.toFixed(2);
    elements.lift.textContent = data.lift.toFixed(2);
    elements.drag.textContent = data.drag.toFixed(2);
    
    // Update secondary measurements
    elements.pressure.textContent = data.pressure.toFixed(3);
    elements.temperature.textContent = data.temperature.toFixed(1);
    elements.rpm.textContent = data.rpm.toLocaleString();
    elements.power.textContent = data.power.toFixed(1);
    
    // Update calculated values
    elements.liftDragRatio.textContent = calculateLiftDragRatio(data.lift, data.drag);
    
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
