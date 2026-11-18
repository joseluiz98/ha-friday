#!/usr/bin/env bash
# 0004 (17/11): the provisional jabra-only.conf drop-ins (15/11, XVF3800 bench test on the first satellite) leave; the
# voice program and the player now start through friday-audio-select (ExecCondition), which picks the audio board.
# The new units, the rule and /etc/asound.conf come with the release's system files. Idempotent. Runs as root.
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
rm -f "$R/etc/systemd/system/friday-satelite.service.d/jabra-only.conf" \
      "$R/etc/systemd/system/sendspin-cli.service.d/jabra-only.conf"
rmdir "$R/etc/systemd/system/friday-satelite.service.d" 2>/dev/null || true
# Review C1 (17/11): an update from v0.3.8 runs the OLD friday-ota-apply, which does not know the audio files and would
# leave the new friday-satelite.service pointing at a missing friday-audio-select. Install them here from the release.
# 18/11: a missing source is now a failure (exit 1), not a silent skip: a skip got 0004 recorded as done with nothing installed.
S=${FRIDAY_RELEASE_DIR:-}/system
inst() {  # source mode dest
  if [ ! -f "$S/$1" ]; then echo "0004: missing $1 in the release system files" >&2; return 1; fi
  mkdir -p "$(dirname "$R/$3")" && cp "$S/$1" "$R/$3.new" && chmod "$2" "$R/$3.new" && mv -f "$R/$3.new" "$R/$3"
}
if [ -n "${FRIDAY_RELEASE_DIR:-}" ]; then
  inst friday-audio-select 0755 usr/local/sbin/friday-audio-select || exit 1
  inst jabra.conf 0644 etc/friday-satelite/asound/jabra.conf || exit 1
  inst xvf3800.conf 0644 etc/friday-satelite/asound/xvf3800.conf || exit 1
  inst 90-friday-audio.rules 0644 etc/udev/rules.d/90-friday-audio.rules || exit 1
  inst audio-board.conf 0644 etc/systemd/system/sendspin-cli.service.d/audio-board.conf || exit 1
  inst friday-silence.service 0644 etc/systemd/system/friday-silence.service || exit 1
  [ -z "$R" ] && udevadm control --reload-rules >/dev/null 2>&1 || true
fi
echo "0004: jabra-only drop-ins removed; audio files installed"
