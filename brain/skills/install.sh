#!/usr/bin/env bash
# Installs OUR brain skills into the OpenClaw workspace, rendering {DONO}, {OWNER_NOTIFY} and {SAT_OFFICE} from the
# local config. Skills the brain created itself (Skill Workshop) are never touched. No restart: the brain reads skills
# when it opens them.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WS=${FRIDAY_WORKSPACE:-/srv/friday/home/.openclaw/workspace/friday}
LOCAL=${FRIDAY_LOCAL_CONFIG:-/srv/friday/state/local.env}
LINE=${FRIDAY_VERSION_LINE:?FRIDAY_VERSION_LINE missing (use scripts/deploy.sh)}
python3 - "$HERE" "$WS" "$LOCAL" "$LINE" <<'PY'
import os, shutil, sys
here, ws, local, line = sys.argv[1:5]
env = {}
with open(local, encoding="utf-8") as f:
    for raw in f:
        s = raw.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("="); env[k.strip()] = v.strip().strip('"').strip("'")
keys = {"{DONO}": "FRIDAY_OWNER_NAME", "{OWNER_NOTIFY}": "FRIDAY_OWNER_NOTIFY", "{SAT_OFFICE}": "FRIDAY_SAT_OFFICE"}
missing = [k for k in keys.values() if not env.get(k)]
if missing:
    sys.exit(f"missing from {local}: {', '.join(missing)}")
def render(src, dst):
    with open(src, encoding="utf-8") as f:
        t = f.read()
    for ph, k in keys.items():
        t = t.replace(ph, env[k])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst + ".new", "w", encoding="utf-8") as f:
        f.write(t)
    os.chmod(dst + ".new", 0o755 if os.access(src, os.X_OK) else 0o644)
    os.replace(dst + ".new", dst)
ours = sorted(d for d in os.listdir(here) if os.path.isdir(os.path.join(here, d)) and d != "__pycache__")
for skill in ours:
    stage = os.path.join(ws, "skills", f".{skill}.new")
    shutil.rmtree(stage, ignore_errors=True)
    for root, _d, files in os.walk(os.path.join(here, skill)):
        for name in files:
            src = os.path.join(root, name)
            render(src, os.path.join(stage, os.path.relpath(src, os.path.join(here, skill))))
    final = os.path.join(ws, "skills", skill)
    old = final + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(final):
        os.replace(final, old)
    os.replace(stage, final)
    shutil.rmtree(old, ignore_errors=True)
    with open(os.path.join(final, "FRIDAY_VERSION"), "w") as f:
        f.write(line + "\n")
bin_src = os.path.join(os.path.dirname(here), "bin")
for name in sorted(os.listdir(bin_src)) if os.path.isdir(bin_src) else []:
    render(os.path.join(bin_src, name), os.path.join(ws, "bin", name))
print(f"brain skills {line} installed: {', '.join(ours)}")
PY
