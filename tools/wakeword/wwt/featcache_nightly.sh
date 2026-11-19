#!/bin/bash
# Cache do ambiente para a prova das palavras (08/11/2026, pedido do dono; todos os satélites desde 18/11). Roda NA VM
# como root, acordado pelo friday-wwt-featcache.path quando um satélite grava a marca `.sync_ok` no fim de um envio
# noturno inteiro e sem erro (friday-sync-recordings na Pi, 03:30), e pelo friday-wwt-featcache.timer às 07:00 (pega o
# satélite pareado depois da última instalação). Units: tools/wakeword/wwt/install.sh.
# Para cada incoming/<sat>/:
# 1) retenção (o dono, 08/11): áudio de ambiente na VM por 30 dias;
# 2) todo dia de gravação anterior a hoje sem $C/amb<AAAAMMDD>_<sat>.json passa uma vez pelo extrator
#    (featcache.py cache, modo ambiente; ~80 min por dia, um por vez, nice 19);
# 3) o cache acompanha o áudio do mesmo satélite: dia sem áudio sai do cache;
# 4) satélite sem falha (inclusive sem gravacoes/ambiente, antes do 1º envio) -> $STATE/wwt-featcache.json
#    {"<sat>": "<ISO agora>"} (merge atômico; o agente do servidor publica como atributo `satellites`).
# Só com todos OK: touch $STATE/wwt-featcache.ok. Falha avisa no Telegram e para.
set -uo pipefail
ENVF=${FRIDAY_LOCAL_CONFIG:-/srv/friday/state/local.env}
[ -f "$ENVF" ] && . "$ENVF"   # FRIDAY_TELEGRAM_CHAT (house value, never in the repo)
IN=${FRIDAY_WAKEWORD_INCOMING:-/srv/wakeword-data/incoming}
C=${FRIDAY_WWT_CACHE:-/srv/wwt-featcache}
STATE=${FRIDAY_WWT_STATE:-/srv/friday/state}
CODE=${FRIDAY_WWT_CODE:-/opt/friday/wwt/training}; TG=${FRIDAY_TELEGRAM_CHAT:?set FRIDAY_TELEGRAM_CHAT (local.env)}; HOJE=$(date +%Y%m%d)
RETER_DIAS=30; CORTE=$(date -d "-$RETER_DIAS days" +%Y%m%d)
falha() { oc message send --channel telegram --target "$TG" -m "Cache noturno das palavras falhou ($1). Log: journalctl -u friday-wwt-featcache" >/dev/null 2>&1; exit 1; }
registra() {  # merge {"<sat>": "<ISO now>"} into the registry, atomically (temp file + rename in the same folder)
  python3 - "$STATE/wwt-featcache.json" "$1" <<'EOF' || falha "registro de $1 em $STATE/wwt-featcache.json"
import datetime, json, os, sys
path, sat = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as f:
        reg = json.load(f)
except (OSError, ValueError):
    reg = {}
reg = reg if isinstance(reg, dict) else {}
reg[sat] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
tmp = f"{path}.tmp{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(reg, f, sort_keys=True)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
EOF
}
[ -f "$CODE/featcache.py" ] || falha "featcache.py ausente em $CODE (deploy wwt)"
mkdir -p "$C" "$STATE"; chown friday: "$C" 2>/dev/null || true
total=0
for S in "$IN"/*/; do
  [ -d "$S" ] || continue
  S=${S%/}; sat=$(basename "$S"); A="$S/gravacoes/ambiente"
  echo "== satélite $sat; marca: $(cat "$S/.sync_ok" 2>/dev/null)"
  apagados=0; feitos=0
  if [ -d "$A" ]; then
    # retenção do áudio primeiro (não gasta extrator com dia que vai sair)
    for f in "$A"/ambiente_????????_*.wav; do
      [ -e "$f" ] || continue
      d=$(basename "$f" | cut -c10-17)
      [ "$d" -lt "$CORTE" ] && { rm -f "$f"; apagados=$((apagados + 1)); }
    done
    for d in $(ls "$A" | sed -nE 's/^ambiente_([0-9]{8})_.*\.wav$/\1/p' | sort -u); do
      [ "$d" -lt "$HOJE" ] || continue
      n="amb${d}_$sat"
      [ -s "$C/$n.json" ] && continue
      echo "== $n $(date +%T)"
      nice -n 19 docker run --rm -v "$C":/fc -v "$A":/a:ro -v "$CODE":/code:ro --entrypoint sh wakeword-trainer:1 \
        -c "python /code/featcache.py cache /fc $n ambiente /a/ambiente_${d}_*.wav" > "$C/$n.log" 2>&1 \
        || { rm -f "$C/$n".*.npy "$C/$n.json"; falha "satélite $sat, dia $d, ver $C/$n.log"; }
      feitos=$((feitos + 1))
    done
  fi
  # o cache acompanha o áudio do mesmo satélite: dia sem áudio sai do cache
  for j in "$C"/amb????????_"$sat".json; do
    [ -e "$j" ] || continue
    d=$(basename "$j" .json); d=${d#amb}; d=${d%%_*}
    ls "$A"/ambiente_"$d"_*.wav >/dev/null 2>&1 || { rm -f "$C/amb${d}_$sat".*; echo "removido do cache: amb${d}_$sat (sem áudio)"; }
  done
  registra "$sat"
  total=$((total + feitos))
  echo "satélite $sat: dias novos no cache: $feitos; wavs apagados (> $RETER_DIAS dias): $apagados"
done
echo "dias novos no cache: $total; cache: $(du -sh "$C" | cut -f1); disco livre: $(df -h / | awk 'NR==2{print $4}')"
touch "$STATE/wwt-featcache.ok"
