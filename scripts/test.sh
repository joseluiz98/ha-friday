#!/usr/bin/env bash
# Runs the test suite on the VM (the HAOS box is too weak): rsync the repo,
# then pytest inside python:3.14-slim with a persistent pip cache volume.
# Usage: scripts/test.sh [pytest args...]
set -euo pipefail
cd "$(dirname "$0")/.."
VM="${FRIDAY_TEST_VM:-$(cat /config/.friday/vm 2>/dev/null)}"
DIR="${FRIDAY_TEST_DIR:-ha-friday}"   # one folder per worktree: parallel sessions must not rsync over each other
[ -n "$VM" ] || { echo "set FRIDAY_TEST_VM=user@host (or /config/.friday/vm)" >&2; exit 2; }
rsync -az --delete \
  --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude venv --exclude .venv \
  ./ "$VM:$DIR/"
ARGS=$(printf '%q ' "$@")
ssh -o BatchMode=yes "$VM" "sudo docker run --rm \
  -v \$HOME/$DIR:/w -v friday-pip-cache:/root/.cache/pip -w /w python:3.14-slim \
  sh -c 'pip install -q --root-user-action=ignore -r requirements_test.lock && pytest -q $ARGS'"
