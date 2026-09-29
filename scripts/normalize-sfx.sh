#!/usr/bin/env bash
#
# normalize-sfx.sh - level the ORIGINAL (non-voice) sounds to the house level.
#
# The originals were never consistent with each other. Measured before any of
# this: usagi's grabbed1-3 sat at -46 dB mean and spawn at -49 dB - 16 to 19 dB
# UNDER everything else, i.e. effectively inaudible - while danceswirl (-18.6),
# mock (-21.5) and the co-animation tracks (-22.9) ran 7 to 11 dB OVER. Same
# spread the Shadowverse clips had, just in both directions.
#
# Unlike the voice clips these have no .mp3 masters, so a gain applied in place
# would be one-way and repeated runs would stack. Instead the first run stashes a
# pristine copy under .audio-originals/ and every run normalizes FROM that copy,
# which makes this idempotent and reversible: restore with
#   cp .audio-originals/<group>/<name>.wav assets/<group>/sounds/<name>.wav
#
# Usage:
#   bash scripts/normalize-sfx.sh
#   DRY_RUN=1 bash scripts/normalize-sfx.sh     # report gains, write nothing
#   TARGET_MEAN=-28 bash scripts/normalize-sfx.sh

set -euo pipefail

TARGET_MEAN="${TARGET_MEAN:--30}"    # same house level as scripts/normalize-voice.sh
PEAK_CEILING="${PEAK_CEILING:--1.0}"
DRY_RUN="${DRY_RUN:-0}"
STASH=".audio-originals"

cd "$(dirname "$0")/.."
command -v ffmpeg >/dev/null || { echo "ffmpeg not found (brew install ffmpeg)" >&2; exit 1; }

shopt -s nullglob
files=(assets/*/sounds/*.wav)
[ ${#files[@]} -gt 0 ] || { echo "no sfx found" >&2; exit 1; }

printf '%-42s %8s %8s %8s\n' FILE MEAN GAIN PEAK

for dst in "${files[@]}"; do
    rel="${dst#assets/}"                 # usagi/sounds/spawn.wav
    group="${rel%%/*}"
    name="$(basename "$dst")"
    src="$STASH/$group/$name"

    # First sighting of a file: stash it untouched. Never overwrite the stash -
    # that is what keeps this idempotent.
    if [ ! -f "$src" ]; then
        mkdir -p "$STASH/$group"
        cp "$dst" "$src"
    fi

    stats=$(ffmpeg -hide_banner -nostats -i "$src" -af volumedetect -f null - 2>&1)
    mean=$(echo "$stats" | grep mean_volume | awk '{print $5}')
    peak=$(echo "$stats" | grep max_volume  | awk '{print $5}')

    gain=$(awk -v t="$TARGET_MEAN" -v m="$mean" -v p="$peak" -v c="$PEAK_CEILING" \
        'BEGIN { g = t - m; if (p + g > c) g = c - p; printf "%.2f", g }')

    printf '%-42s %8s %8s %8s\n' "$rel" "$mean" "$gain" "$peak"
    [ "$DRY_RUN" = "1" ] && continue

    # Keep each file's own channel count and sample rate - some are stereo.
    tmp="${dst}.tmp.wav"
    ffmpeg -y -loglevel error -i "$src" -af "volume=${gain}dB" -c:a pcm_s16le "$tmp"
    mv "$tmp" "$dst"
done

echo
echo "Levelled ${#files[@]} sfx to ${TARGET_MEAN} dB mean. Originals stashed in $STASH/."
