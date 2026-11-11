#!/bin/bash
# Assembles the image payload from a VERSION OF THE REPO (program, agent, units, sync, status, files), refuses it if
# anything from the house slipped in, and puts the project where build.sh runs. The files that cannot be public (vendor
# file, sounds, models, HA public key, Sendspin) are NOT here: build.sh fetches them from the vault on the VM.
#   HA side:  satellite/image/stage.sh [GIT-REF]      (default HEAD)   then:  ssh <vm> 'cd ~/imgbuild/src && ./build.sh'
#   VM side (the OTA, plan 2026-11-10 Task 12):
#     stage.sh --repo /srv/friday/ota/repo --ref <sha> --local --package <packages/<tag>/satellite.tar.gz>
#   --package puts the release's OTA package in the payload: the image is born on the versioned layout
#   (/opt/friday/releases/<tag>, /opt/friday/current). --local copies to $FRIDAY_IMG_SRC (default ~/imgbuild/src).
# Reads only; writes a temp payload and the build folder. Never prints a secret.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
REF=HEAD; LOCAL=0; PACKAGE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO=$2; shift 2 ;;
    --ref) REF=$2; shift 2 ;;
    --local) LOCAL=1; shift ;;
    --package) PACKAGE=$2; shift 2 ;;
    *) REF=$1; shift ;;
  esac
done
if [ "$LOCAL" = 1 ]; then
  REGISTRY=${FRIDAY_REGISTRY:-/mnt/haos/share/friday/satellites.json}
else
  VM=${FRIDAY_IMG_VM:-$(cat /config/.friday/vm 2>/dev/null)}
  [ -n "$VM" ] || { echo "set FRIDAY_IMG_VM=user@host (or /config/.friday/vm)" >&2; exit 2; }
  REGISTRY=${FRIDAY_REGISTRY:-/share/friday/satellites.json}
fi
SHA=$(git -C "$REPO" rev-parse --verify "$REF^{commit}")
VERSION="$(git -C "$REPO" describe --tags --always "$SHA") $SHA"
SRC=$(mktemp -d); OUT=$(mktemp -d)
trap 'rm -rf "$SRC" "$OUT"' EXIT
git -C "$REPO" archive "$SHA" satellite agents scripts/vault.py vault | tar -x -C "$SRC"
S="$SRC/satellite"; A="$SRC/agents"
mkdir -p "$OUT"/{programa,agent,units,sync,sendspin}
# program: only the .py files and vendor/SOURCE.md (no tests, no caches); vendor/xvf_host.py comes from the vault
rsync -a --exclude='test_*' --exclude='__pycache__' --include='*.py' --include='vendor/' --include='vendor/**' \
  --exclude='*' "$S/program/" "$OUT/programa/"
rsync -a --exclude='__pycache__' "$A/satellite/friday_satellite_agent" "$A/common/friday_agent_core" "$OUT/agent/"
cp "$A/satellite/friday-wifi-apply" "$A/satellite/friday-agent-apply" "$A/satellite/sudoers.friday-satellite-agent" "$OUT/agent/"
cp "$S/program/friday-firstboot.service" "$S/program/friday-satelite.service" "$OUT/units/"
cp "$S/program/friday-netwatch.service" "$S/program/friday-netwatch.timer" "$OUT/units/"
cp "$A/satellite/friday-satellite-agent.service" "$OUT/units/"
cp "$S/sync/friday-sync-recordings.service" "$S/sync/friday-sync-recordings.timer" "$OUT/units/"
cp "$S/status/satelite-status.service" "$S/status/satelite-status.timer" "$S/status/satelite-status.sh" "$OUT/units/"
cp "$S/sync/sync_recordings.sh" "$OUT/sync/"
cp "$S/asound.conf" "$OUT/asound.conf"
cp "$S/image/requirements.lock" "$OUT/"
cp -a "$S/image/files" "$OUT/"
cp "$S/sendspin/sendspin-cli.conf" "$OUT/sendspin/"
echo "$VERSION" > "$OUT/FRIDAY_VERSION"
if [ -n "$PACKAGE" ]; then cp "$PACKAGE" "$OUT/release.tar.gz"; fi

# nothing from the house
python3 -I "$HERE/payload_check.py" "$OUT" --secrets-json "$REGISTRY"

# the project (from the same ref), the vault tool + manifest, and the payload go where build.sh runs
cp "$SRC/scripts/vault.py" "$S/image/vault.py"; cp "$SRC/vault/manifest.json" "$S/image/vault-manifest.json"
if [ "$LOCAL" = 1 ]; then
  DEST=${FRIDAY_IMG_SRC:-$HOME/imgbuild/src}
  mkdir -p "$DEST"
  rsync -a --delete --exclude='tests' --exclude='__pycache__' --exclude='payload' "$S/image/" "$DEST/"
  rsync -a --delete "$OUT/" "$DEST/payload/"
  echo "staged $VERSION in $DEST (payload $(du -sh "$OUT" | cut -f1); vault files are added by build.sh)"
else
  ssh -o BatchMode=yes "$VM" 'mkdir -p ~/imgbuild/src'
  rsync -a --delete -e "ssh -o BatchMode=yes" --exclude='tests' --exclude='__pycache__' "$S/image/" "$VM:imgbuild/src/"
  rsync -a --delete -e "ssh -o BatchMode=yes" "$OUT/" "$VM:imgbuild/src/payload/"
  echo "staged $VERSION on $VM:imgbuild/src (payload $(du -sh "$OUT" | cut -f1); vault files are added by build.sh)"
fi
