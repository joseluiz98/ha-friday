#!/usr/bin/env bash
# 0003 (17/11): the factory tones changed to the ElevenLabs set the owner chose on 20/10 (until now only the first
# satellite had them, set by hand). A satellite still on an old factory tone moves to the new one; a tone picked by
# hand, or one whose new file is not in ~/satelite/sons, is kept. Keeps the file's mode and owner. Idempotent.
# Runs as root (friday-ota-apply).
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
CFG="$R/etc/friday-satelite/config.json" SONS="$R/home/jose/satelite/sons" python3 - <<'EOF'
import json, os
cfg, sons = os.environ["CFG"], os.environ["SONS"]
OLD_TO_NEW = {"inicio": ("inicio_a.wav", "inicio_elevenlabs.wav"), "fim": ("fim_a.wav", "fim_elevenlabs_20261020.wav"),
              "erro": ("erro_a.wav", "erro_b.wav"), "alarme": ("alarme_a.wav", "alarme_elevenlabs.wav"),
              "lembrete": ("lembrete_a.wav", "lembrete_elevenlabs.wav")}
try:
    with open(cfg) as f:
        data = json.load(f)
except (OSError, ValueError):
    print("0003: no satellite config, nothing to do"); raise SystemExit(0)
tones = data.get("sons") or {}
changed = [k for k, (old, new) in OLD_TO_NEW.items()
           if tones.get(k) == old and os.path.isfile(os.path.join(sons, new))]
if not changed:
    print("0003: tones already set"); raise SystemExit(0)
for k in changed:
    tones[k] = OLD_TO_NEW[k][1]
data["sons"] = tones
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
print("0003: ElevenLabs tones for " + ", ".join(changed))
EOF
