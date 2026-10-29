<div align="center">

# 🖼️ Inky Photo Frame

**Turn a Raspberry Pi and an Inky Impression e-ink panel into a self-updating digital photo frame, managed entirely from your phone's browser.**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-red)](https://www.raspberrypi.org/)
[![Display 7.3"](https://img.shields.io/badge/display-Inky%20Impression%207.3%22-purple)](https://shop.pimoroni.com/products/inky-impression-7-3)
[![Display 13.3"](https://img.shields.io/badge/display-Inky%20Impression%2013.3%22-purple)](https://shop.pimoroni.com/products/inky-impression-7-3?variant=55186435277179)

[**🚀 Install**](#-installing-on-a-new-raspberry-pi) • [**🌐 Web App**](#-web-app) • [**🎮 Buttons**](#-physical-button-controls) • [**📁 Files**](#-file-locations) • [**🙏 License**](#-license)

</div>

---

## ✨ Features at a Glance

| Feature | Description |
|---|---|
| 📱 **Web App** | Upload, browse, edit and manage photos from any phone or browser on your WiFi — no cables, no SSH |
| 🎨 **Color Modes** | 3 profiles: calibrated Spectra palette, warmth boost for portraits, or Pimoroni default |
| ✂️ **Smart Cropping** | Automatic fill-cropping for the display, plus a manual crop / brightness / contrast editor |
| 🖼️ **Collage Mode** | Show 2–3 photos per screen in a random layout, with an optional border |
| 🔄 **Auto Rotation** | Change photos every N minutes or once a day, with history so nothing repeats too soon |
| 🌙 **Sleep Window** | Freeze the display, power down WiFi, and lower the CPU governor overnight (or any time range) to save battery |
| 🔧 **Self-Update** | Pull the latest version from GitHub and restart services, right from the browser |
| 📊 **System Info & Logs** | Service status, disk/CPU/voltage stats, and a live log viewer — no SSH needed |
| ⏻ **Remote Power** | Restart or power off the Raspberry Pi from the web app |
| 🎮 **Physical Buttons** | 4-button navigation and color-mode cycling on the panel itself |

---

## 🚀 Installing on a new Raspberry Pi

> ⚠️ **Important:** when flashing Raspberry Pi OS (e.g. with Raspberry Pi Imager), the main user account **must be named `pi`** — the installer and all paths (`/home/pi/...`) assume this username. You can also set the device's **hostname** at the same time (e.g. `inkyimpression`), which is how you'll reach the web app afterwards (see below).

Flash Raspberry Pi OS, connect to WiFi and SSH, then run:

```bash
curl -sSL https://raw.githubusercontent.com/sorataj/EinkFrame/main/install.sh | bash
```

This installs everything from scratch: system dependencies, I2C/SPI enablement, a Python virtual environment, the frame software, the web app, and the two systemd services (`inky-photo-frame`, `inky-webui`) set to start automatically on boot. If any hardware permissions changed, the script will tell you a reboot is required. Re-running the script later safely pulls the latest version and reinstalls — or just use the web app's own "Update from GitHub" button.

## 🌐 Web App

Manage your photo frame from any phone, tablet, or computer on the same WiFi network — no need to connect cables, SSH in, or use file-sharing apps.

### How to access

Open a web browser and go to:

```
http://<your-pi-hostname>.local
```

Replace `<your-pi-hostname>` with whatever hostname you set for the Pi when flashing it (e.g. `http://inkyimpression.local` if you named it `inkyimpression`). If you didn't set one, the default Raspberry Pi OS hostname is `raspberrypi`, so it would be `http://raspberrypi.local`.

No port number needed — it's served on the standard web port. If your browser tries to redirect to `https://` and fails, type the full `http://` prefix explicitly, or disable "HTTPS-only mode" for this address in your browser's settings.

### What you can do

#### 📷 Photos tab
- **Upload photos** (JPG, PNG, HEIC, and more) — new photos appear on the frame within seconds, and HEIC photos from iPhones are supported automatically
- **Browse** all photos as thumbnails
- **Click any photo** to open a full-size preview, where you can:
  - **Show on frame now** — instantly display that photo on the e-ink screen
  - **Edit photo** — opens an editor with:
    - **Crop** (optional), either **free-form** or **locked to the frame's current aspect ratio** (automatically matches landscape/portrait from Settings, so the result fills the screen perfectly)
    - **Brightness** and **Contrast** sliders, with an instant live preview before saving
    - A single **Save changes** button applies the crop and brightness/contrast together, overwriting the original photo
- **Delete** photos you no longer want

#### ⚙️ Settings tab
- **Frame orientation** — landscape or portrait, plus a 180° flip option
- **Collage mode** — show 2–3 photos per screen in a random layout
- **Color mode** — choose between Spectra palette (accurate colors, recommended), Warmth boost (warmer tones for portraits), or Pimoroni default (official library behavior)
- **Collage border** — add a border between the photos in collage mode, with adjustable thickness (0–20px) and colour (full colour picker, plus quick-pick swatches for the panel's 6 colours)
- **Change frequency** — have the frame change photos every N minutes, or once a day at a set hour; a smart history keeps track of recently shown photos so nothing repeats too soon
- **Sleep window** — pause photo changes during certain hours (e.g. overnight) to save battery; the frame freezes on whatever is currently shown, and additionally powers down the WiFi radio and switches the CPU to a low-power governor for extra savings. **The web app is unreachable while sleeping** — press physical Button D on the frame to wake WiFi early if needed (normal sleep power-saving resumes automatically the next time the window starts)
- **Software update** — pull the latest version from GitHub and restart the services, right from the browser
- **Power** — restart or power off the Raspberry Pi
- **System info** — at-a-glance service status, disk space, OS version, frame uptime, CPU temperature, core voltage, and a "View logs" button for live troubleshooting without SSH

## 🎮 Physical Button Controls

The Inky Impression has 4 physical buttons on the side for interactive control:

| Button | Location | Function |
|--------|----------|----------|
| A | Top | ⏭️ Next photo |
| B | | ⏮️ Previous photo |
| C | | 🎨 Cycle color modes (pimoroni → spectra_palette → warmth_boost) |
| D | Bottom | 🔄 Reset to pimoroni default mode — or, if currently in sleep power-saving, wake WiFi early instead |

Features:
- No messages displayed — actions happen silently
- Buttons are locked during photo display (~30–40s e-ink refresh)
- Color mode preference is saved and persists across reboots, and stays in sync with the web app's Settings tab
- Navigate through your photo collection without using your phone

## 📁 File Locations

- Photos: `/home/pi/Images`
- Application: `/home/pi/inky-photo-frame`
- Web app: `/home/pi/inky-webui`
- Logs: `/home/pi/inky_photo_frame.log`
- History: `/home/pi/.inky_history.json`

## 🙏 License

This project is licensed under the [MIT License](LICENSE).

It is based on and derived from [**inky-photo-frame**](https://github.com/mehdi7129/inky-photo-frame) by **mehdi7129**, also released under the MIT License. Many thanks to the original author for the foundation this project builds on.
