#!/usr/bin/env bash
# 0002 (16/11): the old status page (satelite-status.sh -> HA webhook) is retired; the satellite agent reports the same
# health to the F.R.I.D.A.Y. integration. Stops and removes its timer, unit and script. /etc/satelite (the satellite's
# id, mic and the old webhook id) stays: an older release still finds what it expects if it is rolled back to.
# Idempotent. Runs as root (friday-ota-apply).
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
if [ -z "$R" ]; then
  systemctl disable --now satelite-status.timer >/dev/null 2>&1 || true
  systemctl stop satelite-status.service >/dev/null 2>&1 || true
fi
rm -f "$R/etc/systemd/system/satelite-status.timer" "$R/etc/systemd/system/satelite-status.service" \
      "$R/etc/systemd/system/timers.target.wants/satelite-status.timer" "$R/usr/local/bin/satelite-status.sh"
[ -z "$R" ] && systemctl daemon-reload >/dev/null 2>&1 || true
echo "0002: status page retired"
