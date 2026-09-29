#!/usr/bin/env bash
#
# vet-voice.sh - audition the voice clips one at a time and reject the duds.
#
# Plays each clip at the volume the app actually uses, then waits for a key:
#
#   k / return   keep it
#   d            reject it
#   r            replay
#   s            skip to the next character
#   q            quit (everything so far is already applied)
#
# A rejected clip is MOVED, not deleted, to .voice-rejected/<character>/ at the
# repo root - both the .wav and its .mp3 master together. Both matter:
#   - moving the .mp3 too stops scripts/normalize-voice.sh from regenerating the
#     .wav you just threw out
#   - .voice-rejected/ lives outside assets/, so rejects are never bundled into
#     the app the way an assets/<char>/sounds/voice/_rejected/ folder would be
# Changed your mind? Move the pair back and re-run normalize-voice.sh.
#
# Removing a clip is always safe at runtime: Character.play_voice() looks its
# labels up by name and quietly does nothing when none are present, so a missing
# line just means that beat is silent for that character. The lines the app
# actually plays are tagged [used] below; everything else is currently unused.
#
# Usage:
#   bash scripts/vet-voice.sh                 # every character
#   bash scripts/vet-voice.sh chiikawa hachiware
#   VOLUME=1.0 bash scripts/vet-voice.sh      # audition louder than the app plays

set -uo pipefail

cd "$(dirname "$0")/.."

VOLUME="${VOLUME:-0.6}"   # matches Character.voiceplayer's volume in Yaha-Pet!.py
REJECT_DIR=".voice-rejected"

command -v afplay >/dev/null || { echo "afplay not found (this script is macOS-only)" >&2; exit 1; }

characters=("$@")
if [ ${#characters[@]} -eq 0 ]; then
    characters=(chiikawa hachiware usagi)
fi

# What each label is wired to, so you know what you're giving up.
usage_of() {
    case "$1" in
        greeting)          echo "[used] spawn + Say hi!" ;;
        thanks)            echo "[used] after a co-animation" ;;
        shocked)           echo "[used] hard-throw crash landing" ;;
        victory)           echo "[used] when a dance finishes" ;;
        concede1|concede2) echo "[used] on despawn (kick)" ;;
        hurt1|hurt2|hurt3|hurt4|hurt5) echo "[used] grab pool, alongside grabbed*.wav" ;;
        *)                 echo "unused" ;;
    esac
}

kept=0; rejected=0

for char in "${characters[@]}"; do
    dir="local-pack/assets/$char/sounds/voice"
    [ -d "$dir" ] || { echo "no voice folder for $char, skipping"; continue; }

    echo
    echo "=============================================="
    echo " $char - k keep · d reject · r replay · s skip character · q quit"
    echo "=============================================="

    skip_char=0
    for wav in "$dir"/*.wav; do
        [ -e "$wav" ] || continue
        [ "$skip_char" = "1" ] && break

        base="$(basename "$wav" .wav)"        # e.g. 16_hurt1
        label="${base#*_}"                    # e.g. hurt1

        while true; do
            printf '\n  %-16s %-40s ' "$label" "$(usage_of "$label")"
            afplay -v "$VOLUME" "$wav" 2>/dev/null
            printf '> '
            read -rsn1 key
            case "$key" in
                d|D)
                    mkdir -p "$REJECT_DIR/$char"
                    mv "$wav" "$REJECT_DIR/$char/"
                    [ -e "$dir/$base.mp3" ] && mv "$dir/$base.mp3" "$REJECT_DIR/$char/"
                    echo "rejected -> $REJECT_DIR/$char/"
                    rejected=$((rejected + 1))
                    break ;;
                r|R)
                    echo "replay" ;;
                s|S)
                    echo "skipping rest of $char"
                    skip_char=1
                    break ;;
                q|Q)
                    echo "quit"
                    echo
                    echo "kept $kept · rejected $rejected"
                    exit 0 ;;
                *)
                    echo "kept"
                    kept=$((kept + 1))
                    break ;;
            esac
        done
    done
done

echo
echo "kept $kept · rejected $rejected"
if [ "$rejected" -gt 0 ]; then
    echo "Rejects are in $REJECT_DIR/ (wav + mp3 master together). Rebuild to apply:"
    echo "  /usr/bin/python3 -m PyInstaller --noconfirm Yaha-Pet.spec"
fi
