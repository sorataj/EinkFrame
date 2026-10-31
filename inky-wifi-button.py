#!/usr/bin/env python3
"""Inky Photo Frame - standalone WiFi recovery button watchdog.

Runs as its own systemd service, completely independent of the main
inky-photo-frame app. Watches Button D (GPIO 24) directly and, on every
press, unconditionally restores WiFi connectivity:
  - unblocks the radio (rfkill)
  - brings the wlan0 interface up
  - triggers a reconnect to whatever network was last configured

This works even if the main photo-frame app is crashed, crash-looping
(e.g. because the e-ink display isn't physically connected), or the
WiFi radio was deliberately put to sleep by the frame's battery-saving
sleep window. Button D is always listening, giving a reliable physical
"fix my network" switch no matter what state the rest of the system is in.
"""

import logging
import subprocess
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
)

GPIO_BUTTON_D = 24
WIFI_IFACE = 'wlan0'


def _run(cmd, timeout=10):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.returncode == 0, (out.stdout or '') + (out.stderr or '')
    except Exception as e:
        return False, str(e)


def restore_wifi():
    """Unconditionally bring WiFi back up and reconnect."""
    logging.info('🔘 Button D pressed - restoring WiFi')

    ok, out = _run(['rfkill', 'unblock', 'wifi'])
    logging.info(f'rfkill unblock wifi: {"ok" if ok else "FAILED: " + out.strip()}')

    ok, out = _run(['ip', 'link', 'set', WIFI_IFACE, 'up'])
    logging.info(f'ip link set {WIFI_IFACE} up: {"ok" if ok else "FAILED: " + out.strip()}')

    # Trigger a reconnect to the last-configured network. Try wpa_cli first
    # (works with wpa_supplicant, the traditional Raspberry Pi OS stack);
    # fall back to restarting dhcpcd, which also re-associates.
    ok, out = _run(['wpa_cli', '-i', WIFI_IFACE, 'reconnect'])
    if ok:
        logging.info('wpa_cli reconnect: ok')
    else:
        logging.info(f'wpa_cli reconnect unavailable ({out.strip()}), restarting dhcpcd instead')
        ok2, out2 = _run(['systemctl', 'restart', 'dhcpcd'])
        logging.info(f'systemctl restart dhcpcd: {"ok" if ok2 else "FAILED: " + out2.strip()}')

    time.sleep(3)
    ok, out = _run(['ip', 'addr', 'show', WIFI_IFACE])
    if 'inet ' in out:
        ip_line = next((l.strip() for l in out.splitlines() if 'inet ' in l), '')
        logging.info(f'✅ WiFi restored: {ip_line}')
    else:
        logging.warning('⚠️ No IP address yet - may need a few more seconds to reconnect')


def main():
    try:
        from gpiozero import Button
    except ImportError:
        logging.error('gpiozero not available - WiFi recovery button disabled')
        while True:
            time.sleep(3600)

    logging.info(f'🔌 WiFi recovery button watchdog started (GPIO {GPIO_BUTTON_D})')
    button = Button(GPIO_BUTTON_D, bounce_time=0.05)
    button.when_pressed = restore_wifi

    # Keep the process alive; gpiozero handles events in the background.
    while True:
        time.sleep(3600)


if __name__ == '__main__':
    main()
