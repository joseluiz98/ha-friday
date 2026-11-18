#!/usr/bin/env bash
# Every suite, including the ones that need the friday image or the production brain workspace (run on HAOS; the
# heavy parts run on the VM). A release is refused unless this exits 0.
set -uo pipefail
cd "$(dirname "$0")/.."
VM=${FRIDAY_TEST_VM:-$(cat /config/.friday/vm 2>/dev/null)}
ALL="${FRIDAY_TEST_DIR:-ha-friday}-all"
[ -n "$VM" ] || { echo "set FRIDAY_TEST_VM=user@host (or /config/.friday/vm)" >&2; exit 2; }
fail=0
# Every suite runs with a private TMPDIR, removed at the end. A suite that leaves anything there fails: leaked test
# folders filled the HAOS eMMC on 18/11/2026 (11.8 GB in /tmp of the SSH add-on, ~120 MB per satellite run).
TMPDIR=$(mktemp -d "${TMPDIR:-/tmp}/test-all.XXXXXX") || exit 2
export TMPDIR
trap 'rm -rf "$TMPDIR"' EXIT
step() {
  echo "== $1"; local name=$1; shift
  "$@" || { echo "FAILED: $*" >&2; fail=1; }
  local left; left=$(ls -A "$TMPDIR")
  if [ -n "$left" ]; then
    echo "FAILED: $name leaked $(echo "$left" | wc -l) temp entr(ies), e.g. $(echo "$left" | head -3 | tr '\n' ' ')" >&2
    fail=1; find "$TMPDIR" -mindepth 1 -delete
  fi
}
step "sweep (private list)" python3 scripts/sweep.py
step "integration + agents + tools (VM, python:3.14)" bash scripts/test.sh
step "satellite program" sh -c 'cd satellite/program && python3 -m unittest -q 2>&1 | tail -3 | grep -q "^OK"'
step "satellite image" sh -c 'cd satellite/image && python3 -m unittest -q tests.test_payload_check tests.test_make_manifest tests.test_vault_payload tests.test_build_download'
step "ota scripts" bash ota/test_self_update.sh
rsync -az --delete --exclude __pycache__ --exclude .git ./ "$VM:$ALL/"
step "voice gateway (friday image, production workspace)" ssh -o BatchMode=yes "$VM" \
  'sudo docker run --rm --entrypoint /opt/pc/bin/python -e FRIDAY_LOCAL_CONFIG=/w/local.example.env -v /srv/friday:/srv/friday -v $HOME/'"$ALL"'/voice_gateway:/w -w /w "$(sudo docker inspect --format {{.Config.Image}} friday)" -m unittest -q'
step "server tools (VM)" ssh -o BatchMode=yes "$VM" 'cd '"$ALL"'/server && export FRIDAY_LOCAL_CONFIG=$HOME/'"$ALL"'/voice_gateway/local.example.env && for d in status cataloger cost browser; do (cd $d && python3 -m unittest -q) || exit 1; done && for t in essentials/test_*.sh image/test_*.sh; do bash "$t" >/dev/null || { echo "$t"; exit 1; }; done'
[ "$fail" = 0 ] && echo "ALL GREEN" || { echo "SOME SUITE FAILED" >&2; exit 1; }
