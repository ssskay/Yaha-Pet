#!/usr/bin/env bash
#
# optimize-assets.sh - produce a slimmed COPY of the assets tree for packaging.
#
# The repo's assets/ holds full-quality masters. This script writes a slimmed
# copy to an output dir; it NEVER modifies the input. Two levers, in order:
#
#   1. Downscale any PNG whose longest edge exceeds max-px. The pet never draws
#      larger than ~512 physical px, so oversized frames are pure weight. Most
#      frames are already small, so this is a minor pass.
#   2. Palette-quantize every PNG to <=256 colours (pngquant) then losslessly
#      recompress (oxipng). The frames are flat-colour cartoon art, so 32-bit
#      RGBA is ~90% waste; quantizing is visually identical and is the big win
#      (e.g. a 317 KB dance frame -> 21 KB). Frames stay .png files.
#
# WAVs and every other file are copied verbatim - sounds are kept exactly as-is.
#
#   optimize-assets.sh <src-dir> <out-dir> [max-px] [quality]
#
#   src-dir   assets tree to read (e.g. "assets"). Never written to.
#   out-dir   where the slimmed copy is written. Wiped and recreated each run.
#   max-px    longest-edge cap for PNGs (default 768).
#   quality   pngquant quality floor-ceiling (default 70-95). Frames that can't
#             meet the floor are left unquantized rather than degraded.

set -euo pipefail

SRC="${1:?usage: optimize-assets.sh <src-dir> <out-dir> [max-px] [quality]}"
OUT="${2:?usage: optimize-assets.sh <src-dir> <out-dir> [max-px] [quality]}"
MAX_PX="${3:-768}"
QUALITY="${4:-70-95}"

[ -d "$SRC" ] || { echo "optimize-assets: src dir not found: $SRC" >&2; exit 1; }
command -v sips     >/dev/null 2>&1 || { echo "optimize-assets: sips not found (macOS only)" >&2; exit 1; }
command -v pngquant >/dev/null 2>&1 || { echo "optimize-assets: pngquant not found (brew install pngquant)" >&2; exit 1; }
# oxipng is an optional final lossless squeeze; proceed without it if absent.
HAVE_OXIPNG=0; command -v oxipng >/dev/null 2>&1 && HAVE_OXIPNG=1

# Fresh copy of the whole tree, then optimize PNGs in place inside the COPY.
rm -rf "$OUT"
mkdir -p "$OUT"
cp -R "$SRC/." "$OUT/"

# 1. Downscale oversized frames.
shrunk=0
while IFS= read -r -d '' img; do
  read -r w h < <(sips -g pixelWidth -g pixelHeight "$img" 2>/dev/null \
    | awk '/pixelWidth/{w=$2} /pixelHeight/{h=$2} END{print w, h}')
  [ -n "$w" ] && [ -n "$h" ] || continue
  longest=$w; [ "$h" -gt "$w" ] && longest=$h
  if [ "$longest" -gt "$MAX_PX" ]; then
    sips -Z "$MAX_PX" "$img" >/dev/null 2>&1 && shrunk=$((shrunk + 1))
  fi
done < <(find "$OUT" -type f -name '*.png' -print0)

# 2. Palette-quantize (lossy but visually identical for this art), in place.
#    --skip-if-larger leaves any frame pngquant can't shrink untouched.
find "$OUT" -type f -name '*.png' -print0 \
  | xargs -0 pngquant --quality="$QUALITY" --skip-if-larger --ext .png --force >/dev/null 2>&1 || true

# 3. Lossless final squeeze.
if [ "$HAVE_OXIPNG" -eq 1 ]; then
  find "$OUT" -type f -name '*.png' -print0 \
    | xargs -0 oxipng -o 2 --strip safe -q >/dev/null 2>&1 || true
fi

png_total="$(find "$OUT" -type f -name '*.png' | wc -l | tr -d ' ')"
src_size="$(du -sh "$SRC" 2>/dev/null | cut -f1)"
out_size="$(du -sh "$OUT" 2>/dev/null | cut -f1)"
echo "optimize-assets: ${png_total} PNGs (${shrunk} downscaled, all quantized$([ "$HAVE_OXIPNG" -eq 1 ] && echo ' + oxipng'))"
echo "optimize-assets: ${SRC} (${src_size}) -> ${OUT} (${out_size})"
