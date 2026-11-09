#!/usr/bin/env bash
# Installs custom_components/friday of a git ref into Home Assistant as a real directory (no symlink) and records
# the ref in FRIDAY_VERSION. Never restarts HA. Usage: scripts/install-integration.sh <git-ref> [target]
set -euo pipefail
cd "$(dirname "$0")/.."
REF=${1:?usage: install-integration.sh <git-ref> [target]}
DEST=${2:-/config/custom_components/friday}
SHA=$(git rev-parse --verify "$REF^{commit}")
DESC=$(git describe --tags --always "$SHA")
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git archive "$SHA" custom_components/friday | tar -C "$TMP" -x
echo "$DESC $SHA" > "$TMP/custom_components/friday/FRIDAY_VERSION"
if [ -L "$DEST" ]; then rm "$DEST"; fi
rm -rf "$DEST.new" && mv "$TMP/custom_components/friday" "$DEST.new"
if [ -d "$DEST" ]; then rm -rf "$DEST.old" && mv "$DEST" "$DEST.old"; fi
mv "$DEST.new" "$DEST" && rm -rf "$DEST.old"
echo "installed $DESC ($SHA) into $DEST"
