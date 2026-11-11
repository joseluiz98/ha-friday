#!/usr/bin/env bash
# Installs the VM services from the repo tree this script sits in (run on the VM as root): status port, essentials
# backup, cataloger (Lucid), cost, aec and asr go to /opt/friday/<name> (files copied
# over, never deleting what is not in the repo), the systemd units to /etc/systemd/system. Restarts only the status
# port (no conversation impact). The container image is its own component (server/image/install.sh).
#   sudo FRIDAY_VERSION_LINE="<describe> <sha>" bash server/install.sh      (scripts/deploy.sh sets the line)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OPT=${FRIDAY_OPT:-/opt/friday}
UNITS=${FRIDAY_UNITS:-/etc/systemd/system}
LINE=${FRIDAY_VERSION_LINE:?FRIDAY_VERSION_LINE missing (use scripts/deploy.sh)}
for d in status essentials cataloger cost aec asr; do
  mkdir -p "$OPT/$d"
  for f in "$HERE/$d"/*; do
    n=$(basename "$f"); [ -f "$f" ] || continue
    cp -p "$f" "$OPT/$d/.$n.new" && mv -f "$OPT/$d/.$n.new" "$OPT/$d/$n"
  done
  echo "$LINE" > "$OPT/$d/FRIDAY_VERSION"
done
cp "$HERE/../voice_gateway/local_config.py" "$OPT/cataloger/local_config.py"   # Lucid reads the owner name from it
mkdir -p "$OPT/systemd" "$UNITS"
for u in "$HERE"/systemd/*.service "$HERE"/systemd/*.timer "$HERE/status/friday-status.service"; do
  cp "$u" "$OPT/systemd/" && cp "$u" "$UNITS/"
done
if [ "${FRIDAY_NO_SYSTEMCTL:-}" != 1 ]; then
  systemctl daemon-reload
  systemctl restart friday-status
fi
echo "server services $LINE installed in $OPT"
