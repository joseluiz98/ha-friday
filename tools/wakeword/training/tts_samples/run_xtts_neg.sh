#!/bin/bash
# Gera os negativos nativos com XTTS-v2 no Kaggle (06/11/2026). A vigia é o kaggle_watch.sh na VM (aviso no Telegram).
# 06/11: o Whisper na GPU do Kaggle falhou nas 600 falas (asr: RuntimeError); depois do --fetch, filtrar na VM com filtrar_xtts_neg.py (imagem friday-asr + pip install soundfile scipy).
#   run_xtts_neg.sh <saida> <marca_do_dataset> [takes]   confere a marca servida, faz o push, imprime o link e SAI
#   run_xtts_neg.sh <saida> --fetch                      quando o o dono avisar: baixa, confere run_id e marca, resume
set -uo pipefail
OUT=${1:?saida}; HERE=$(cd "$(dirname "$0")" && pwd)
VM="$(cat /config/.friday/vm)"; KID=joseluiz98/xtts-sexta-neg-gen; DID=joseluiz98/ptbr-voice-refs
T=/tmp/xtts-neg-$$; L=/tmp/xtts-neg-local-$$
K() { ssh "$VM" "sudo docker run --rm -e KAGGLE_API_TOKEN=\"\$(sudo cat /srv/friday/state/kaggle_api.token)\" -v $T:/w -w /w friday-kaggle:1 $*"; }
trap 'ssh "$VM" "sudo rm -rf $T" >/dev/null 2>&1; rm -rf "$L"' EXIT
mkdir -p "$OUT"; PEND="$OUT/pending.json"
if [ "${2:-}" = --fetch ]; then
  [ -f "$PEND" ] || { echo "sem $PEND: nada para buscar" >&2; exit 1; }
  ssh "$VM" "mkdir -p $T/out"
  st=$(K kernels status "$KID" 2>&1); echo "   $st"
  case "$st" in *complete*|*COMPLETE*) ;; *) echo "ainda não terminou (ou falhou): nada baixado" >&2; exit 1;; esac
  K kernels output "$KID" -p /w/out -o >/dev/null 2>&1
  ssh "$VM" "sudo tar cf - -C $T/out ." | tar xf - -C "$OUT"; chmod -R go-rwx "$OUT"
  python3 -I - "$OUT/manifesto.json" "$PEND" <<'PY' || exit 1
import json, sys, collections
m = json.load(open(sys.argv[1])); p = json.load(open(sys.argv[2]))
assert m["run_id"] == p["run_id"] and m["marca_dataset"] == p["marca"], (m["run_id"], m["marca_dataset"], p)
g = m["geradas"]; ac = [x for x in g if "arquivo" in x]
print(f"   marcas conferidas; aceitas {len(ac)}/{len(g)} de {len({x['falante'] for x in ac})} vozes;",
      "rejeições", dict(collections.Counter(x.get("motivo") or ("erro" if "erro" in x else "?") for x in g if "arquivo" not in x)))
print("   por texto:", collections.Counter(x["texto"] for x in ac).most_common())
PY
  exit 0
fi
MARCA=${2:?marca}; TAKES=${3:-2}; RUN_ID=xttsneg-$(date +%Y%m%d%H%M%S)
ssh "$VM" "mkdir -p $T/chk"
K datasets download "$DID" -f marca.json -p /w/chk --force >/dev/null 2>&1
SERV=$(ssh "$VM" "sudo cat $T/chk/marca.json 2>/dev/null" | python3 -I -c 'import json,sys
try: print(json.load(sys.stdin)["marca"])
except Exception: print("")')
[ "$SERV" = "$MARCA" ] || { echo "DATASET ERRADO: o Kaggle serve '${SERV:-nada}', esperado '$MARCA'. Nada foi executado." >&2; exit 1; }
echo "   dataset conferido antes do push: $SERV"
python3 -I "$HERE/build_xtts_neg.py" "$L" "$MARCA" "$RUN_ID" "$TAKES" >/dev/null || exit 2
tar cf - -C "$L" . | ssh "$VM" "tar xf - -C $T"
K kernels push -p /w --accelerator NvidiaTeslaT4 || exit 1
printf '{"run_id": "%s", "marca": "%s"}\n' "$RUN_ID" "$MARCA" > "$PEND"
echo "   push feito; run_id $RUN_ID"
ssh "$VM" "sudo systemd-run --quiet --collect --unit=kaggle-watch-$$ /bin/bash /mnt/haos/config/tools/satelite/wakeword-training/kaggle_watch.sh $KID 'baixar e separar a amostra de escuta'" \
  || echo "AVISO: vigia do Kaggle não subiu; acompanhe pelo link" >&2
echo "   https://www.kaggle.com/code/${KID}"
echo "   quando terminar: run_xtts_neg.sh $OUT --fetch"
