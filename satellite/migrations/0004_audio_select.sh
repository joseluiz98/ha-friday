#!/usr/bin/env bash
# 0004 (17/11): the provisional jabra-only.conf drop-ins (15/11, XVF3800 bench test on the first satellite) leave; the
# voice program and the player now start through friday-audio-select (ExecCondition), which picks the audio board.
# The new units, the rule and /etc/asound.conf come with the release's system files. Idempotent. Runs as root.
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
rm -f "$R/etc/systemd/system/friday-satelite.service.d/jabra-only.conf" \
      "$R/etc/systemd/system/sendspin-cli.service.d/jabra-only.conf"
rmdir "$R/etc/systemd/system/friday-satelite.service.d" 2>/dev/null || true
echo "0004: jabra-only drop-ins removed"
