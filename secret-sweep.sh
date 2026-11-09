#!/usr/bin/env bash
# Fails (exit 1) on secrets or house data. Thin wrapper kept for old callers; the logic is scripts/sweep.py.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 scripts/sweep.py "$@"
