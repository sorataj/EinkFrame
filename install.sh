#!/bin/bash
#
# Inky Photo Frame - Fresh Install Script
#
# Installs everything needed to run the Inky Photo Frame on a Raspberry Pi
# (tested on Raspberry Pi Zero 2 W) directly from GitHub.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/sorataj/EinkFrame/main/install.sh | bash
#
# What this does:
#   1. Installs required system packages (git, python3, GPIO build deps)
#   2. Clones the EinkFrame repo into /home/pi/inky-photo-frame
#   3. Creates a Python virtualenv (~/.virtualenvs/pimoroni) with all deps
#   4. Copies the web UI into its own standalone folder /home/pi/inky-webui
#   5. Creates /home/pi/Images for photos
#   6. Installs & enables two systemd services: inky-photo-frame, inky-webui
#   7. Enables I2C and SPI (required for the Inky display hardware)
#   8. Grants passwordless sudo for the web UI's update/restart/power buttons
#   9. Adds the pi user to the gpio group
#   10. Disables the Pi's activity and power LEDs (no light pollution at night)
#   11. Sets up log rotation
#
# If I2C/SPI/GPIO group membership needed changing, a reboot is required
# afterwards for the e-ink display to work (the script will tell you).
#
# After install, manage the services with standard systemd commands, e.g.:
#   sudo systemctl status inky-photo-frame
#   sudo systemctl restart inky-webui
#   sudo journalctl -u inky-photo-frame -f
#
# To update later, re-run this script, or pull the repo and restart services.

set -e

REPO_URL="https://github.com/sorataj/EinkFrame.git"
INSTALL_DIR="/home/pi/inky-photo-frame"
WEBUI_DIR="/home/pi/inky-webui"
PHOTOS_DIR="/home/pi/Images"
VENV_DIR="/home/pi/.virtualenvs/pimoroni"
LOG_FILE="/home/pi/inky_photo_frame.log"
RUN_USER="pi"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}✅ $1${NC}"; }
print_error()  { echo -e "${RED}❌ $1${NC}"; }
print_info()   { echo -e "${YELLOW}ℹ️  $1${NC}"; }

echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║        🖼️  Inky Photo Frame - Fresh Install            ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
print_info "Updating package lists..."
sudo apt-get update -qq

print_info "Installing system packages (git, python3, GPIO build deps)..."
sudo apt-get install -y \
    git \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    swig \
    liblgpio-dev
print_status "System packages installed"

# ---------------------------------------------------------------------------
# 2. Clone (or update) the repo
# ---------------------------------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
    print_info "Existing installation found, pulling latest changes..."
    git -C "$INSTALL_DIR" pull
    print_status "Repository updated"
else
    print_info "Cloning EinkFrame from GitHub..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    print_status "Repository cloned to $INSTALL_DIR"
fi

# ---------------------------------------------------------------------------
# 3. Python virtual environment
# ---------------------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
    print_info "Creating Python virtual environment at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
    print_status "Virtual environment created"
else
    print_info "Virtual environment already exists, reusing it"
fi

print_info "Installing Python dependencies (this can take a few minutes on a Pi Zero 2 W)..."
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
pip install --upgrade pip --quiet
pip install --upgrade \
    inky \
    pillow \
    pillow-heif \
    lgpio \
    RPi.GPIO \
    gpiozero \
    watchdog \
    flask \
    waitress \
    psutil \
    --quiet
deactivate
print_status "Python dependencies installed"

# ---------------------------------------------------------------------------
# 4. Web UI standalone copy
# ---------------------------------------------------------------------------
print_info "Setting up web UI..."
mkdir -p "$WEBUI_DIR"
cp "$INSTALL_DIR/webui/inky_webui.py" "$WEBUI_DIR/inky_webui.py"
print_status "Web UI copied to $WEBUI_DIR"

# ---------------------------------------------------------------------------
# 5. Photos folder
# ---------------------------------------------------------------------------
mkdir -p "$PHOTOS_DIR"
print_status "Photos folder ready: $PHOTOS_DIR"

# ---------------------------------------------------------------------------
# 6. systemd services
# ---------------------------------------------------------------------------
print_info "Installing WiFi recovery button service (Button D, always works)..."
sudo tee /etc/systemd/system/inky-wifi-button.service > /dev/null << EOF
[Unit]
Description=Inky Photo Frame - WiFi recovery button (Button D)
After=multi-user.target

