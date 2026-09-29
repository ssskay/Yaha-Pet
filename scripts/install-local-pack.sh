#!/usr/bin/env bash
#
# install-local-pack.sh - copy the personal-use local pack into
# ~/Library/Application Support/Yaha-Pet/local-pack so the INSTALLED app
# (the public DMG, which never contains these files) picks them up too.
#
# The pack mirrors assets/ (local-pack/assets/<name>/sounds/voice/, etc.) and
# is gitignored. Run from the repo root:  scripts/install-local-pack.sh
# Re-run after adding clips. Remove the folder to go back to the public build.
set -euo pipefail
SRC="${1:-local-pack}"
DEST="$HOME/Library/Application Support/Yaha-Pet/local-pack"
[ -d "$SRC" ] || { echo "install-local-pack: $SRC not found (run from the repo root)" >&2; exit 1; }
mkdir -p "$DEST"
rsync -a --delete --exclude '*.mp3' --exclude '.DS_Store' "$SRC/" "$DEST/"
echo "install-local-pack: $(find "$DEST" -name '*.wav' | wc -l | tr -d ' ') wavs, $(find "$DEST" -name '*.png' | wc -l | tr -d ' ') frames -> $DEST"
echo "install-local-pack: restart Yaha-Pet to load them"
