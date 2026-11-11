#!/usr/bin/env bash
# Tests ota/self_update.sh with stub trees, a stub systemctl and a local HTTP server that answers like the agent.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'kill $SRV 2>/dev/null || true; rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/new/agents/server" "$T/old/agents/server"
printf '#!/bin/sh\nexit 0\n' > "$T/bin/systemctl"; chmod +x "$T/bin/systemctl"
for v in new old; do
  printf '#!/bin/sh\necho "$FRIDAY_VERSION_LINE" > %s/installed\n' "$T" > "$T/$v/agents/server/install.sh"
done
export PATH="$T/bin:$PATH"

# 1) the agent never answers -> the old tree goes back, result rolled_back/health
FRIDAY_SELF_URL=http://127.0.0.1:9/x bash "$HERE/self_update.sh" "$T/new" "v0.3.0 b" "$T/old" "v0.2.0 a" "$T/r.json" 1 \
  && { echo "FAIL: exit 0 on an unhealthy agent"; exit 1; } || true
grep -q '"result": "rolled_back", "stage": "health"' "$T/r.json" || { echo "FAIL: $(cat "$T/r.json")"; exit 1; }
[ "$(cat "$T/installed")" = "v0.2.0 a" ] || { echo "FAIL: old tree not reinstalled"; exit 1; }

# 2) the agent answers 401 -> installed, the new tree stays
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
python3 - "$PORT" <<'PY' &
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401); self.end_headers()
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
SRV=$!
sleep 0.5
FRIDAY_SELF_URL=http://127.0.0.1:$PORT/v1/ota/offer bash "$HERE/self_update.sh" "$T/new" "v0.3.0 b" "$T/old" "v0.2.0 a" \
  "$T/r.json" 10
grep -q '"tag": "v0.3.0", "result": "installed"' "$T/r.json" || { echo "FAIL: $(cat "$T/r.json")"; exit 1; }
[ "$(cat "$T/installed")" = "v0.3.0 b" ] || { echo "FAIL: new tree not kept"; exit 1; }
echo "self_update: ok"
