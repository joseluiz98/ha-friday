#!/usr/bin/env bash
# Replaces the VM agent from outside it (spec 2026-11-10 §13): the agent starts this detached with
#   systemd-run --unit friday-ota-self --collect bash ota/self_update.sh <new_tree> <new_line> <old_tree> <old_line> \
#     <result_file> <timeout_s>
# It installs the new tree (which restarts friday-server-agent), waits until the agent answers on :8801 (the OTA route
# answers 401 without a token), and reinstalls the old tree when it does not. The outcome goes to <result_file> as JSON
# for whichever agent comes up; nothing here prints a secret.
set -uo pipefail
NEW=$1 NEW_LINE=$2 OLD=$3 OLD_LINE=$4 RESULT=$5 TIMEOUT=${6:-180}
URL=${FRIDAY_SELF_URL:-http://127.0.0.1:8801/v1/ota/offer}
TAG=${NEW_LINE%% *}

install_tree() { (cd "$1" && FRIDAY_VERSION_LINE="$2" bash agents/server/install.sh); }

answers() {
  python3 - "$URL" <<'PY'
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=5)
    sys.exit(1)  # 200 without a token is not our agent
except urllib.error.HTTPError as e:
    sys.exit(0 if e.code == 401 else 1)
except Exception:
    sys.exit(1)
PY
}

up() { systemctl is-active -q friday-server-agent && answers; }

# up, and still up STABLE seconds later (an agent that crashes right after its first answer is not healthy)
healthy() {
  local end=$((SECONDS + TIMEOUT))
  while [ "$SECONDS" -lt "$end" ]; do
    if up; then sleep "${FRIDAY_SELF_STABLE:-20}"; up && return 0; fi
    sleep 5
  done
  return 1
}

write() {
  printf '{"tag": "%s", "result": "%s", "stage": "%s", "at": %s}\n' "$TAG" "$1" "$2" "$(date +%s)" > "$RESULT.tmp" \
    && mv -f "$RESULT.tmp" "$RESULT"
}

if install_tree "$NEW" "$NEW_LINE"; then
  if healthy; then write installed ""; exit 0; fi
  stage=health
else
  stage=install
fi
install_tree "$OLD" "$OLD_LINE" || true
write rolled_back "$stage"
exit 1
