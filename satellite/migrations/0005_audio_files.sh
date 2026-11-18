#!/usr/bin/env bash
# 0005 (18/11): reinstalls the audio board files that 0004 should have installed. On satellites updated by the v0.3.8
# packager the release carried no system/ audio files, and 0004 skipped the missing sources silently, so it was recorded
# as done with nothing installed (friday-satelite.service then pointed at a missing friday-audio-select). The release
# now declares those files (package.satellite.system) and 0004 fails when they are missing; this migration runs once
# more on every satellite that already recorded 0004. Idempotent. Runs as root.
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
if [ -z "${FRIDAY_RELEASE_DIR:-}" ]; then echo "0005: FRIDAY_RELEASE_DIR not set" >&2; exit 1; fi
S=$FRIDAY_RELEASE_DIR/system
inst() {  # source mode dest
  if [ ! -f "$S/$1" ]; then echo "0005: missing $1 in the release system files" >&2; return 1; fi
  mkdir -p "$(dirname "$R/$3")" && cp "$S/$1" "$R/$3.new" && chmod "$2" "$R/$3.new" && mv -f "$R/$3.new" "$R/$3"
}
inst friday-audio-select 0755 usr/local/sbin/friday-audio-select || exit 1
inst jabra.conf 0644 etc/friday-satelite/asound/jabra.conf || exit 1
inst xvf3800.conf 0644 etc/friday-satelite/asound/xvf3800.conf || exit 1
inst 90-friday-audio.rules 0644 etc/udev/rules.d/90-friday-audio.rules || exit 1
inst audio-board.conf 0644 etc/systemd/system/sendspin-cli.service.d/audio-board.conf || exit 1
inst friday-silence.service 0644 etc/systemd/system/friday-silence.service || exit 1
[ -z "$R" ] && udevadm control --reload-rules >/dev/null 2>&1 || true
echo "0005: audio files installed"
