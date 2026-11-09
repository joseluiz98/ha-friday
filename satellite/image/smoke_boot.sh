#!/bin/bash
# Boots a throwaway copy of a finished image in an arm64 systemd-nspawn container (network isolated) and checks what
# only a real boot shows: first-boot detection, SSH host keys, firstboot, the agent listening. Called by build.sh.
#   SUDO=sudo ./smoke_boot.sh image.img
# Audio/Wi-Fi do not exist here, so friday-satelite is NOT judged (without a Jabra it exits by design). The container
# prints many emulation errors (credentials, journald): they are noise, only the checks below count.
set -uo pipefail
IMG=${1:?image}; SUDO=${SUDO-sudo}
export PATH="$PATH:/usr/sbin:/sbin"
D=$(mktemp -d "${TMPDIR:-$HOME/imgbuild}/smoke.XXXXXX"); R=$D/root; M=friday-smoke; LOOP=""; fail=0
bad() { echo "FAIL: $*" >&2; fail=1; }
cleanup() {
  set +e
  if $SUDO machinectl show "$M" >/dev/null 2>&1; then
    $SUDO machinectl terminate "$M"; sleep 8
    $SUDO machinectl show "$M" >/dev/null 2>&1 && $SUDO machinectl kill "$M" --signal=SIGKILL --kill-whom=all && sleep 5
  fi
  $SUDO umount -l "$R/boot/firmware" "$R" 2>/dev/null
  [ -n "$LOOP" ] && $SUDO losetup -d "$LOOP"
  $SUDO rm -rf "$D"
}
trap cleanup EXIT
mkdir -p "$R"
cp --sparse=always "$IMG" "$D/copy.img"
LOOP=$($SUDO losetup -Pf --show "$D/copy.img")
$SUDO mount "${LOOP}p2" "$R"; $SUDO mount "${LOOP}p1" "$R/boot/firmware"
$SUDO systemd-nspawn -b -D "$R" --machine="$M" --private-network --console=read-only > "$D/console.log" 2>&1 &
for _ in $(seq 1 36); do
  sleep 10
  $SUDO test -e "$R/etc/friday-satelite/.firstboot-done" && [ "$($SUDO ls "$R/etc/ssh" | grep -c '^ssh_host_')" -gt 0 ] && break
done
sleep 60   # the agent takes a while under emulation
L=$($SUDO machinectl show "$M" -p Leader --value 2>/dev/null)
[ -n "$L" ] || { bad "container did not boot"; tail -5 "$D/console.log" >&2; exit 1; }
in_c() { $SUDO nsenter -t "$L" -a "$@"; }
[ "$($SUDO ls "$R/etc/ssh" | grep -c '^ssh_host_')" -gt 0 ] || bad "no SSH host keys (first boot not detected?)"
in_c /usr/sbin/sshd -t >/dev/null 2>&1 || bad "sshd -t fails"
[ "$(in_c systemctl is-active ssh 2>/dev/null)" = active ] || bad "ssh not active"
$SUDO test -e "$R/etc/friday-satelite/.firstboot-done" || bad "firstboot did not finish"
for f in id config.json pairable; do $SUDO test -e "$R/etc/friday-satelite/$f" || bad "firstboot left no $f"; done
for _ in $(seq 1 12); do in_c ss -ltn 2>/dev/null | grep -q ':8701 ' && break; sleep 10; done
in_c ss -ltn 2>/dev/null | grep -q ':8701 ' || bad "agent not listening on 8701"
[ "$fail" = 0 ] && echo "smoke boot: OK" || { echo "smoke boot: FAILED" >&2; exit 1; }
