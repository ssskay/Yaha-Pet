# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for a code-signed, notarizable macOS build.
#
# Signing config is intentionally driven by scripts/release.sh so this file
# stays copyable to other projects:
#   - CODESIGN_IDENTITY is read from the environment. release.sh exports it
#     (from `security find-identity`) before invoking PyInstaller. Run the spec
#     standalone with the var unset and codesign_identity is None => an ad-hoc
#     build, which is fine for local testing.
#   - Signing here is a convenience; release.sh re-signs every nested binary
#     inside-out with --force afterward and is the authoritative signer.

import os

# Read the Developer ID from the environment (set by scripts/release.sh).
# None => ad-hoc / unsigned, so a bare `pyinstaller Yaha-Pet.spec` still works.
codesign_identity = os.environ.get("CODESIGN_IDENTITY") or None

# Which assets tree to bundle. release.sh sets YAHA_ASSETS_SRC to a downscaled
# COPY (see scripts/optimize-assets.sh) so the shipped app is small; unset, we
# fall back to the full-res masters so a bare `pyinstaller Yaha-Pet.spec` still
# produces a working (if heavier) build.
assets_src = os.environ.get("YAHA_ASSETS_SRC", "assets")

a = Analysis(
    ['Yaha-Pet!.py'],
    pathex=[],
    binaries=[],
    datas=[(assets_src, 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# ---- Prune Qt weight we never ship -----------------------------------------
# These filters run after PyInstaller's PyQt6 hook has collected everything, so
# they trim what the hook over-includes. Both are macOS-safe for this app.
#
# 1. Qt translations (~7 MB): the app ships no translations of its own and Qt's
#    English is built in. Keep only English .qm files, drop the rest.
a.datas = [
    d for d in a.datas
    if "Qt6/translations" not in d[0].replace(os.sep, "/")
    or "_en" in os.path.basename(d[0])
]

# 2. KEEP the QtMultimedia FFmpeg backend (~16 MB). It is tempting to drop it
#    since the darwin backend (libdarwinmediaplugin) is also bundled, but
#    QSoundEffect DECODES its WAV via QAudioDecoder, and on macOS that decode
#    path relies on the FFmpeg backend - the darwin backend only handles
#    playback. Removing libav*/libffmpegmediaplugin makes every sound silently
#    fail to decode (no error, just no audio). Verified the hard way. Do not
#    strip it unless you also replace QSoundEffect's decode path.

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Yaha-Pet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX MUST stay False: UPX rewrites Mach-O binaries in place, which corrupts
    # any code signature applied afterward. It is the #1 cause of
    # "signs fine, notarization rejected" with PyInstaller. Do not re-enable.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=codesign_identity,
    entitlements_file='entitlements.plist',
    icon=['yaha.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    # Same reason as above - UPX corrupts signatures. Keep False.
    upx=False,
    upx_exclude=[],
    name='Yaha-Pet',
)
app = BUNDLE(
    coll,
    name='Yaha-Pet.app',
    icon='yaha.icns',
    bundle_identifier='me.sarakay.YahaPet',
    version='1.0.0',
)
