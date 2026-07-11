# Yaha-Pet! — Chiikawa Desktop Pet for Mac & Windows

Yaha-Pet is a free Chiikawa desktop pet app for Mac (macOS) and Windows, created with Python and PyQt6. Chiikawa, Hachiware, and Usagi roam your desktop, play animations, and react to your interactions — a Chiikawa buddy that lives on your screen.

## ✨ Features

* Autonomous Behavior: Characters walk around the screen on their own.
* Interactive: Drag and drop the characters anywhere on your screen.
* Throw Physics: flick a character and it goes flying — arcing, bouncing off screen edges, and crash-landing if you toss it hard enough. Gentle drops still float politely down.
* Lively Animations: Features several sprite animations for different actions like walking and dancing.
* Sound Effects: Characters have unique sounds for animations and interactions. Chiikawa and Hachiware now have real anime voice clips (chiikawa cries when grabbed, of course).
* Together Moment: If Chiikawa and Hachiware touch — by wandering into each other or being dropped together — they stroll off together and share a heart or hold hands before separating. Also available on demand via "Bring them together!" in the tray menu. Configurable via `config.json`: `{"coanimations": {"enabled": true, "cooldown_min_s": 60, "cooldown_max_s": 150}}`.
* System Tray Control: Manage your spawned characters through a system tray icon menu.
* Customizable: Don't like a certain animation? Sound is too loud? Change it all on the tray control.

## 🍎 How to Run on Mac (macOS)

Yaha-Pet now runs natively on Mac! The Chiikawa desktop pet works on Apple Silicon and Intel Macs. Download `Yaha-Pet-macOS.zip` from the Releases page, unzip, and open `Yaha-Pet.app` (right-click → Open the first time if Gatekeeper complains). Usagi spawns automatically; right-click the Dock icon for the full menu (Spawn Character, Gather everyone!, animations, and more).

To build from source on macOS:

1. `python3 -m pip install --user PyQt6 pyinstaller`
2. `pyinstaller --noconfirm --windowed --name Yaha-Pet --icon yaha.icns --add-data assets:assets "Yaha-Pet!.py"`
3. The app appears in `dist/Yaha-Pet.app`

The Mac version also includes: a fix for a crash when grabbing characters (double-free of the grab sound player), a fix for the random-behavior roll so characters walk and dance instead of only jumping, a "Gather everyone!" menu action that recalls wandering pets to screen center, pets staying visible while other apps are focused, and a Dock menu.

## 🚀 How to Run (Windows)

This application is intended for Windows users.

1.  Go to the Releases (https://github.com/gitChara-dot/Yaha-Pet/releases) page of this repository.
2.  Download the latest `.zip` file (e.g., `Yaha-Pet-v1.0.zip`).
3.  Unzip the downloaded file.
4.  Run the `.exe` file inside the folder.
5.  Open Window System Tray (The small arrow pointing up on your taskbar), right-click Usagi's Icon. That's it!

6.  (OPTIONAL, IMPORTANT FOR WIN11 USERS): If you have Windows 11 and don't have the System Tray on your taskbar, do these steps:
    a. Right click the Taskbar
    b. Open taskbar configuration
    c. Go to "Taskbar Overflow" or "Other Taskbar Icons"
    d. Enable Yaha-Pet.

## ⚠️ Disclaimer

This is a non-profit fan project created for entertainment and educational purposes. All characters and associated assets from "Chiikawa" are the intellectual property of their original creator, Nagano. Please support the official work.

## 📄 License

This project is licensed under the MIT License. See the `LICENSE.md` file for details.

## Support me!
Totally optional. Yaha-Pet will always be free, for Chiikawa fans, and made by a Chiikawa fan. 
Still, you can support its development buying me a Ko-Fi! You should do it only if you can and want to.
https://ko-fi.com/gitchara
