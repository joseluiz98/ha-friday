#!/bin/bash
# Gera "sexta-feira" com XTTS-v2 no Kaggle (27/10/2026). Uso: run_xtts_gen.sh <saida> <marca_do_dataset> [takes]
# ANTES do push confere que o Kaggle serve o marca.json com <marca> (regra do o dono); o notebook confere de novo na
# célula 1, e no fim o manifesto tem de trazer o RUN_ID desta subida e a mesma marca.
set -uo pipefail
OUT=${1:?saida}; MARCA=${2:?marca}; TAKES=${3:-2}; HERE=$(cd "$(dirname "$0")" && pwd)
VM="$(cat /config/.friday/vm)"; KID=joseluiz98/xtts-sexta-gen; DID=joseluiz98/ptbr-voice-refs
T=/tmp/xtts-gen-$$; L=/tmp/xtts-gen-local-$$; RUN_ID=xtts-$(date +%Y%m%d%H%M%S)
K() { ssh "$VM" "sudo docker run --rm -e KAGGLE_API_TOKEN=\"\$(sudo cat /srv/friday/state/kaggle_api.token)\" -v $T:/w -w /w friday-kaggle:1 $*"; }
trap 'ssh "$VM" "sudo rm -rf $T" >/dev/null 2>&1; rm -rf "$L"' EXIT
ssh "$VM" "mkdir -p $T/chk"
K datasets download "$DID" -f marca.json -p /w/chk --force >/dev/null 2>&1
SERV=$(ssh "$VM" "sudo cat $T/chk/marca.json 2>/dev/null" | python3 -I -c 'import json,sys
try: print(json.load(sys.stdin)["marca"])
except Exception: print("")')
[ "$SERV" = "$MARCA" ] || { echo "DATASET ERRADO: o Kaggle serve '${SERV:-nada}', esperado '$MARCA'. Nada foi executado." >&2; exit 1; }
echo "   dataset conferido antes do push: $SERV"
python3 -I "$HERE/build_xtts_gen.py" "$L" "$MARCA" "$RUN_ID" "$TAKES" >/dev/null || exit 2
tar cf - -C "$L" . | ssh "$VM" "tar xf - -C $T"
K kernels push -p /w --accelerator NvidiaTeslaT4 || exit 1
echo "   push feito; run_id $RUN_ID"
INICIO=$(date +%s)
while :; do
  sleep 120
  st=$(K kernels status "$KID" 2>&1)
  case "$st" in *RUNNING*|*QUEUED*|*running*|*queued*) ;; *) break;; esac
  [ $(( $(date +%s)-INICIO )) -gt 10800 ] && { echo "passou de 3 h: desisto de esperar" >&2; exit 1; }
done
echo "   $(( ($(date +%s)-INICIO)/60 )) min: $st"
K kernels output "$KID" -p /w/out -o >/dev/null 2>&1
mkdir -p "$OUT"; ssh "$VM" "sudo tar cf - -C $T/out ." | tar xf - -C "$OUT"; chmod -R go-rwx "$OUT"
python3 -I - "$OUT/manifesto.json" "$RUN_ID" "$MARCA" <<'PY' || { tail -c 3000 "$OUT"/*.log >&2; exit 1; }
import json, sys, collections
m = json.load(open(sys.argv[1]))
assert m["run_id"] == sys.argv[2] and m["marca_dataset"] == sys.argv[3], (m["run_id"], m["marca_dataset"])
g = m["geradas"]; ac = [x for x in g if "arquivo" in x]
print(f"   marcas conferidas; aceitas {len(ac)}/{len(g)} de {len({x['falante'] for x in ac})} vozes;",
      "rejeições", dict(collections.Counter(x.get("motivo") or ("erro" if "erro" in x else "?") for x in g if "arquivo" not in x)))
PY
