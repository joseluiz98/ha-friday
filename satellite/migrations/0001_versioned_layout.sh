#!/usr/bin/env bash
# 0001: a satellite of the old layout (program in ~/satelite, agent in /opt/friday-satellite-agent, venv ~/oww) gets
# the versioned one (spec 2026-11-10 §14): its running code becomes the release v0.0.0-legacy and `current` points to
# it, so the activation that runs this migration has a previous release to fall back to. Nothing is moved: the code is
# copied, the legacy venv is a link to ~/oww (moving a venv breaks its shebangs; a later migration removes ~/oww).
# Data stays where it is (~/satelite/sons, ~/satelite/estado, ~/modelos, ~/vozes, ~/gravacoes, /etc/friday-satelite).
# Idempotent: a satellite that already has /opt/friday/current is left alone. Runs as root (friday-ota-apply).
set -euo pipefail
R=${FRIDAY_OTA_ROOT:-}
OPT="$R/opt/friday"
[ -e "$OPT/current" ] && { echo "0001: already versioned"; exit 0; }
HOMEDIR="$R/home/jose"
OLD_PROGRAM="$HOMEDIR/satelite"
OLD_AGENT="$R/opt/friday-satellite-agent"
[ -f "$OLD_PROGRAM/friday_satelite.py" ] || { echo "0001: no program in $OLD_PROGRAM" >&2; exit 1; }
[ -d "$OLD_AGENT/friday_satellite_agent" ] || { echo "0001: no agent in $OLD_AGENT" >&2; exit 1; }
LEG="$OPT/releases/v0.0.0-legacy"
rm -rf "$LEG.new"
mkdir -p "$LEG.new/program" "$LEG.new/agent"
for f in "$OLD_PROGRAM"/*.py "$OLD_PROGRAM"/FRIDAY_VERSION "$OLD_PROGRAM"/FRIDAY_VERSION.pending; do
  [ -f "$f" ] && cp -p "$f" "$LEG.new/program/"
done
[ -d "$OLD_PROGRAM/vendor" ] && cp -a "$OLD_PROGRAM/vendor" "$LEG.new/program/"
# the release folder is root's: the program cannot promote a .pending at its next start, so it happens here
[ -f "$LEG.new/program/FRIDAY_VERSION.pending" ] && mv -f "$LEG.new/program/FRIDAY_VERSION.pending" "$LEG.new/program/FRIDAY_VERSION"
cp -a "$OLD_AGENT/friday_satellite_agent" "$OLD_AGENT/friday_agent_core" "$LEG.new/agent/"
ln -s "/home/jose/oww" "$LEG.new/venv"
touch "$LEG.new/.complete"
rm -rf "$LEG"
mv "$LEG.new" "$LEG"
ln -s releases/v0.0.0-legacy "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"
echo "0001: legacy release recorded, current -> v0.0.0-legacy"
