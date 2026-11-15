#!/bin/bash
# Static checks of a customised, still-mounted image (called by build.sh; also usable alone):
#   MNT=/path/to/mounted/rootfs SUDO=sudo ./verify_image.sh
# Prints only names and counts, never a value. Exit 1 on the first group of failures.
set -uo pipefail
MNT=${MNT:?mounted rootfs}
SUDO=${SUDO-sudo}
fail=0
bad() { echo "FAIL: $*" >&2; fail=1; }
has() { $SUDO test -e "$MNT$1" || bad "missing $1"; }

echo "-- files"
if $SUDO test -L "$MNT/opt/friday/current"; then  # versioned layout (OTA, plan 2026-11-10 Task 12)
  C=/opt/friday/current; PROG=$C/program; PY=$C/venv/bin/python; AGENT=$C/agent
  for f in $C/.complete $C/manifest.json $C/venv/bin/python /usr/local/sbin/friday-ota-apply /var/lib/friday/migrations.json; do
    has "$f"; done
  [ "$($SUDO env TMPDIR=/tmp chroot "$MNT" stat -c %U /var/lib/friday/ota/downloads 2>/dev/null)" = jose ] || bad "downloads folder must be jose's"
  $SUDO grep -q "/opt/friday/current/venv/bin/python" "$MNT/etc/systemd/system/friday-satelite.service" || bad "program unit not on current"
  $SUDO grep -q "friday-ota-apply" "$MNT/etc/sudoers.d/friday-satellite-agent" || bad "sudoers lacks friday-ota-apply"
  $SUDO test -e "$MNT/home/jose/satelite/friday_satelite.py" && bad "code in ~/satelite (data only on the versioned layout)"
else
  PROG=/home/jose/satelite; PY=/home/jose/oww/bin/python; AGENT=/opt/friday-satellite-agent
fi
for f in $PROG/friday_satelite.py $PROG/firstboot.py $PROG/netwatch.py $PROG/net_recovery.py $PROG/ring.py \
         $PROG/vendor/xvf_host.py /home/jose/satelite/sons/onboarding /home/jose/modelos/friday.onnx \
         $PY /home/jose/satelite-sync/sync_recordings.sh $AGENT/friday_satellite_agent/__main__.py \
         $AGENT/friday_agent_core/server.py /usr/local/sbin/friday-agent-apply \
         /usr/local/sbin/friday-wifi-apply /etc/asound.conf /etc/udev/rules.d/99-respeaker.rules \
         /etc/NetworkManager/conf.d/wifi-powersave.conf /etc/systemd/journald.conf.d/50-persistente.conf \
         /etc/sudoers.d/010-jose-nopasswd /home/jose/.ssh/authorized_keys; do has "$f"; done
$SUDO grep -q 'wifi.powersave = 2' "$MNT/etc/NetworkManager/conf.d/wifi-powersave.conf" || bad "wifi powersave"
$SUDO grep -q 'SystemMaxUse=500M' "$MNT/etc/systemd/journald.conf.d/50-persistente.conf" || bad "journal size"
[ "$($SUDO stat -c %a "$MNT/home/jose/.ssh/authorized_keys" 2>/dev/null)" = 600 ] || bad "authorized_keys mode"
[ "$($SUDO grep -c '^ssh-' "$MNT/home/jose/.ssh/authorized_keys" 2>/dev/null)" = 1 ] || bad "authorized_keys must hold exactly the HA key"
$SUDO grep -q 'idVendor}=="2886"' "$MNT/etc/udev/rules.d/99-respeaker.rules" || bad "udev rule"
$SUDO grep -q 'idVendor}=="0b0e"' "$MNT/etc/udev/rules.d/70-jabra-satelite.rules" || bad "Jabra udev rule"

echo "-- units enabled"
for u in friday-firstboot friday-satelite friday-satellite-agent; do
  $SUDO test -L "$MNT/etc/systemd/system/multi-user.target.wants/$u.service" || bad "$u.service not enabled"
done
$SUDO test -L "$MNT/etc/systemd/system/timers.target.wants/friday-sync-recordings.timer" || bad "sync timer not enabled"
$SUDO test -L "$MNT/etc/systemd/system/timers.target.wants/friday-netwatch.timer" || bad "netwatch timer not enabled"
has /etc/systemd/system/friday-netwatch.service; has $PROG/netwatch.py
$SUDO grep -q '^StateDirectory=friday-netwatch' "$MNT/etc/systemd/system/friday-netwatch.service" || bad "netwatch StateDirectory (network recovery state)"
$SUDO test -L "$MNT/etc/systemd/system/timers.target.wants/satelite-status.timer" && bad "satelite-status.timer must stay disabled (needs the house webhook id)"
$SUDO grep -q 'ConditionPathExists=!/etc/friday-satelite/.firstboot-done' "$MNT/etc/systemd/system/friday-firstboot.service" || bad "firstboot condition"
$SUDO grep -q 'Before=.*friday-satelite.service' "$MNT/etc/systemd/system/friday-firstboot.service" || bad "firstboot ordering"

echo "-- nothing from the house"
$SUDO test -e "$MNT/etc/friday-satelite/id" && bad "an id is baked in"
$SUDO test -e "$MNT/etc/friday-satelite/config.json" && bad "a config is baked in"
$SUDO test -e "$MNT/etc/friday-satelite/ha_token" && bad "a token is baked in"
n=$($SUDO find "$MNT/home/jose" "$MNT/etc" "$MNT/opt" \( -name '*verificador*' -o -name 'id_satsync*' -o -name 'vozes' -o -name 'gravacoes' -o -name 'webhook_id' -o -name 'satellites.json' \) -not -name '*.py' 2>/dev/null | wc -l)
[ "$n" = 0 ] || bad "$n file(s) named like voices/verifier/keys/recordings"
[ -n "${PRODUCTION_IDS:-}" ] || bad "PRODUCTION_IDS not given (build.sh reads FRIDAY_SATELLITE_IDS from the VM local config)"
for id in ${PRODUCTION_IDS:-}; do
  n=$($SUDO grep -rIl "$id" "$MNT/home/jose" "$MNT/etc/friday-satelite" "$MNT/opt" "$MNT/usr/local" 2>/dev/null | wc -l)
  [ "$n" = 0 ] || bad "$n file(s) mention a production satellite id"
done
$SUDO test -s "$MNT/etc/friday-satelite/house.json" || bad "house.json (VM address) missing"
$SUDO test -s "$MNT$PROG/FRIDAY_VERSION" || bad "FRIDAY_VERSION missing"
n=$($SUDO find "$MNT/etc/ssh" -name 'ssh_host_*' 2>/dev/null | wc -l)
[ "$n" = 0 ] || bad "ssh host keys baked in"
[ "$($SUDO cat "$MNT/etc/machine-id" 2>/dev/null)" = uninitialized ] || bad "machine-id must be 'uninitialized' (empty is not a first boot)"

echo "-- python (arm64 venv, run under qemu)"
$SUDO env TMPDIR=/tmp chroot "$MNT" $PY -c \
  "import spake2, usb, libusb_package, openwakeword, zeroconf, cryptography, aiohttp, numpy, onnxruntime" 2>/dev/null \
  || bad "venv imports failed"
$SUDO env TMPDIR=/tmp chroot "$MNT" $PY -c "import sys; sys.path.insert(0,'$PROG'); import ring, firstboot, comandos, maestro" \
  2>/dev/null || bad "program modules do not import"

[ "$fail" = 0 ] && echo "static checks: OK" || { echo "static checks: FAILED" >&2; exit 1; }
