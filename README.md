# Inky Photo Frame

## 🌐 Web App

Manage your photo frame from any phone, tablet, or computer on the same WiFi network — no need to connect cables or use file-sharing apps.

### How to access

Open a web browser and go to:

```
http://inkyimpression.local
```

(No port number needed — it's served on the standard web port.)

### What you can do

#### 📷 Photos tab
- **Upload photos** (JPG, PNG, HEIC, and more) — new photos appear on the frame within seconds
- **Browse** all photos as thumbnails
- **Click any photo** to open a full-size preview, where you can:
  - **Show on frame now** — instantly display that photo on the e-ink screen
  - **Crop** the photo, either **free-form** or **locked to the frame's aspect ratio** (so the result fills the screen perfectly)
- **Delete** photos you no longer want

#### ⚙️ Settings tab
- **Frame orientation** — landscape or portrait, plus a 180° flip option
- **Collage mode** — show 2–3 photos per screen in a random layout, with adjustable border thickness and colour
- **Change frequency** — have the frame change photos every N minutes, or once a day at a set hour
- **Sleep window** — pause photo changes during certain hours (e.g. overnight) to save battery; the frame simply freezes on whatever is currently shown
- **System info** — at-a-glance disk space, OS version, frame uptime, CPU temperature, and core voltage

## ✨ Features

- **Instant display** of new photos
- **Configurable rotation** — by interval or daily schedule
- **Smart history** - doesn't repeat recent photos
- **HEIC support** for iPhone photos
- **Smart cropping** for e-ink display
- **Battery-saving sleep window**

## 🛠 Useful Commands

```bash
# Check service status
sudo systemctl status inky-photo-frame

# View logs
sudo journalctl -u inky-photo-frame -f

# Restart service
sudo systemctl restart inky-photo-frame

# Stop service
sudo systemctl stop inky-photo-frame
```

## 📁 File Locations

- Photos: /home/pi/Images
- Application: /home/pi/inky-photo-frame
- Logs: /home/pi/inky_photo_frame.log
- History: /home/pi/.inky_history.json

## 🙏 Credits

This project is based on the original work by [mehdi7129](https://github.com/mehdi7129/inky-photo-frame).
