#!/usr/bin/env bash
# Installs the wake-word training code the VM runs on its own (the nightly ambient featcache) from the repo tree this
# script sits in: tools/wakeword/training (no tests) -> $DEST/training, swapped as a whole, plus FRIDAY_VERSION, and
# featcache_nightly.sh -> $DEST. No restart: friday-wwt-featcache.path starts the script fresh each night.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DEST=${FRIDAY_WWT_DIR:-/opt/friday/wwt}
LINE=${FRIDAY_VERSION_LINE:?FRIDAY_VERSION_LINE missing (use scripts/deploy.sh)}
mkdir -p "$DEST"
rm -rf "$DEST/.training.new" "$DEST/.training.old"
cp -r "$HERE/../training" "$DEST/.training.new"
find "$DEST/.training.new" \( -name 'test_*' -o -name '__pycache__' \) -prune -exec rm -rf {} +
echo "$LINE" > "$DEST/.training.new/FRIDAY_VERSION"
[ -d "$DEST/training" ] && mv "$DEST/training" "$DEST/.training.old"
mv "$DEST/.training.new" "$DEST/training"
rm -rf "$DEST/.training.old"
cp "$HERE/featcache_nightly.sh" "$DEST/.featcache_nightly.sh.new" && chmod 755 "$DEST/.featcache_nightly.sh.new"
mv -f "$DEST/.featcache_nightly.sh.new" "$DEST/featcache_nightly.sh"
echo "wake-word training code $LINE installed in $DEST"
