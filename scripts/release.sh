#!/usr/bin/env bash
#
# release.sh - build, code-sign, notarize, and staple a PyInstaller macOS app.
#
# This is a reference implementation meant to be COPIED to other projects.
# To reuse it: change the CONFIG block below and make sure you have:
#   - a "Developer ID Application" cert in your login keychain
#   - a notarytool keychain profile (see NOTARY_PROFILE) created once with:
#       xcrun notarytool store-credentials "AC_NOTARY" \
#         --apple-id "<apple-id>" --team-id "<team-id>" --password "<app-specific-pw>"
#   - a PyInstaller .spec with upx=False and an entitlements file
#
# The pipeline, in order:
#   preflight -> build -> sign inside-out -> local verify -> zip
#     -> notarize app -> staple app -> Gatekeeper gate
#     -> build dmg -> sign dmg -> notarize dmg -> staple dmg -> gate dmg
#
# Flags:
#   --dry-run   Do everything EXCEPT submit to Apple (no notarization, no
#               stapling, no notarized Gatekeeper gate). Good for a fast
#               local check that the build + signing are clean.
#   -h|--help   Show usage.
#
# Every step that costs an Apple round-trip prints a "⚠️ APPLE ROUND-TRIP"
# banner first.

set -euo pipefail

# ============================================================================
# CONFIG  - the only block you edit when copying this to another project.
# ============================================================================
APP_NAME="Yaha-Pet"                 # base name, no extension
APP_BUNDLE="${APP_NAME}.app"        # the .app produced by PyInstaller
BUNDLE_ID="me.sarakay.YahaPet"      # must match bundle_identifier in the .spec
SPEC="${APP_NAME}.spec"             # PyInstaller spec file (repo-root relative)
ENTITLEMENTS="entitlements.plist"   # hardened-runtime entitlements
NOTARY_PROFILE="AC_NOTARY"          # notarytool keychain profile name
DIST_DIR="dist"                     # PyInstaller output dir
DMG_BASENAME="${APP_NAME}-macOS"    # -> dist/Yaha-Pet-macOS.dmg

# PyInstaller invocation. Override with PYINSTALLER=/path/to/pyinstaller if it
# is not on PATH (pip --user installs land in ~/Library/Python/X.Y/bin).
PYINSTALLER="${PYINSTALLER:-pyinstaller}"

# ============================================================================
# Below here is generic - you should not need to edit it per project.
# ============================================================================

# ---- logging helpers -------------------------------------------------------
if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'
  YLW=$'\033[33m'; BLU=$'\033[34m'; RST=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GRN=""; YLW=""; BLU=""; RST=""
fi

phase() { printf '\n%s══ %s ══%s\n' "$BOLD$BLU" "$1" "$RST"; }
log()   { printf '%s•%s %s\n' "$DIM" "$RST" "$1"; }
ok()    { printf '%s✓%s %s\n' "$GRN" "$RST" "$1"; }
warn()  { printf '%s⚠ %s%s\n' "$YLW" "$1" "$RST"; }
die()   { printf '%s✗ %s%s\n' "$RED" "$1" "$RST" >&2; exit 1; }

roundtrip_banner() {
  printf '\n%s┌────────────────────────────────────────────┐%s\n' "$YLW" "$RST"
  printf '%s│  ⚠️  APPLE ROUND-TRIP: %-21s │%s\n' "$YLW" "$1" "$RST"
  printf '%s└────────────────────────────────────────────┘%s\n' "$YLW" "$RST"
}

# ---- args ------------------------------------------------------------------
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^#\{0,1\} \{0,1\}//'
      exit 0 ;;
    *) die "unknown argument: $arg (see --help)" ;;
  esac
done
[ "$DRY_RUN" -eq 1 ] && warn "DRY RUN - will build and sign locally but never contact Apple."

# ============================================================================
phase "1/8  Preflight"
# ============================================================================

# Run from repo root (where the .spec lives). Resolve script dir -> parent.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
log "repo root: $REPO_ROOT"

