#!/usr/bin/env bash
# 0006 (18/11, owner): satellites ship with both wake words active. A satellite with no extra word yet gets
# "Sexta-feira" active (threshold 0.5, patience 2, no verifier until voice enrollment), as on the MK1 and in new
# images. Extras someone already set are never touched; without the model file nothing changes. Keeps the file's mode
# and owner. Idempotent. Runs as root (friday-ota-apply).
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
CFG="$R/etc/friday-satelite/config.json" MODEL="$R/home/jose/modelos/sexta_feira.onnx" python3 - <<'EOF'
import json, os
cfg, model = os.environ["CFG"], os.environ["MODEL"]
SEXTA = {"nome": "sexta_feira", "arquivo": "/home/jose/modelos/sexta_feira.onnx", "limiar": 0.5, "modo": "ativa",
         "paciencia": 2}
try:
    with open(cfg) as f:
        data = json.load(f)
except (OSError, ValueError):
    print("0006: no satellite config, nothing to do"); raise SystemExit(0)
if data.get("palavras_extras"):
    print("0006: extra wake words already set"); raise SystemExit(0)
if not os.path.isfile(model):
    print("0006: no sexta_feira model, nothing to do"); raise SystemExit(0)
data["palavras_extras"] = [SEXTA]
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
print("0006: sexta_feira active")
EOF
