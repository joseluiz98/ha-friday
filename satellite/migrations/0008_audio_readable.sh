#!/usr/bin/env bash
# 0008 (23/11): moves the ALSA board files out of /etc/friday-satelite (mode 0700, jose's agent state) into
# /etc/friday-audio (0755). The music player (sendspin-cli) runs as another user and could not follow /etc/asound.conf
# into the 0700 folder, so it never found a PCM. The old files stay where they are (a rollback to v0.4.3 still finds its
# link target). Idempotent. Runs as root.
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
if [ -z "${FRIDAY_RELEASE_DIR:-}" ]; then echo "0008: FRIDAY_RELEASE_DIR not set" >&2; exit 1; fi
S=$FRIDAY_RELEASE_DIR/system
for f in jabra.conf xvf3800.conf friday-audio-select; do  # all sources checked before anything is touched
  if [ ! -f "$S/$f" ]; then echo "0008: missing $f in the release system files" >&2; exit 1; fi
done
inst() {  # source mode dest
  mkdir -p "$(dirname "$R/$3")" && cp "$S/$1" "$R/$3.new" && chmod "$2" "$R/$3.new" && mv -f "$R/$3.new" "$R/$3"
}
mkdir -p "$R/etc/friday-audio" && chmod 0755 "$R/etc/friday-audio" || exit 1
inst jabra.conf 0644 etc/friday-audio/jabra.conf || exit 1
inst xvf3800.conf 0644 etc/friday-audio/xvf3800.conf || exit 1
inst friday-audio-select 0755 usr/local/sbin/friday-audio-select || exit 1

repoint() {  # the link follows the stored board; missing or "none" keeps the Jabra file
  b=$(cat "$R/etc/friday-satelite/audio_board" 2>/dev/null || true)
  case "$b" in xvf3800) ;; *) b=jabra ;; esac
  mkdir -p "$R/etc" && ln -sfn "/etc/friday-audio/$b.conf" "$R/etc/asound.conf.new" && mv -fT "$R/etc/asound.conf.new" "$R/etc/asound.conf"
}
if [ -z "$R" ]; then  # real device: the selector decides from the hardware and writes audio_board/mic
  "/usr/local/sbin/friday-audio-select" >/dev/null 2>&1 || repoint || exit 1
  systemctl restart sendspin-cli >/dev/null 2>&1 || true
else
  repoint || exit 1
fi
echo "0008: audio board files readable in /etc/friday-audio"