[ -f "$SPEC" ]         || die "spec not found: $SPEC (run from the project that owns it)"
[ -f "$ENTITLEMENTS" ] || die "entitlements not found: $ENTITLEMENTS"

# Required tools.
for tool in codesign xcrun ditto spctl security /usr/libexec/PlistBuddy; do
  command -v "$tool" >/dev/null 2>&1 || [ -x "$tool" ] || die "missing required tool: $tool"
done

# PyInstaller - fall back to the pip --user location if not on PATH.
if ! command -v "$PYINSTALLER" >/dev/null 2>&1; then
  fallback="$(ls "$HOME"/Library/Python/*/bin/pyinstaller 2>/dev/null | head -1 || true)"
  if [ -n "$fallback" ]; then
    warn "pyinstaller not on PATH; using $fallback"
    PYINSTALLER="$fallback"
  else
    die "pyinstaller not found. Install it (pip install --user pyinstaller) or set PYINSTALLER=/path/to/pyinstaller"
  fi
fi
log "pyinstaller: $($PYINSTALLER --version 2>/dev/null) ($PYINSTALLER)"

# create-dmg is only needed for the DMG phase, but fail early so you don't get
# a green app and then a crash 5 minutes later.
if ! command -v create-dmg >/dev/null 2>&1; then
  die "create-dmg not found. Install with: brew install create-dmg"
fi

# Find the Developer ID Application identity. We match the human-readable name
# and extract the 40-char SHA-1 hash (unambiguous even with multiple certs).
phase_identity_line="$(security find-identity -v -p codesigning \
  | grep 'Developer ID Application' | head -1 || true)"
