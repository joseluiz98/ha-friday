#!/usr/bin/env python3
"""Asks for a restart only when nothing is in use. Prints one line and exits 0 when the restart is deferred.

  guarded_restart.py gateway   POST FRIDAY_RESTART_URL (status port, Bearer token file); 202 = restarting, 409 = deferred
  guarded_restart.py program   GET :8700/status of the satellite program; idle = estado "parada", no timers, no alarm;
                               idle -> `systemctl restart friday-satelite` (sudo), busy -> deferred
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


def _token(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def gateway() -> int:
    url = os.environ.get("FRIDAY_RESTART_URL", "http://127.0.0.1:8800/v1/restart")
    tok = _token(os.environ.get("FRIDAY_STATUS_TOKEN_FILE", "/srv/friday/secrets/status_token"))
    req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    if code == 202:
        print("restart requested")
        return 0
    if code == 409:
        print("restart deferred: conversation open")
        return 0
    print(f"restart failed: HTTP {code}", file=sys.stderr)
    return 1


def program() -> int:
    cfg_path = os.environ.get("FRIDAY_PROGRAM_CONFIG", "/etc/friday-satelite/config.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    base = os.environ.get("FRIDAY_PROGRAM_URL", f"http://127.0.0.1:{cfg.get('porta_local', 8700)}")
    req = urllib.request.Request(base + "/status", headers={"Authorization": f"Bearer {cfg['senha_porta_local']}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        st = json.load(r)
    if st.get("estado") != "parada" or st.get("timers") or st.get("alarme"):
        print("restart deferred: satellite busy")
        return 0
    cmd = os.environ.get("FRIDAY_PROGRAM_RESTART_CMD", "sudo systemctl restart friday-satelite").split()
    subprocess.run(cmd, check=True)
    print("restart requested")
    return 0


if __name__ == "__main__":
    sys.exit({"gateway": gateway, "program": program}[sys.argv[1]]())
