#!/bin/bash

###########################################
# Wind Tunnel Controller - Installation Script
# Automated installation for Raspberry Pi 5
# Supports fresh install, updates, and uninstall
###########################################

set -e  # Exit on error

# Set proper PATH for systemd compatibility
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Configuration
REPO_URL="https://github.com/angeeinstein/windtunnel-controller.git"
INSTALL_DIR="$HOME/windtunnel-controller"
SERVICE_NAME="windtunnel"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
VENV_DIR="$INSTALL_DIR/venv"
PYTHON_CMD="python3"
PIP_CMD="pip3"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

###########################################
# Helper Functions
###########################################

print_header() {
    echo -e "${BLUE}"
    echo "============================================"
    echo "  Wind Tunnel Controller - Installation"
    echo "============================================"
    echo -e "${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}➜ $1${NC}"
}

print_step() {
    echo -e "${BLUE}[STEP] $1${NC}"
}

# Check if running with sudo when needed
check_sudo() {
    if [[ $EUID -ne 0 ]] && [[ "$1" == "required" ]]; then
        print_error "This script must be run with sudo privileges"
        echo "Please run: sudo bash install.sh"
        exit 1
    fi
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check if running on supported system
check_system() {
    print_step "Checking system compatibility..."
    
    if [[ ! -f /etc/os-release ]]; then
        print_error "Cannot determine OS. This script is designed for Debian/Ubuntu/Raspberry Pi OS."
        exit 1
    fi
    
    . /etc/os-release
    
    if [[ "$ID" != "debian" && "$ID" != "ubuntu" && "$ID" != "raspbian" ]]; then
        print_error "Unsupported OS: $ID"
        print_info "This script is designed for Debian/Ubuntu/Raspberry Pi OS"
        exit 1
    fi
    
    print_success "Running on $PRETTY_NAME"
}

# Install system packages
install_system_packages() {
    print_step "Installing system packages..."
    
    # Update package list
    print_info "Updating package list..."
    apt-get update -qq || {
        print_error "Failed to update package list"
        exit 1
    }
    
    # List of required packages
    local packages=(
        "git"
        "python3"
        "python3-pip"
        "python3-venv"
        "python3-dev"
        "build-essential"
        "libssl-dev"
        "libffi-dev"
    )
    
    # Check and install missing packages
    local to_install=()
    for package in "${packages[@]}"; do
        if ! dpkg -l | grep -q "^ii  $package "; then
            to_install+=("$package")
        fi
    done
    
    if [ ${#to_install[@]} -gt 0 ]; then
        print_info "Installing: ${to_install[*]}"
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${to_install[@]}" || {
            print_error "Failed to install required packages"
            exit 1
        }
        print_success "System packages installed"
    else
        print_success "All system packages already installed"
    fi
}

# Detect installation status
detect_installation() {
    if [[ -d "$INSTALL_DIR" ]]; then
        if [[ -d "$INSTALL_DIR/.git" ]]; then
            return 0  # Installed
        else
            print_error "Directory exists but is not a git repository: $INSTALL_DIR"
            print_info "Please remove or rename this directory and try again"
            exit 1
        fi
    else
        return 1  # Not installed
    fi
}

# Clone repository
clone_repository() {
    print_step "Cloning repository..."
    
    if [[ -d "$INSTALL_DIR" ]]; then
        print_error "Directory already exists: $INSTALL_DIR"
        exit 1
    fi
    
    git clone "$REPO_URL" "$INSTALL_DIR" || {
        print_error "Failed to clone repository"
        print_info "Please check your internet connection and repository URL"
        exit 1
    }
    
    print_success "Repository cloned successfully"
}

# Update repository
update_repository() {
    print_step "Updating repository..."
    
    cd "$INSTALL_DIR" || exit 1
    
    # Find git command (use full path for systemd compatibility)
    GIT_CMD=$(command -v git 2>/dev/null || echo "/usr/bin/git")
    
    # Stash any local changes
    if ! $GIT_CMD diff-index --quiet HEAD -- 2>/dev/null; then
        print_info "Stashing local changes..."
        $GIT_CMD stash
    fi
    
    # Pull latest changes
    $GIT_CMD pull origin main || {
        print_error "Failed to update repository"
        exit 1
    }
    
    print_success "Repository updated successfully"
}

# Setup Python virtual environment
setup_venv() {
    print_step "Setting up Python virtual environment..."
    
    cd "$INSTALL_DIR" || exit 1
    
    if [[ -d "$VENV_DIR" ]]; then
        print_info "Virtual environment already exists"
    else
        $PYTHON_CMD -m venv "$VENV_DIR" || {
            print_error "Failed to create virtual environment"
            exit 1
        }
        print_success "Virtual environment created"
    fi
    
    # Activate virtual environment
    source "$VENV_DIR/bin/activate" || {
        print_error "Failed to activate virtual environment"
        exit 1
    }
    
    # Upgrade pip
    print_info "Upgrading pip..."
    pip install --upgrade pip setuptools wheel -q || {
        print_error "Failed to upgrade pip"
        exit 1
    }
    
    print_success "Virtual environment ready"
}

# Install Python dependencies
install_python_packages() {
    print_step "Installing Python dependencies..."
    
    cd "$INSTALL_DIR" || exit 1
    source "$VENV_DIR/bin/activate" || exit 1
    
    if [[ ! -f "requirements.txt" ]]; then
        print_error "requirements.txt not found"
        exit 1
    fi
    
    pip install -r requirements.txt -q || {
        print_error "Failed to install Python packages"
        print_info "Trying again with verbose output..."
        pip install -r requirements.txt || exit 1
    }
    
    print_success "Python dependencies installed"
}

# Create systemd service
create_service() {
    print_step "Creating systemd service..."
    
    # Stop service if it exists
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_info "Stopping existing service..."
        systemctl stop "$SERVICE_NAME"
    fi
    
    # Create service file
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Wind Tunnel Controller Web Interface
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$VENV_DIR/bin"
ExecStart=$VENV_DIR/bin/gunicorn --worker-class gthread --workers 1 --threads 4 --bind 0.0.0.0:80 app:app
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    print_success "Service file created"
    
    # Reload systemd
    print_info "Reloading systemd daemon..."
    systemctl daemon-reload
    
    print_success "Systemd service configured"
}

# Enable and start service
enable_service() {
    print_step "Enabling and starting service..."
    
    systemctl enable "$SERVICE_NAME" || {
        print_error "Failed to enable service"
        exit 1
    }
    
    systemctl start "$SERVICE_NAME" || {
        print_error "Failed to start service"
        print_info "Check logs with: sudo journalctl -u $SERVICE_NAME -f"
        exit 1
    }
    
    sleep 2
    
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_success "Service is running"
    else
        print_error "Service failed to start"
        print_info "Check logs with: sudo journalctl -u $SERVICE_NAME -f"
        exit 1
    fi
}

# Configure firewall
configure_firewall() {
    print_step "Configuring firewall..."
    
    if command_exists ufw; then
        if ufw status | grep -q "Status: active"; then
            print_info "UFW is active, opening port 80..."
            ufw allow 80/tcp >/dev/null 2>&1 || true
            print_success "Firewall configured"
        else
            print_info "UFW is not active, skipping firewall configuration"
        fi
    else
        print_info "UFW not installed, skipping firewall configuration"
    fi
}

# Get IP address
get_ip_address() {
    local ip=""
    
    # Try to get primary IP
    ip=$(hostname -I | awk '{print $1}')
    
    if [[ -z "$ip" ]]; then
        ip="localhost"
    fi
    
    echo "$ip"
}

# Display final information
show_completion_info() {
    local ip=$(get_ip_address)
    
    echo ""
    echo -e "${GREEN}============================================"
    echo "  Installation Complete!"
    echo "============================================${NC}"
    echo ""
    echo "The Wind Tunnel Controller is now running!"
    echo ""
    echo -e "${BLUE}Access the web interface at:${NC}"
    echo "  • http://$ip/"
    echo "  • http://localhost/"
    echo ""
    echo -e "${BLUE}Useful commands:${NC}"
    echo "  • Check status:    sudo systemctl status $SERVICE_NAME"
    echo "  • View logs:       sudo journalctl -u $SERVICE_NAME -f"
    echo "  • Restart service: sudo systemctl restart $SERVICE_NAME"
    echo "  • Stop service:    sudo systemctl stop $SERVICE_NAME"
    echo ""
    echo -e "${YELLOW}Note: Currently using mock data for testing."
    echo "Replace the generate_mock_data() function in app.py"
    echo "with your actual sensor readings.${NC}"
    echo ""
}

# Uninstall function
uninstall() {
    print_header
    echo -e "${RED}═══════════════════════════════════════════${NC}"
    echo -e "${RED}         UNINSTALL WIND TUNNEL CONTROLLER${NC}"
    echo -e "${RED}═══════════════════════════════════════════${NC}"
    echo ""
    echo -e "${YELLOW}This will completely remove:${NC}"
    echo "  • Wind Tunnel Controller application"
    echo "  • System service (windtunnel.service)"
    echo "  • Installation directory: $INSTALL_DIR"
    echo "  • All configuration files"
    echo ""
    echo -e "${RED}WARNING: This action cannot be undone!${NC}"
    echo ""
    read -p "Are you sure you want to uninstall? (yes/no): " confirm
    
    if [[ "$confirm" != "yes" ]]; then
        print_info "Uninstall cancelled"
        exit 0
    fi
    
    print_step "Uninstalling Wind Tunnel Controller..."
    
    # Stop and disable service
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_info "Stopping service..."
        systemctl stop "$SERVICE_NAME" || true
        print_success "Service stopped"
    fi
    
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        print_info "Disabling service..."
        systemctl disable "$SERVICE_NAME" || true
        print_success "Service disabled"
    fi
    
    # Remove service file
    if [[ -f "$SERVICE_FILE" ]]; then
        print_info "Removing service file..."
        rm -f "$SERVICE_FILE"
        systemctl daemon-reload
        print_success "Service file removed"
    fi
    
    # Remove installation directory
    if [[ -d "$INSTALL_DIR" ]]; then
        print_info "Removing installation directory..."
        rm -rf "$INSTALL_DIR"
        print_success "Installation directory removed"
    fi
    
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════${NC}"
    echo -e "${GREEN}    Uninstallation Complete${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════${NC}"
    echo ""
    echo "Wind Tunnel Controller has been completely removed from your system."
    echo ""
}

# Prompt for update
prompt_update() {
    echo ""
    echo -e "${YELLOW}Wind Tunnel Controller is already installed.${NC}"
    echo ""
    echo "What would you like to do?"
    echo "  1) Update to latest version"
    echo "  2) Reinstall (fresh install)"
    echo "  3) Uninstall completely"
    echo "  4) Exit"
    echo ""
    read -p "Enter your choice [1-4]: " choice
    
    case $choice in
        1)
            return 0  # Update
            ;;
        2)
            print_info "Removing existing installation..."
            systemctl stop "$SERVICE_NAME" 2>/dev/null || true
            systemctl disable "$SERVICE_NAME" 2>/dev/null || true
            rm -f "$SERVICE_FILE"
            rm -rf "$INSTALL_DIR"
            systemctl daemon-reload
            print_success "Existing installation removed"
            return 1  # Reinstall
            ;;
        3)
            uninstall
            exit 0
            ;;
        4)
            print_info "Exiting..."
            exit 0
            ;;
        *)
            print_error "Invalid choice"
            exit 1
            ;;
    esac
}

