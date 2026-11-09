#!/usr/bin/env bash
# Installs the satellite program (run on the Pi as jose) from the repo tree this script sits in: the program's .py
# files (no tests) go to ~/satelite, each swapped atomically, plus FRIDAY_VERSION.pending. The restart happens only
# when the satellite is idle (estado "parada", no timers, no alarm) and the program answers; otherwise nothing retries:
# the new files load at the service's next start, and only then the program promotes the .pending file to
# FRIDAY_VERSION (so the version the agent reports is always the code that is running).
# The vault files (vendor, sounds) are not here: the update bundle or the image brings them.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DEST=${FRIDAY_PROGRAM_DIR:-/home/jose/satelite}
LINE=${FRIDAY_VERSION_LINE:?FRIDAY_VERSION_LINE missing (use scripts/deploy.sh)}
mkdir -p "$DEST"
for f in "$HERE"/*.py; do
  n=$(basename "$f")
  case "$n" in test_*) continue ;; esac
  cp "$f" "$DEST/.$n.new" && mv -f "$DEST/.$n.new" "$DEST/$n"
done
if [ -d "$HERE/vendor" ]; then mkdir -p "$DEST/vendor"; cp "$HERE/vendor/SOURCE.md" "$DEST/vendor/" 2>/dev/null || true; fi
echo "$LINE" > "$DEST/.FRIDAY_VERSION.new" && mv -f "$DEST/.FRIDAY_VERSION.new" "$DEST/FRIDAY_VERSION.pending"
echo "satellite program $LINE installed in $DEST"
[ "${FRIDAY_NO_RESTART:-}" = 1 ] && { echo "restart skipped (FRIDAY_NO_RESTART=1)"; exit 0; }
python3 "$HERE/../../scripts/lib/guarded_restart.py" program
