#!/bin/bash
#
# Inky Photo Frame - Power mode helper
#
# Called by the frame software (with sudo, via a dedicated passwordless
# sudoers rule - see install.sh) to reduce power draw during the configured
# sleep window, and restore normal operation afterwards.
#
# Usage:
#   sudo /usr/local/bin/inky-power-mode.sh sleep   # entering sleep window
#   sudo /usr/local/bin/inky-power-mode.sh wake    # leaving sleep window (or Button D override)

set -e

MODE="$1"

set_governor() {
    local governor="$1"
    for cpu in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
        [ -w "$cpu" ] && echo "$governor" > "$cpu" 2>/dev/null || true
    done
}

case "$MODE" in
    sleep)
        set_governor "powersave"
        rfkill block wifi 2>/dev/null || true
        ;;
    wake)
        set_governor "ondemand"
        rfkill unblock wifi 2>/dev/null || true
        ;;
    *)
        echo "Usage: $0 {sleep|wake}" >&2
        exit 1
        ;;
esac
