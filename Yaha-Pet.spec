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

a = Analysis(
    ['Yaha-Pet!.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
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
