# Inky Photo Frame

## 📱 How to Add Photos from Your Phone

### iPhone/iPad
1. Open the **Files** app
2. Tap **Connect to Server**
3. Enter: `smb://192.168.1.151`
4. Use these credentials:
   - **Username:** inky
   - **Password:** 3KGdDeIwJw
5. Open the **Images** folder
6. Upload your photos (JPG, PNG, HEIC supported)

### Android
1. Use a file explorer app (CX File Explorer, Solid Explorer)
2. Connect to: `smb://192.168.1.151`
3. Enter the same credentials as above
4. Navigate to **Images** folder
5. Upload your photos

## ✨ Features

- **Instant display** of new photos
- **Daily rotation** at 5AM
- **Smart history** - doesn't repeat recent photos
- **HEIC support** for iPhone photos
- **Smart cropping** for e-ink display

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
