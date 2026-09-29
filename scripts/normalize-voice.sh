#!/usr/bin/env bash
#
# normalize-voice.sh - regenerate the voice .wav files from their .mp3 masters
# at the project's house playback level.
#
# Why this exists: every in-game sfx already in assets/ sits at about -30 dB
# mean RMS (chiikawa/hachiware were normalized that way at some point). The
# Shadowverse voice rips came in around -19 dB, i.e. ~10 dB hotter, which reads
# as roughly twice as loud. That is jarring on its own and outright wrong for
# the hurt lines, which share ONE random pool with the "grabbed" squeaks - the
# same grab would be a whisper or a shout depending on which file won the roll.
#
# The .mp3 files are the untouched masters straight from svgdb.me and the .wav
# files are derived (QSoundEffect can't play mp3), so this is safe to re-run:
# it always rebuilds from the master rather than re-gaining an already-gained
# wav, so repeated runs don't stack attenuation or pile up requantization noise.
#
# Output format matches the existing wavs exactly: mono, 44.1 kHz, 16-bit PCM.
#
# Usage:
#   bash scripts/normalize-voice.sh            # normalize to -30 dB mean
#   TARGET_MEAN=-27 bash scripts/normalize-voice.sh   # a little more forward
#   DRY_RUN=1 bash scripts/normalize-voice.sh  # report gains, write nothing

set -euo pipefail

TARGET_MEAN="${TARGET_MEAN:--30}"   # dB, matches the existing sfx house level
PEAK_CEILING="${PEAK_CEILING:--1.0}" # dBFS, never let a normalized clip get closer
DRY_RUN="${DRY_RUN:-0}"

cd "$(dirname "$0")/.."

command -v ffmpeg >/dev/null || { echo "ffmpeg not found (brew install ffmpeg)" >&2; exit 1; }

shopt -s nullglob
masters=(local-pack/assets/*/sounds/voice/*.mp3)
[ ${#masters[@]} -gt 0 ] || { echo "no voice masters found under local-pack/assets/*/sounds/voice/" >&2; exit 1; }

printf '%-46s %8s %8s %8s\n' FILE MEAN GAIN PEAK

for src in "${masters[@]}"; do
    dst="${src%.mp3}.wav"

    stats=$(ffmpeg -hide_banner -nostats -i "$src" -af volumedetect -f null - 2>&1)
    mean=$(echo "$stats" | grep mean_volume | awk '{print $5}')
    peak=$(echo "$stats" | grep max_volume  | awk '{print $5}')

    # Gain to hit the target mean, then pulled back if it would push the peak
    # past the ceiling. Plain gain only - no compression, so the performance
    # keeps its own dynamics.
    gain=$(awk -v t="$TARGET_MEAN" -v m="$mean" -v p="$peak" -v c="$PEAK_CEILING" \
        'BEGIN { g = t - m; if (p + g > c) g = c - p; printf "%.2f", g }')

    printf '%-46s %8s %8s %8s\n' "${src#local-pack/assets/}" "$mean" "$gain" "$peak"
    [ "$DRY_RUN" = "1" ] && continue

    tmp="${dst}.tmp.wav"
    ffmpeg -y -loglevel error -i "$src" -af "volume=${gain}dB" \
        -ar 44100 -ac 1 -c:a pcm_s16le "$tmp"
    mv "$tmp" "$dst"
done

echo
echo "Wrote ${#masters[@]} wavs at ${TARGET_MEAN} dB mean (peak ceiling ${PEAK_CEILING} dBFS)."