###########################################
# Main Installation Flow
###########################################

main() {
    print_header
    
    # Check if running as root
    check_sudo required
    
    # Check system compatibility
    check_system
    
    # Install system packages
    install_system_packages
    
    # Detect if already installed
    if detect_installation; then
        if prompt_update; then
            # Update mode
            print_info "Performing update..."
            update_repository
            setup_venv
            install_python_packages
            create_service
            enable_service
            show_completion_info
        else
            # Reinstall mode
            print_info "Performing fresh installation..."
            clone_repository
            setup_venv
            install_python_packages
            create_service
            enable_service
            configure_firewall
            show_completion_info
        fi
    else
        # Fresh installation
        print_info "Performing fresh installation..."
        clone_repository
        setup_venv
        install_python_packages
        create_service
        enable_service
        configure_firewall
        show_completion_info
    fi
}

###########################################
# Run Main Function
###########################################

# Check for uninstall flag
if [[ "$1" == "uninstall" ]] || [[ "$1" == "--uninstall" ]] || [[ "$1" == "-u" ]]; then
    uninstall
    exit 0
fi

# Check for auto-update flag (non-interactive)
if [[ "$1" == "auto-update" ]] || [[ "$1" == "--auto-update" ]]; then
    echo "=== AUTO-UPDATE MODE STARTED ==="
    print_header
    print_info "Running automatic update (non-interactive mode)..."
    
    # Skip sudo check - assume already running with proper privileges
    
    # Check if installation exists
    if [[ ! -d "$INSTALL_DIR" ]]; then
        print_error "Installation directory not found: $INSTALL_DIR"
        exit 1
    fi
    
    echo "Step 1: Updating repository..."
    # Perform update steps
    update_repository
    
    echo "Step 2: Setting up virtual environment..."
    setup_venv
    
    echo "Step 3: Installing Python packages..."
    install_python_packages
    
    echo "Step 4: Updating service configuration..."
    create_service
    
    print_success "Update completed successfully!"
    print_info "Restarting service..."
    systemctl restart "$SERVICE_NAME" 2>/dev/null || true
    
    echo "=== AUTO-UPDATE MODE FINISHED ==="
    exit 0
fi

# Trap errors
trap 'print_error "An error occurred. Installation failed."; exit 1' ERR

# Run main installation
main

exit 0
