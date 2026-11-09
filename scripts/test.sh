#!/usr/bin/env bash
# Runs the test suite on the VM (the HAOS box is too weak): rsync the repo,
# then pytest inside python:3.14-slim with a persistent pip cache volume.
# Usage: scripts/test.sh [pytest args...]
set -euo pipefail
cd "$(dirname "$0")/.."
VM="${FRIDAY_TEST_VM:-$(cat /config/.friday/vm 2>/dev/null)}"
[ -n "$VM" ] || { echo "set FRIDAY_TEST_VM=user@host (or /config/.friday/vm)" >&2; exit 2; }
rsync -az --delete \
  --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude venv --exclude .venv \
  ./ "$VM:ha-friday/"
ARGS=$(printf '%q ' "$@")
ssh -o BatchMode=yes "$VM" "sudo docker run --rm \
  -v \$HOME/ha-friday:/w -v friday-pip-cache:/root/.cache/pip -w /w python:3.14-slim \
  sh -c 'pip install -q --root-user-action=ignore -r requirements_test.lock && pytest -q $ARGS'"