[ -n "$phase_identity_line" ] || die \
  "No 'Developer ID Application' certificate found in the keychain.
   Get one from https://developer.apple.com (Certificates -> Developer ID Application),
   download the .cer, and double-click to install into your login keychain.
   Then re-run. (\`security find-identity -v -p codesigning\` should list it.)"

SIGN_ID="$(printf '%s' "$phase_identity_line" | awk '{print $2}')"
SIGN_NAME="$(printf '%s' "$phase_identity_line" | sed -E 's/^[^"]*"([^"]+)".*/\1/')"
ok "signing identity: $SIGN_NAME"
log "identity hash:   $SIGN_ID"

# Export for the .spec (it reads CODESIGN_IDENTITY from the environment).
export CODESIGN_IDENTITY="$SIGN_ID"

# ============================================================================
phase "2/8  Build (pyinstaller --clean)"
# ============================================================================
log "cleaning previous build/ and dist/ for a reproducible result"
"$PYINSTALLER" "$SPEC" --clean --noconfirm

APP_PATH="$DIST_DIR/$APP_BUNDLE"
[ -d "$APP_PATH" ] || die "expected app not produced: $APP_PATH"

# Single source of truth for the version: read it back out of the built app.
VERSION="$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' \
  "$APP_PATH/Contents/Info.plist" 2>/dev/null || echo '0.0.0')"
ok "built $APP_PATH (version $VERSION)"

# ============================================================================
phase "3/8  Sign inside-out"
# ============================================================================
# The rule that makes or breaks PyInstaller notarization: sign the DEEPEST
# code first and the .app bundle LAST, so each container seals contents that
# are already validly signed. We do NOT use --deep: it is deprecated and
# silently skips nested code, producing bundles that notarize but then fail
# to launch or fail stapling.
#
# Flags on every codesign call:
#   --force      re-sign even if already signed (PyInstaller may have ad-hoc/
#                Developer-ID signed during the build; we are authoritative)
#   --options runtime   enable the hardened runtime (required to notarize)
#   --timestamp  request a secure timestamp from Apple's TSA (required to
#                notarize; this is a quick network call, not a notarization)
# Entitlements go ONLY on the final .app sign (the main executable), never on
# nested libraries.

codesign_inner() {
  # sign one nested Mach-O / framework, no entitlements
  codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$1"
}

# --- 3a: every nested Mach-O file (dylibs, .so, and extension-less binaries) -
# We detect Mach-O with `file` rather than trusting extensions, so framework
# binaries and helper executables get caught too.
log "scanning for nested Mach-O binaries (this is the ~100-item step)..."
inner_count=0
while IFS= read -r -d '' f; do
  if file -b "$f" 2>/dev/null | grep -q 'Mach-O'; then
    codesign_inner "$f"
    inner_count=$((inner_count + 1))
  fi
done < <(find "$APP_PATH" -type f -print0)
ok "signed $inner_count nested Mach-O binaries"

# --- 3b: framework bundles, deepest first ----------------------------------
# Signing the .framework bundle re-seals it around the inner binary signed
# above. Deepest-first ordering handles any framework-within-framework nesting.
fw_count=0
while IFS= read -r -d '' fw; do
  codesign_inner "$fw"
  fw_count=$((fw_count + 1))
done < <(find "$APP_PATH" -name '*.framework' -type d -print0 \
         | awk -F/ '{print NF"\t"$0}' | sort -rn | cut -f2- | tr '\n' '\0')
ok "signed $fw_count framework bundles"

# --- 3c: the .app bundle itself, LAST, WITH entitlements -------------------
# TODO(next release): try dropping com.apple.security.cs.disable-library-validation
# from entitlements.plist. Because we re-sign every nested binary above with this
# same Developer ID, the team IDs already match, so it may be unnecessary. Delete
# the key, re-run this script, and if the app still NOTARIZES and LAUNCHES, remove
# it for good. Kept for the 1.0.0 release because it was a known-good build.
log "signing the app bundle with hardened runtime + entitlements"
codesign --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" \
  --sign "$SIGN_ID" "$APP_PATH"
ok "signed $APP_BUNDLE"

# ============================================================================
phase "4/8  Local verification (before spending an Apple round-trip)"
# ============================================================================
# --deep is correct for VERIFICATION (walk everything); it is only wrong for
# signing. --strict catches problems the notary service would otherwise reject.
codesign --verify --deep --strict --verbose=2 "$APP_PATH" \
  || die "local codesign verification failed - fix before notarizing"
ok "codesign --verify --deep --strict passed"

# Show what we actually signed (team id should now be set, not 'not set').
codesign -dvv "$APP_PATH" 2>&1 | grep -E 'Identifier|TeamIdentifier|Authority|flags' | sed 's/^/    /' || true

# ============================================================================
phase "5/8  Package (ditto zip for notarization)"
# ============================================================================
# ditto -c -k --keepParent preserves symlinks, resource forks, and signatures.
# Plain `zip` breaks framework symlinks and can corrupt signatures - never use
# it for a signed .app.
ZIP_PATH="$DIST_DIR/${APP_NAME}-${VERSION}.zip"
rm -f "$ZIP_PATH"
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"
ok "wrote $ZIP_PATH"

# ---------------------------------------------------------------------------
# notarize(): submit a file, wait, and on failure auto-dump Apple's log.
# Returns 0 on Accepted, non-zero otherwise. Sets nothing global.
# ---------------------------------------------------------------------------
notarize() {
  local file="$1" label="$2"
  roundtrip_banner "notarize $label"
  log "submitting $file to Apple notary service (this can take 1-5 min)..."

  local out submission status
  # Capture the full output (notarytool --wait blocks until done, so there is
  # little live output to miss), then print it so it's in the log either way.
  out="$(xcrun notarytool submit "$file" \
          --keychain-profile "$NOTARY_PROFILE" --wait 2>&1)"
  printf '%s\n' "$out"

  submission="$(printf '%s\n' "$out" | awk '/id:/ {print $2; exit}')"
  status="$(printf '%s\n' "$out" | awk -F': *' '/status:/ {print $2}' | tail -1)"

  if [ "$status" = "Accepted" ]; then
    ok "notarization ACCEPTED ($label, id $submission)"
    return 0
  fi

  warn "notarization NOT accepted (status: ${status:-unknown}). Fetching Apple's log..."
  if [ -n "$submission" ]; then
    # This is the log the user should never have to go dig for.
    xcrun notarytool log "$submission" --keychain-profile "$NOTARY_PROFILE" || true
  else
    warn "could not parse a submission id from the output above."
  fi
  return 1
}

# ============================================================================
phase "6/8  Notarize + staple the app"
# ============================================================================
if [ "$DRY_RUN" -eq 1 ]; then
  warn "[dry-run] skipping app notarization, stapling, and notarized gate."
else
  notarize "$ZIP_PATH" "app" || die "app notarization failed (see Apple log above)."

  log "stapling the notarization ticket into the .app..."
  xcrun stapler staple "$APP_PATH" || die "stapler failed for the app"
  ok "stapled $APP_BUNDLE"

  # Final Gatekeeper gate for the APP. NOTE: for a .app the correct assessment
  # type is `exec` (will this app be allowed to run?). `install` is for
  # installer packages - we use that on the DMG below. Must report
  # "accepted" AND "source=Notarized Developer ID".
  log "Gatekeeper assessment (spctl -a -t exec) ..."
  app_assess="$(spctl -a -t exec -vvv "$APP_PATH" 2>&1 || true)"
  printf '%s\n' "$app_assess" | sed 's/^/    /'
  if printf '%s\n' "$app_assess" | grep -q 'source=Notarized Developer ID'; then
    ok "app accepted by Gatekeeper as Notarized Developer ID"
  else
    die "app did NOT pass the notarized Gatekeeper gate"
  fi
fi

# ============================================================================
phase "7/8  Build + sign the DMG"
# ============================================================================
# Stapling the DMG (not just the app inside it) is what makes the very first
# open-from-download clean: Gatekeeper reads the ticket from the disk image
# without needing a network check.
DMG_PATH="$DIST_DIR/${DMG_BASENAME}.dmg"
rm -f "$DMG_PATH"

# Stage just the .app in a clean dir so the DMG contains exactly one item + the
# /Applications drop link.
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT
cp -R "$APP_PATH" "$STAGE_DIR/"

log "building DMG with create-dmg..."
create-dmg \
  --volname "$APP_NAME" \
  --app-drop-link 480 170 \
  --icon "$APP_BUNDLE" 160 170 \
  --window-size 640 360 \
  --hide-extension "$APP_BUNDLE" \
  --no-internet-enable \
  "$DMG_PATH" "$STAGE_DIR" \
  || die "create-dmg failed"
ok "built $DMG_PATH"

# Sign the DMG. A disk image is not executable code, so no hardened runtime and
# no entitlements - just a Developer ID signature so it can be notarized.
codesign --force --timestamp --sign "$SIGN_ID" "$DMG_PATH"
ok "signed the DMG"

# ============================================================================
phase "8/8  Notarize + staple the DMG"
# ============================================================================
if [ "$DRY_RUN" -eq 1 ]; then
  warn "[dry-run] skipping DMG notarization, stapling, and gate."
  warn "[dry-run] complete. Nothing was sent to Apple. Artifacts:"
  log  "  app: $APP_PATH (signed, hardened runtime, NOT notarized)"
  log  "  dmg: $DMG_PATH (signed, NOT notarized)"
  exit 0
fi

notarize "$DMG_PATH" "dmg" || die "DMG notarization failed (see Apple log above)."

log "stapling the notarization ticket into the DMG..."
xcrun stapler staple "$DMG_PATH" || die "stapler failed for the DMG"
ok "stapled the DMG"

# Gate the DMG. `install` is the right assessment type for a disk image /
# installer. Must report accepted + Notarized Developer ID.
log "Gatekeeper assessment of the DMG (spctl -a -t install) ..."
dmg_assess="$(spctl -a -t install -vvv "$DMG_PATH" 2>&1 || true)"
printf '%s\n' "$dmg_assess" | sed 's/^/    /'
if printf '%s\n' "$dmg_assess" | grep -q 'source=Notarized Developer ID'; then
  ok "DMG accepted by Gatekeeper as Notarized Developer ID"
else
  die "DMG did NOT pass the notarized Gatekeeper gate"
fi

printf '\n%s🎉 Release complete.%s\n' "$BOLD$GRN" "$RST"
log "Ship this: $DMG_PATH"
log "Users can download, open, and drag to Applications with no Gatekeeper warning."
