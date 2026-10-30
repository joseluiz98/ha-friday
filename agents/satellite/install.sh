#!/usr/bin/env bash
# Run on the Pi as jose, from the repo's agents/ dir: sudo bash satellite/install.sh
# Does NOT touch friday-satelite. Installs `zeroconf` in the program venv only if missing (heavy: run alone).
set -euo pipefail
cd "$(dirname "$0")/.."
D=/opt/friday-satellite-agent
VENV=/home/jose/oww
if ! "$VENV/bin/python" -c "import zeroconf" 2>/dev/null; then
  nice -n 10 "$VENV/bin/pip" install -q zeroconf
fi
mkdir -p "$D"
rm -rf "$D/friday_satellite_agent" "$D/friday_agent_core"
cp -r satellite/friday_satellite_agent common/friday_agent_core "$D/"
find "$D" -name __pycache__ -prune -exec rm -rf {} +
cp satellite/friday-satellite-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable friday-satellite-agent
# sudoers: validate before installing so a bad rule can never lock sudo out
TMP="$(mktemp)"
cp satellite/sudoers.friday-satellite-agent "$TMP"
chmod 0440 "$TMP"
visudo -cf "$TMP"
install -m 0440 -o root -g root "$TMP" /etc/sudoers.d/friday-satellite-agent
rm -f "$TMP"
systemctl restart friday-satellite-agent
