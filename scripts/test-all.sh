#!/usr/bin/env bash
# Every suite, including the ones that need the friday image or the production brain workspace (run on HAOS; the
# heavy parts run on the VM). A release is refused unless this exits 0.
set -uo pipefail
cd "$(dirname "$0")/.."
VM=${FRIDAY_TEST_VM:-$(cat /config/.friday/vm 2>/dev/null)}
[ -n "$VM" ] || { echo "set FRIDAY_TEST_VM=user@host (or /config/.friday/vm)" >&2; exit 2; }
fail=0
step() { echo "== $1"; shift; "$@" || { echo "FAILED: $*" >&2; fail=1; }; }
step "sweep (private list)" python3 scripts/sweep.py
step "integration + agents + tools (VM, python:3.14)" bash scripts/test.sh
step "satellite program" sh -c 'cd satellite/program && python3 -m unittest -q 2>&1 | tail -3 | grep -q "^OK"'
step "satellite image" sh -c 'cd satellite/image && python3 -m unittest -q tests.test_payload_check tests.test_make_manifest tests.test_vault_payload'
rsync -az --delete --exclude __pycache__ --exclude .git ./ "$VM:ha-friday-all/"
step "voice gateway (friday image, production workspace)" ssh -o BatchMode=yes "$VM" \
  'sudo docker run --rm --entrypoint /opt/pc/bin/python -e FRIDAY_LOCAL_CONFIG=/w/local.example.env -v /srv/friday:/srv/friday -v $HOME/ha-friday-all/voice_gateway:/w -w /w friday:2026.9.9 -m unittest -q'
step "server tools (VM)" ssh -o BatchMode=yes "$VM" 'cd ha-friday-all/server && export FRIDAY_LOCAL_CONFIG=$HOME/ha-friday-all/voice_gateway/local.example.env && for d in status cataloger cost; do (cd $d && python3 -m unittest -q) || exit 1; done && for t in essentials/test_*.sh image/test_*.sh; do bash "$t" >/dev/null || { echo "$t"; exit 1; }; done'
[ "$fail" = 0 ] && echo "ALL GREEN" || { echo "SOME SUITE FAILED" >&2; exit 1; }
