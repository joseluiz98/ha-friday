#!/usr/bin/env bash
# Installs the voice gateway from the repo tree this script sits in: every file of voice_gateway/ goes to the app dir
# the container mounts (each swapped atomically), FRIDAY_VERSION records the version, and the container restart goes
# through the status port, which refuses (409) while a conversation is open: then the restart is just deferred.
#   sudo FRIDAY_VERSION_LINE="<describe> <sha>" bash voice_gateway/install.sh      (scripts/deploy.sh sets the line)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DEST=${FRIDAY_APP_DIR:-/srv/friday/app}
LINE=${FRIDAY_VERSION_LINE:?FRIDAY_VERSION_LINE missing (use scripts/deploy.sh)}
mkdir -p "$DEST"
for f in "$HERE"/*; do
  n=$(basename "$f")
  case "$n" in install.sh|__pycache__) continue ;; esac
  [ -f "$f" ] || continue
  cp "$f" "$DEST/.$n.new" && mv -f "$DEST/.$n.new" "$DEST/$n"
done
echo "$LINE" > "$DEST/.FRIDAY_VERSION.new" && mv -f "$DEST/.FRIDAY_VERSION.new" "$DEST/FRIDAY_VERSION"
echo "voice gateway $LINE installed in $DEST"
[ "${FRIDAY_NO_RESTART:-}" = 1 ] && { echo "restart skipped (FRIDAY_NO_RESTART=1)"; exit 0; }
python3 "$HERE/../scripts/lib/guarded_restart.py" gateway
