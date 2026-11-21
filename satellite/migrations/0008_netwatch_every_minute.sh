#!/usr/bin/env bash
# 0008 (21/11): friday-netwatch.timer runs every minute (was 5 min) so the ring turns red 2-3 min after the network
# drops (spec 2026-11-21-error-light). The timer unit lives in /etc/systemd/system from the image and OTA does not
# rewrite it, so a drop-in overrides the interval. The ladder is paced by time in net_recovery (a round counts at most
# every 5 min), so the faster timer does not bounce the Wi-Fi more often.
# Idempotent. Runs as root (friday-ota-apply).
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
D="$R/etc/systemd/system/friday-netwatch.timer.d"
mkdir -p "$D"
printf '[Timer]\nOnUnitActiveSec=\nOnUnitActiveSec=1min\n' > "$D/.interval.conf.tmp" && mv -f "$D/.interval.conf.tmp" "$D/interval.conf"
if [ -z "$R" ]; then
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl restart friday-netwatch.timer >/dev/null 2>&1 || true
fi
echo "0008: netwatch every minute"
