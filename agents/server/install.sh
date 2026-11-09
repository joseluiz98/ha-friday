#!/usr/bin/env bash
# Run on the VM as root, from the repo's agents/ parent (e.g. ~/ha-friday): sudo bash agents/server/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
D=/opt/friday/agent
mkdir -p "$D" /srv/friday/secrets
python3 -c "import ensurepip" 2>/dev/null || apt-get install -y -qq python3-venv
[ -x "$D/venv/bin/pip" ] || python3 -m venv --clear "$D/venv"
"$D/venv/bin/pip" install -q -r server/requirements.lock
rm -rf "$D/friday_server_agent" "$D/friday_agent_core"
cp -r server/friday_server_agent common/friday_agent_core "$D/"
find "$D" -name __pycache__ -prune -exec rm -rf {} +
# the vault tool and manifest (the satellite update bundle reads them) and the installed version
cp ../scripts/vault.py "$D/vault.py"; cp ../vault/manifest.json "$D/vault-manifest.json"
echo "${FRIDAY_VERSION_LINE:?FRIDAY_VERSION_LINE missing (use scripts/deploy.sh)}" > "$D/friday_agent_core/FRIDAY_VERSION"
T=/srv/friday/secrets/agent_token
if [ ! -s "$T" ]; then
  umask 077
  "$D/venv/bin/python" -c "import secrets;print(secrets.token_urlsafe(32))" > "$T"
fi
chmod 600 "$T"
cp server/friday-server-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable friday-server-agent
systemctl restart friday-server-agent
