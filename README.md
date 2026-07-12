# Yaha-Pet for macOS 🐰 — Chiikawa Desktop Pets on your Mac

![Downloads](https://img.shields.io/github/downloads/ssskay/Yaha-Pet/total?label=downloads&color=ff69b4) ![Release](https://img.shields.io/github/v/release/ssskay/Yaha-Pet?label=latest&color=8fd3f4) ![Platform](https://img.shields.io/badge/platform-macOS%20(Apple%20Silicon%20%26%20Intel)-black)

**Usagi, Hachiware, and Chiikawa live on your Mac's desktop.** If you searched for a *"chiikawa desktop pet for Mac"* — this is the native macOS version (Apple Silicon M1/M2/M3/M4 & Intel, no emulator needed). Download from the [Releases](../../releases) page.

This is a Mac port of [**Yaha-Pet** by gitChara-dot](https://github.com/gitChara-dot/Yaha-Pet) — all credit for the original app, art, and sounds goes to them. **On Windows?** Use [the original](https://github.com/gitChara-dot/Yaha-Pet/releases), it's great.

ちいかわ・うさぎ・ハチワレのデスクトップペット、Mac版です 🐰 (macOS ネイティブアプリ)

A simple desktop pet application featuring characters from the "Chiikawa" universe, created with Python and PyQt6. The characters will roam your desktop, play animations, and react to your interactions.

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

Download **[`Yaha-Pet-macOS.dmg`](https://github.com/ssskay/Yaha-Pet/releases/latest/download/Yaha-Pet-macOS.dmg)** from the [latest release](https://github.com/ssskay/Yaha-Pet/releases/latest), open it, and drag **Yaha-Pet.app** into Applications. The app is **signed and notarized by Apple**, so it just opens — no "unidentified developer" warning and no right-click → Open workaround. Usagi spawns automatically; right-click the Dock icon for the full menu (Spawn Character, Gather everyone!, animations, and more).

> **Apple Silicon (M1–M4).** The release DMG is an `arm64` build. On an Intel Mac, build from source (below).

To build from source on macOS:

1. `python3 -m pip install --user PyQt6 pyinstaller`
2. `pyinstaller --noconfirm --windowed --name Yaha-Pet --icon yaha.icns --add-data assets:assets "Yaha-Pet!.py"`
3. The app appears in `dist/Yaha-Pet.app`

The Mac version also includes: a fix for a crash when grabbing characters (double-free of the grab sound player), a fix for the random-behavior roll so characters walk and dance instead of only jumping, a "Gather everyone!" menu action that recalls wandering pets to screen center, pets staying visible while other apps are focused, and a Dock menu.

## 🚀 How to Run (Windows)

The original Yaha-Pet is for Windows users.

1.  Go to the Releases (https://github.com/gitChara-dot/Yaha-Pet/releases) page of the original repository.
2.  Download the latest `.zip` file (e.g., `Yaha-Pet-v1.0.zip`).
3.  Unzip the downloaded file.
4.  Run the `.exe` file inside the folder.
5.  Open Window System Tray (The small arrow pointing up on your taskbar), right-click Usagi's Icon. That's it!

6.  (OPTIONAL, IMPORTANT FOR WIN11 USERS): If you have Windows 11 and don't have the System Tray on your taskbar, do these steps:
    a. Right click the Taskbar
    b. Open taskbar configuration
    c. Go to "Taskbar Overflow" or "Other Taskbar Icons"
    d. Enable Yaha-Pet.

## 🔏 Releasing (code signing & notarization)

Release builds are signed with a **Developer ID Application** certificate and
**notarized** by Apple, so users can download and open the app with no
"unidentified developer" Gatekeeper warning. The whole pipeline is one script:

```bash
./scripts/release.sh            # full release: build → sign → notarize → staple → DMG
./scripts/release.sh --dry-run  # build + sign + verify + DMG locally, never contacts Apple
```

Ship the resulting `dist/Yaha-Pet-macOS.dmg`.

### One-time setup

1. A **Developer ID Application** certificate installed in your login keychain
   (verify with `security find-identity -v -p codesigning`).
2. A **notarytool keychain profile** named `AC_NOTARY`, created once:
   ```bash
   xcrun notarytool store-credentials "AC_NOTARY" \
     --apple-id "<your-apple-id>" --team-id "<TEAMID>" \
     --password "<app-specific-password>"
   ```
   The app-specific password comes from appleid.apple.com (not your Apple ID
   password). Verify with `xcrun notarytool history --keychain-profile "AC_NOTARY"`.
3. `brew install create-dmg`, plus `PyQt6` and `pyinstaller`.

### What the pipeline does, and why

- **`pyinstaller Yaha-Pet.spec --clean`** rebuilds `dist/Yaha-Pet.app`. The spec
  keeps **`upx=False`** deliberately — UPX rewrites Mach-O binaries in place and
  corrupts any signature applied afterward. It is the single most common cause
  of "signed fine, notarization rejected" with PyInstaller.
- **Inside-out signing.** Every nested `.so`/`.dylib`/`.framework` (there are
  ~100) is signed *first*, deepest code before its container, and the `.app`
  bundle *last*. We never use `codesign --deep` — it's deprecated and silently
  skips nested code. Every call uses `--options runtime` (hardened runtime,
  required for notarization) and `--timestamp` (secure timestamp).
- **Local verify** (`codesign --verify --deep --strict`) runs before any Apple
  round-trip so mistakes are caught for free. (`--deep` is fine for *verifying* —
  it's only wrong for *signing*.)
- **Zip with `ditto -c -k --keepParent`**, never `zip` — plain zip breaks
  framework symlinks and signatures.
- **Notarize** the app, then the DMG, with `xcrun notarytool submit --wait`.
  On failure the script auto-runs `notarytool log <id>` and prints Apple's
  actual reason.
- **Staple** the ticket into *both* the app and the DMG. Stapling the DMG is
  what makes the very first open-from-download clean, with no network check.
- **Gatekeeper gate:** `spctl` must report `source=Notarized Developer ID` or
  the script exits non-zero.

### The two entitlements (`entitlements.plist`)

Under the hardened runtime, CPython needs exactly two exceptions. They live in
`entitlements.plist`, which must be **comment-free** — `codesign` feeds it to
Apple's AMFI parser, which rejects XML comments even though `plutil` accepts
them.

- **`com.apple.security.cs.allow-unsigned-executable-memory`** — the hardened
  runtime forbids memory that is both writable and executable. CPython's
  ctypes/libffi layer writes small machine-code trampolines at runtime and jumps
  into them; PyQt6's `sip` bindings reach that path. Without this exception the
  kernel kills the process on launch. Load-bearing here.
- **`com.apple.security.cs.disable-library-validation`** — library validation
  requires every loaded library to share the main executable's Team ID.
  PyInstaller `dlopen`s dozens of bundled C extensions and Qt plugins at
  runtime. Because `release.sh` re-signs *all* of them with the same Developer
  ID, this one is a candidate for removal — try deleting it, re-run
  `release.sh`, and if the app still notarizes **and** launches, you didn't need
  it.

**Audio needs no entitlement.** The app only *plays* sound (`QSoundEffect`); it
never records. No microphone entitlement and no `NSMicrophoneUsageDescription`
Info.plist key are required. Add both only if a future version captures audio.

## ⚠️ Disclaimer

This is a non-profit fan project created for entertainment and educational purposes. All characters and associated assets from "Chiikawa" are the intellectual property of their original creator, Nagano. Please support the official work.

## 📄 License

This project is licensed under the MIT License. See the `LICENSE.md` file for details.

## Support me!
Totally optional. Yaha-Pet will always be free, for Chiikawa fans, and made by a Chiikawa fan. 
Still, you can support its development buying me a Ko-Fi! You should do it only if you can and want to.
https://ko-fi.com/gitchara