[Service]
User=root
ExecStart=$VENV_DIR/bin/python3 $INSTALL_DIR/inky-wifi-button.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
print_status "inky-wifi-button.service installed"

print_info "Installing systemd service: inky-photo-frame..."
sudo tee /etc/systemd/system/inky-photo-frame.service > /dev/null << EOF
[Unit]
Description=Inky Photo Frame
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python3 $INSTALL_DIR/inky_photo_frame.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
print_status "inky-photo-frame.service installed"

print_info "Installing systemd service: inky-webui..."
sudo tee /etc/systemd/system/inky-webui.service > /dev/null << EOF
[Unit]
Description=Inky Photo Frame web UI
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$WEBUI_DIR
ExecStart=$VENV_DIR/bin/python3 $WEBUI_DIR/inky_webui.py
Environment=INKY_PHOTOS_DIR=$PHOTOS_DIR
Environment=INKY_WEB_PORT=80
Environment=INKY_WEB_PASSWORD=
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
print_status "inky-webui.service installed"

sudo systemctl daemon-reload
sudo systemctl enable inky-wifi-button.service inky-photo-frame.service inky-webui.service > /dev/null 2>&1
sudo systemctl restart inky-wifi-button.service > /dev/null 2>&1
print_status "Services enabled to start on boot"

# ---------------------------------------------------------------------------
# 7. Enable I2C and SPI (required by the Inky library: I2C reads the
#    display's EEPROM to auto-detect the model, SPI drives the panel).
#    Commented out by default on a fresh Raspberry Pi OS image.
# ---------------------------------------------------------------------------
print_info "Checking I2C and SPI are enabled..."
CONFIG_TXT="/boot/firmware/config.txt"
[ -f "$CONFIG_TXT" ] || CONFIG_TXT="/boot/config.txt"
NEEDS_REBOOT_FOR_HW=false

if [ -f "$CONFIG_TXT" ]; then
    if grep -q '^dtparam=i2c_arm=on' "$CONFIG_TXT"; then
        print_status "I2C already enabled"
    elif grep -q '^#dtparam=i2c_arm=on' "$CONFIG_TXT"; then
        sudo sed -i 's/^#dtparam=i2c_arm=on/dtparam=i2c_arm=on/' "$CONFIG_TXT"
        print_status "I2C enabled (reboot required)"
        NEEDS_REBOOT_FOR_HW=true
    else
        echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG_TXT" > /dev/null
        print_status "I2C enabled (reboot required)"
        NEEDS_REBOOT_FOR_HW=true
    fi

    if grep -q '^dtparam=spi=on' "$CONFIG_TXT"; then
        print_status "SPI already enabled"
    elif grep -q '^#dtparam=spi=on' "$CONFIG_TXT"; then
        sudo sed -i 's/^#dtparam=spi=on/dtparam=spi=on/' "$CONFIG_TXT"
        print_status "SPI enabled (reboot required)"
        NEEDS_REBOOT_FOR_HW=true
    else
        echo "dtparam=spi=on" | sudo tee -a "$CONFIG_TXT" > /dev/null
        print_status "SPI enabled (reboot required)"
        NEEDS_REBOOT_FOR_HW=true
    fi
else
    print_error "Could not find $CONFIG_TXT - enable I2C and SPI manually with raspi-config"
fi

# ---------------------------------------------------------------------------
# 8. Passwordless sudo for the web UI's Update/Restart/Power-off buttons,
#    and for the frame's sleep-window power-saving script
# ---------------------------------------------------------------------------
print_info "Installing power-mode helper script (CPU governor + WiFi radio)..."
sudo cp "$INSTALL_DIR/inky-power-mode.sh" /usr/local/bin/inky-power-mode.sh
sudo chmod 755 /usr/local/bin/inky-power-mode.sh
print_status "inky-power-mode.sh installed to /usr/local/bin"

print_info "Granting passwordless sudo for service restart / reboot / poweroff / power-mode..."
SUDOERS_FILE=/etc/sudoers.d/inky-webui
sudo tee "$SUDOERS_FILE" > /dev/null << EOF
$RUN_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart inky-photo-frame, /bin/systemctl restart inky-webui, /bin/systemctl restart inky-photo-frame inky-webui, /sbin/reboot, /sbin/poweroff, /usr/local/bin/inky-power-mode.sh sleep, /usr/local/bin/inky-power-mode.sh wake
EOF
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" > /dev/null 2>&1 && print_status "Sudoers rule installed" || print_error "Sudoers rule failed validation - update/power/sleep buttons may not work"

