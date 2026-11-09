#!/bin/bash
# VM side. Builds the F.R.I.D.A.Y. satellite image: the official Raspberry Pi OS Lite (64-bit, Trixie) image, grown,
# loop-mounted, customised in an arm64 chroot (qemu-user-static) with the payload that stage.sh copied, checked, and
# compressed with its Imager manifest. Needs root for losetup/mount/chroot: run it with sudo, or let it call sudo.
#   ./build.sh --check        only report what is missing (no root needed)
#   ./build.sh                full build; output in /srv/nobackup/images/
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${FRIDAY_IMG_WORK:-$HOME/imgbuild/work}
OUTDIR=${FRIDAY_IMG_OUT:-/srv/nobackup/images}
GROW=${FRIDAY_IMG_GROW:-2G}            # extra root space for the venv (the base is ~3 GB, the venv ~1 GB)
LOCAL_ENV=${FRIDAY_LOCAL_CONFIG:-/srv/friday/state/local.env}   # house values (VM address, production ids); never in the repo
house() { sudo sh -c ". '$LOCAL_ENV'; printf %s \"\$$1\""; }
VM_IP=$(house FRIDAY_VM_IP); [ -n "$VM_IP" ] || { echo "FRIDAY_VM_IP missing from $LOCAL_ENV" >&2; exit 1; }
SERVE_URL=${FRIDAY_IMG_URL:-http://$VM_IP:8088}   # where the PC's Imager downloads it (see README)
# the base image, pinned: URL and the sha256 of the DECOMPRESSED .img (the official list publishes that one)
BASE_URL=https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.img.xz
BASE_SHA256=b8a393dc1c3701450b5fffbfe74368a98fb6eab7415ce456cc0cead886ea8780
BASE_SIZE=3078619136
export PATH="$PATH:/usr/sbin:/sbin"
SUDO=""; [ "$(id -u)" = 0 ] || SUDO="sudo"

missing=()
for c in curl xz losetup parted e2fsck resize2fs rsync sha256sum python3 chroot mount umount systemd-nspawn machinectl; do
  command -v "$c" >/dev/null || missing+=("$c")
done
[ -x /usr/bin/qemu-aarch64-static ] || missing+=("qemu-user-static")
[ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ] || missing+=("binfmt for aarch64 (binfmt-support)")
[ -d "$HERE/payload" ] || missing+=("payload/ (run stage.sh on the HA first)")
if [ "${1:-}" = "--check" ]; then
  [ ${#missing[@]} -eq 0 ] && echo "ready" || { printf 'missing: %s\n' "${missing[@]}"; exit 1; }
  exit 0
fi
[ ${#missing[@]} -eq 0 ] || { printf 'missing: %s\n' "${missing[@]}" >&2; exit 1; }

DATE=$(date +%Y%m%d)
NAME=friday-satellite-$DATE
T0=$(date +%s)
mkdir -p "$WORK"; $SUDO mkdir -p "$OUTDIR"; $SUDO chown "$(id -u):$(id -g)" "$OUTDIR"
BASE_XZ=$WORK/$(basename "$BASE_URL")
BASE_IMG=${BASE_XZ%.xz}
IMG=$WORK/$NAME.img
MNT=$WORK/mnt
LOOP=""

cleanup() {
  set +e
  for m in dev/pts dev proc sys boot/firmware ""; do $SUDO umount -l "$MNT/$m" 2>/dev/null; done
  [ -n "$LOOP" ] && $SUDO losetup -d "$LOOP" 2>/dev/null
}
trap cleanup EXIT

echo "== base image"
[ -f "$BASE_XZ" ] || curl -fL --retry 3 -o "$BASE_XZ" "$BASE_URL"
if [ ! -f "$BASE_IMG" ] || [ "$(stat -c %s "$BASE_IMG")" != "$BASE_SIZE" ]; then xz -dkf "$BASE_XZ"; fi
echo "$BASE_SHA256  $BASE_IMG" | sha256sum -c - >/dev/null || { echo "base image hash mismatch" >&2; exit 1; }

echo "== grow"
cp --sparse=always "$BASE_IMG" "$IMG"
truncate -s +"$GROW" "$IMG"
$SUDO parted -s "$IMG" resizepart 2 100%
LOOP=$($SUDO losetup -Pf --show "$IMG")
$SUDO e2fsck -fp "${LOOP}p2" >/dev/null || true
$SUDO resize2fs "${LOOP}p2" >/dev/null

echo "== mount and customise"
mkdir -p "$MNT"
$SUDO mount "${LOOP}p2" "$MNT"
$SUDO mount "${LOOP}p1" "$MNT/boot/firmware"
$SUDO cp /usr/bin/qemu-aarch64-static "$MNT/usr/bin/"
$SUDO mount --bind /dev "$MNT/dev"; $SUDO mount --bind /dev/pts "$MNT/dev/pts"
$SUDO mount -t proc proc "$MNT/proc"; $SUDO mount -t sysfs sys "$MNT/sys"
$SUDO cp /etc/resolv.conf "$MNT/etc/resolv.conf.build"
$SUDO mv -f "$MNT/etc/resolv.conf" "$MNT/etc/resolv.conf.orig" 2>/dev/null || true
$SUDO cp "$MNT/etc/resolv.conf.build" "$MNT/etc/resolv.conf"
echo "== vault files and house values into the payload"
FRIDAY_VAULT_PY="$HERE/vault.py" python3 "$HERE/vault_payload.py" "$HERE/payload" --manifest "$HERE/vault-manifest.json"
printf '{"endereco_friday": "https://%s:7443"}\n' "$VM_IP" > "$HERE/payload/house.json"
$SUDO rm -rf "$MNT/payload"; $SUDO cp -a "$HERE/payload" "$MNT/payload"
$SUDO install -m 0755 "$HERE/customize-chroot.sh" "$MNT/customize-chroot.sh"
$SUDO nice -n 10 chroot "$MNT" /customize-chroot.sh
$SUDO rm -f "$MNT/customize-chroot.sh" "$MNT/etc/resolv.conf" "$MNT/etc/resolv.conf.build" "$MNT/usr/bin/qemu-aarch64-static"
$SUDO mv -f "$MNT/etc/resolv.conf.orig" "$MNT/etc/resolv.conf" 2>/dev/null || true

echo "== static checks"
MNT="$MNT" SUDO="$SUDO" PRODUCTION_IDS="$(house FRIDAY_SATELLITE_IDS)" bash "$HERE/verify_image.sh"

echo "== close"
cleanup
LOOP=""
trap - EXIT
$SUDO e2fsck -fp "$IMG" >/dev/null 2>&1 || true

echo "== smoke boot (arm64 container on a copy)"
SUDO="$SUDO" bash "$HERE/smoke_boot.sh" "$IMG"

echo "== compress and manifest"
EX_SHA=$(sha256sum "$IMG" | cut -d' ' -f1)
EX_SIZE=$(stat -c %s "$IMG")
nice -n 10 xz -T0 -6 -f -k "$IMG"
mv -f "$IMG.xz" "$OUTDIR/$NAME.img.xz"
python3 "$HERE/make_manifest.py" "$OUTDIR/$NAME.img.xz" --url "$SERVE_URL/$NAME.img.xz" \
  --out "$OUTDIR/$NAME.json" --date "$(date +%F)" --extract-sha256 "$EX_SHA" --extract-size "$EX_SIZE"
( cd "$OUTDIR" && sha256sum "$NAME.img.xz" > "$NAME.img.xz.sha256" )
rm -f "$IMG"
echo "built $OUTDIR/$NAME.img.xz ($(du -h "$OUTDIR/$NAME.img.xz" | cut -f1)) in $(( $(date +%s) - T0 )) s"
