#!/usr/bin/env bash
# Installs the VM services from the repo tree this script sits in (run on the VM as root): status port, essentials
# backup, cataloger (Lucid), cost, aec, asr, browser (idle Chromium closer) and images (the images server wrapper) go
# to /opt/friday/<name> (files copied over; of what is not in the repo only stale .py files are removed, so a module deleted from the repo stops being
# importable), the systemd units to /etc/systemd/system (retired units are disabled and removed). Restarts only the
# status port (no conversation impact). The container image is its own component (server/image/install.sh).
#   sudo FRIDAY_VERSION_LINE="<describe> <sha>" bash server/install.sh      (scripts/deploy.sh sets the line)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OPT=${FRIDAY_OPT:-/opt/friday}
UNITS=${FRIDAY_UNITS:-/etc/systemd/system}
STATE=${FRIDAY_STATE:-/srv/friday/state}
RETIRED_UNITS="friday-memory-index.timer friday-memory-index.service"   # OpenClaw 2026.11.3 refuses `memory index` while the gateway runs
LINE=${FRIDAY_VERSION_LINE:?FRIDAY_VERSION_LINE missing (use scripts/deploy.sh)}
for d in status essentials cataloger cost aec asr browser images; do
  mkdir -p "$OPT/$d"
  for f in "$HERE/$d"/*; do
    n=$(basename "$f"); [ -f "$f" ] || continue
    cp -p "$f" "$OPT/$d/.$n.new" && mv -f "$OPT/$d/.$n.new" "$OPT/$d/$n"
  done
  for f in "$OPT/$d"/*.py; do
    n=$(basename "$f"); [ -f "$f" ] || continue
    [ "$d/$n" = cataloger/local_config.py ] && continue   # copied below from voice_gateway/
    [ -e "$HERE/$d/$n" ] || rm -f "$f"
  done
  echo "$LINE" > "$OPT/$d/FRIDAY_VERSION"
done
cp "$HERE/../voice_gateway/local_config.py" "$OPT/cataloger/local_config.py"   # Lucid reads the owner name from it
mkdir -p "$OPT/systemd" "$UNITS"
for u in "$HERE"/systemd/*.service "$HERE"/systemd/*.timer "$HERE/status/friday-status.service"; do
  cp "$u" "$OPT/systemd/" && cp "$u" "$UNITS/"
done
for u in $RETIRED_UNITS; do
  [ "${FRIDAY_NO_SYSTEMCTL:-}" = 1 ] || systemctl disable --now "$u" 2>/dev/null || true
  rm -f "$UNITS/$u" "$OPT/systemd/$u"
done
rm -f "$STATE/memory-index.ok"
if [ "${FRIDAY_NO_SYSTEMCTL:-}" != 1 ]; then
  systemctl daemon-reload
  systemctl restart friday-status
  systemctl enable --now friday-browser-idle.timer   # idle Chromium closer (the other timers were enabled by hand)
  systemctl enable --now friday-images-http.service  # satellite images for the Imager manifest (read-only, LAN)
fi
echo "server services $LINE installed in $OPT"