# ---------------------------------------------------------------------------
# 9. GPIO + journal permissions (journal access lets the web UI's "View
#    logs" button read service logs without needing sudo)
# ---------------------------------------------------------------------------
print_info "Checking GPIO permissions..."
if ! groups "$RUN_USER" | grep -q '\bgpio\b'; then
    sudo usermod -a -G gpio "$RUN_USER"
    print_status "User '$RUN_USER' added to gpio group (reboot may be required)"
    NEEDS_REBOOT_FOR_HW=true
else
    print_status "GPIO permissions already OK"
fi

print_info "Checking systemd-journal permissions..."
if ! groups "$RUN_USER" | grep -q '\bsystemd-journal\b'; then
    sudo usermod -a -G systemd-journal "$RUN_USER"
    print_status "User '$RUN_USER' added to systemd-journal group (reboot may be required)"
    NEEDS_REBOOT_FOR_HW=true
else
    print_status "Journal permissions already OK"
fi

# ---------------------------------------------------------------------------
# 10. Disable Raspberry Pi LEDs (activity + power, no light pollution)
# ---------------------------------------------------------------------------
print_info "Setting up LED disable service (no light pollution at night)..."
sudo tee /etc/systemd/system/disable-leds.service > /dev/null << 'EOF'
[Unit]
Description=Disable Raspberry Pi LEDs
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo none > /sys/class/leds/ACT/trigger || true'
ExecStart=/bin/sh -c 'echo 0 > /sys/class/leds/ACT/brightness || true'
ExecStart=/bin/sh -c 'echo none > /sys/class/leds/default-on/trigger || true'
ExecStart=/bin/sh -c 'echo 0 > /sys/class/leds/default-on/brightness || true'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable disable-leds.service > /dev/null 2>&1
sudo systemctl start disable-leds.service > /dev/null 2>&1
print_status "LED disable service installed and enabled"

# ---------------------------------------------------------------------------
# 11. Log rotation
# ---------------------------------------------------------------------------
if [ -f "$INSTALL_DIR/logrotate.conf" ]; then
    print_info "Installing logrotate configuration..."
    sudo cp "$INSTALL_DIR/logrotate.conf" /etc/logrotate.d/inky-photo-frame
    sudo chown root:root /etc/logrotate.d/inky-photo-frame
    sudo chmod 644 /etc/logrotate.d/inky-photo-frame
    print_status "Logrotate configured"
fi

# ---------------------------------------------------------------------------
# 12. Start services
# ---------------------------------------------------------------------------
print_info "Starting services..."
sudo systemctl restart inky-photo-frame.service
sudo systemctl restart inky-webui.service

sleep 3

OK=true
if sudo systemctl is-active --quiet inky-photo-frame.service; then
    print_status "inky-photo-frame is running"
else
    print_error "inky-photo-frame failed to start - check: sudo journalctl -u inky-photo-frame -n 50"
    OK=false
fi

if sudo systemctl is-active --quiet inky-webui.service; then
    print_status "inky-webui is running"
else
    print_error "inky-webui failed to start - check: sudo journalctl -u inky-webui -n 50"
    OK=false
fi

HOSTNAME=$(hostname)
IP_ADDR=$(hostname -I 2>/dev/null | cut -d' ' -f1)

echo ""
if [ "$OK" = true ]; then
    echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║            ✅ Installation complete!                   ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
else
    echo -e "${YELLOW}⚠️  Installation finished with errors - see messages above.${NC}"
fi
echo ""
print_info "Web app:  http://$HOSTNAME.local  (or http://$IP_ADDR)"
print_info "Photos folder: $PHOTOS_DIR"
print_info "Logs: sudo journalctl -u inky-photo-frame -f"
if [ "$NEEDS_REBOOT_FOR_HW" = true ]; then
    echo ""
    echo -e "${YELLOW}⚠️  A REBOOT IS REQUIRED now that I2C/SPI/GPIO permissions were changed.${NC}"
    echo -e "${YELLOW}   Without a reboot, the e-ink display will fail with 'No EEPROM detected'.${NC}"
    print_info "Run: sudo reboot"
else
    print_info "A reboot is recommended if this is the first install (for GPIO group membership)."
fi
echo ""
