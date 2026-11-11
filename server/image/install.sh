#!/usr/bin/env bash
# Installs the container image sources and builds the image (run on the VM as root): every file of server/image goes
# to /opt/friday/image (each swapped atomically), FRIDAY_VERSION records the version, and the image is built as
# friday:<describe> labelled friday.sha=<sha>. It never switches the running container — that restarts the voice.
# The OTA installer switches at a safe moment; by hand, when no conversation is open:
#   echo FRIDAY_IMAGE_TAG=<describe> > /opt/friday/image/.env && cd /opt/friday/image && docker compose up -d --no-build
#   sudo FRIDAY_VERSION_LINE="<describe> <sha>" bash server/image/install.sh      (scripts/deploy.sh sets the line)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DEST=${FRIDAY_IMAGE_DIR:-/opt/friday/image}
LINE=${FRIDAY_VERSION_LINE:?FRIDAY_VERSION_LINE missing (use scripts/deploy.sh)}
TAG=${LINE%% *}
SHA=${LINE##* }
mkdir -p "$DEST"
for f in "$HERE"/*; do
  n=$(basename "$f")
  case "$n" in install.sh) continue ;; esac
  [ -f "$f" ] || continue
  cp -p "$f" "$DEST/.$n.new" && mv -f "$DEST/.$n.new" "$DEST/$n"
done
echo "$LINE" > "$DEST/.FRIDAY_VERSION.new" && mv -f "$DEST/.FRIDAY_VERSION.new" "$DEST/FRIDAY_VERSION"
if [ "${FRIDAY_NO_BUILD:-}" = 1 ]; then echo "image sources $LINE installed in $DEST (build skipped)"; exit 0; fi
docker build -q -t "friday:$TAG" --label "friday.sha=$SHA" "$DEST" >/dev/null
echo "image sources $LINE installed in $DEST; built friday:$TAG (not switched)"
