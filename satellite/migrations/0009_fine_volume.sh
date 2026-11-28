#!/usr/bin/env bash
# 0009 (27/11): installs the ALSA board files with the `Fine` softvol (fine volume). Activation runs migrations BEFORE
# it installs system files, so this one installs them itself, then restarts what keeps the audio output open with the
# old chain (the player and the silence feeder; activation restarts only the program and the agent).
# The activation snapshot already covers both files, so a rollback restores them. Idempotent. Runs as root.
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
if [ -z "${FRIDAY_RELEASE_DIR:-}" ]; then echo "0009: FRIDAY_RELEASE_DIR not set" >&2; exit 1; fi
S=$FRIDAY_RELEASE_DIR/system
for f in jabra.conf xvf3800.conf; do  # all sources checked before anything is touched
  if [ ! -f "$S/$f" ]; then echo "0009: missing $f in the release system files" >&2; exit 1; fi
done
inst() {  # source mode dest
  mkdir -p "$(dirname "$R/$3")" && cp "$S/$1" "$R/$3.new" && chmod "$2" "$R/$3.new" && mv -f "$R/$3.new" "$R/$3"
}
mkdir -p "$R/etc/friday-audio" && chmod 0755 "$R/etc/friday-audio" || exit 1
inst jabra.conf 0644 etc/friday-audio/jabra.conf || exit 1
inst xvf3800.conf 0644 etc/friday-audio/xvf3800.conf || exit 1
if [ -z "$R" ]; then
  systemctl try-restart sendspin-cli friday-silence >/dev/null 2>&1 || true
fi
echo "0009: Fine volume audio files installed"
