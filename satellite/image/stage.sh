#!/bin/bash
# HA side. Assembles the image payload from a VERSION OF THE REPO (program, agent, units, sync, status, files),
# refuses it if anything from the house slipped in, and copies the project to the VM. The files that cannot be public
# (vendor file, sounds, models, HA public key, Sendspin) are NOT here: build.sh fetches them from the vault on the VM.
#   satellite/image/stage.sh [GIT-REF]      (default HEAD)   then:  ssh <vm> 'cd ~/imgbuild/src && ./build.sh'
# Reads only; writes a temp payload and ~/imgbuild/src on the VM. Never prints a secret.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
REF=${1:-HEAD}
VM=${FRIDAY_IMG_VM:-$(cat /config/.friday/vm 2>/dev/null)}
[ -n "$VM" ] || { echo "set FRIDAY_IMG_VM=user@host (or /config/.friday/vm)" >&2; exit 2; }
REGISTRY=${FRIDAY_REGISTRY:-/share/friday/satellites.json}
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

# nothing from the house
python3 -I "$HERE/payload_check.py" "$OUT" --secrets-json "$REGISTRY"

# the project (from the same ref), the vault tool + manifest, and the payload go to the VM
ssh -o BatchMode=yes "$VM" 'mkdir -p ~/imgbuild/src'
cp "$SRC/scripts/vault.py" "$S/image/vault.py"; cp "$SRC/vault/manifest.json" "$S/image/vault-manifest.json"
rsync -a --delete -e "ssh -o BatchMode=yes" --exclude='tests' --exclude='__pycache__' "$S/image/" "$VM:imgbuild/src/"
rsync -a --delete -e "ssh -o BatchMode=yes" "$OUT/" "$VM:imgbuild/src/payload/"
echo "staged $VERSION on $VM:imgbuild/src (payload $(du -sh "$OUT" | cut -f1); vault files are added by build.sh)"
