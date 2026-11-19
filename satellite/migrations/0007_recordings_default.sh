#!/usr/bin/env bash
# 0007 (18/11, owner): satellites record activations and ambient by default. A satellite where neither was adopted
# (both keys off or absent) gets gravar_ativacoes true and gravar_ambiente_janela "08:00-22:00", as in new images.
# Any partial adoption (one on, or a custom window) is left untouched, byte for byte. Keeps the file's mode and owner.
# Idempotent. Runs as root (friday-ota-apply).
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
CFG="$R/etc/friday-satelite/config.json" python3 - <<'EOF'
import json, os
cfg = os.environ["CFG"]
try:
    with open(cfg) as f:
        data = json.load(f)
except (OSError, ValueError):
    print("0007: no satellite config, nothing to do"); raise SystemExit(0)
if data.get("gravar_ativacoes") or data.get("gravar_ambiente_janela"):
    print("0007: recordings already adopted, nothing to do"); raise SystemExit(0)
data["gravar_ativacoes"] = True
data["gravar_ambiente_janela"] = "08:00-22:00"
st = os.stat(cfg)
tmp = cfg + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)
os.chmod(tmp, st.st_mode & 0o777)
try:
    os.chown(tmp, st.st_uid, st.st_gid)
except PermissionError:
    pass
os.replace(tmp, cfg)
print("0007: recordings on")
EOF
