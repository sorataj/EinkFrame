# Inky Photo Frame

## Installing on a new Raspberry Pi

Flash Raspberry Pi OS, connect to WiFi and SSH, then run:

```bash
curl -sSL https://raw.githubusercontent.com/sorataj/EinkFrame/main/install.sh | bash
```

This installs everything from scratch: system dependencies, a Python virtual environment, the frame software, the web app, and the two systemd services (`inky-photo-frame`, `inky-webui`) set to start automatically on boot. A reboot is recommended afterwards so GPIO group permissions take effect. Re-running the script later safely pulls the latest version and reinstalls.

## Web App

Manage your photo frame from any phone, tablet, or computer on the same WiFi network, no need to connect cables, SSH in, or use file-sharing apps.

### How to access

Open a web browser and go to:

```
http://inkyimpression.local
```

(No port number needed, it's served on the standard web port.)

### What you can do

#### Photos tab
- **Upload photos** (JPG, PNG, HEIC, and more) - new photos appear on the frame within seconds, and HEIC photos from iPhones are supported automatically
- **Browse** all photos as thumbnails
- **Click any photo** to open a full-size preview, where you can:
  - **Show on frame now** - instantly display that photo on the e-ink screen
  - **Edit photo** - opens an editor with:
    - **Crop** (optional), either **free-form** or **locked to the frame's current aspect ratio** (automatically matches landscape/portrait from Settings, so the result fills the screen perfectly)
    - **Brightness** and **Contrast** sliders, with an instant live preview before saving
    - A single **Save changes** button applies the crop and brightness/contrast together, overwriting the original photo
- **Delete** photos you no longer want

#### Settings tab
- **Frame orientation** - landscape or portrait, plus a 180 degree flip option
- **Collage mode** - show 2-3 photos per screen in a random layout
- **Color mode** - choose between Spectra palette (accurate colors, recommended), Warmth boost (warmer tones for portraits), or Pimoroni default (official library behavior)
- **Collage border** - add a border between the photos in collage mode, with adjustable thickness (0-20px) and colour (full colour picker, plus quick-pick swatches for the panel's 6 colours)
- **Change frequency** - have the frame change photos every N minutes, or once a day at a set hour; a smart history keeps track of recently shown photos so nothing repeats too soon
- **Sleep window** - pause photo changes during certain hours (e.g. overnight) to save battery; the frame simply freezes on whatever is currently shown
- **Software update** - pull the latest version from GitHub and restart the services, right from the browser
- **Power** - restart or power off the Raspberry Pi
- **System info** - at-a-glance service status, disk space, OS version, frame uptime, CPU temperature, core voltage, and a "View logs" button for live troubleshooting without SSH

## Physical Button Controls

The Inky Impression has 4 physical buttons on the side for interactive control:

| Button | Location | Function |
|--------|----------|----------|
| A | Top | Next photo |
| B | | Previous photo |
| C | | Cycle color modes (pimoroni -> spectra_palette -> warmth_boost) |
| D | Bottom | Reset to pimoroni default mode |

Features:
- No messages displayed - actions happen silently
- Buttons are locked during photo display (~30-40s e-ink refresh)
- Color mode preference is saved and persists across reboots, and stays in sync with the web app's Settings tab
- Navigate through your photo collection without using your phone

## File Locations

- Photos: /home/pi/Images
- Application: /home/pi/inky-photo-frame
- Logs: /home/pi/inky_photo_frame.log
- History: /home/pi/.inky_history.json

## Credits

This project is based on the original work by [mehdi7129](https://github.com/mehdi7129/inky-photo-frame).
