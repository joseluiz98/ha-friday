#!/bin/bash
# Roda o notebook de amostras no Kaggle (27/10/2026) e baixa as falas para <saida>. Uso: run_tts_samples.sh <saida>
# Confere no fim que o manifesto traz a MESMA marca RUN_ID desta subida (lição de 26/10: nunca confiar na versão).
set -uo pipefail
OUT=${1:?saida}; HERE=$(cd "$(dirname "$0")" && pwd)
VM="$(cat /config/.friday/vm)"; KID=joseluiz98/tts-samples-ptbr; T=/tmp/tts-samples-$$; L=/tmp/tts-samples-local-$$
RUN_ID=tts-$(date +%Y%m%d%H%M%S)
K() { ssh "$VM" "sudo docker run --rm -e KAGGLE_API_TOKEN=\"\$(sudo cat /srv/friday/state/kaggle_api.token)\" -v $T:/w -w /w friday-kaggle:1 $*"; }
trap 'ssh "$VM" "sudo rm -rf $T" >/dev/null 2>&1; rm -rf "$L"' EXIT
python3 -I "$HERE/build_tts_samples.py" "$L" "$RUN_ID" || exit 2
ssh "$VM" "mkdir -p $T" && tar cf - -C "$L" . | ssh "$VM" "tar xf - -C $T"
K kernels push -p /w --accelerator NvidiaTeslaT4 || exit 1
echo "   marca: $RUN_ID"
INICIO=$(date +%s)
while :; do
  sleep 60
  st=$(K kernels status "$KID" 2>&1)
  case "$st" in *RUNNING*|*QUEUED*|*running*|*queued*) ;; *) break;; esac
  [ $(( $(date +%s)-INICIO )) -gt 5400 ] && { echo "passou de 90 min: desisto de esperar" >&2; exit 1; }
done
echo "   $(( ($(date +%s)-INICIO)/60 )) min: $st"
K kernels output "$KID" -p /w/out -o >/dev/null 2>&1
mkdir -p "$OUT"; ssh "$VM" "sudo tar cf - -C $T/out ." | tar xf - -C "$OUT"; chmod -R go-rwx "$OUT"
USADA=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("run_id",""))' "$OUT/manifesto.json" 2>/dev/null)
[ "$USADA" = "$RUN_ID" ] || { echo "MARCA ERRADA: '$USADA' (esperada $RUN_ID)" >&2; tail -c 3000 "$OUT"/*.log >&2; exit 1; }
python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d["saidas"], ensure_ascii=False, indent=0))' "$OUT/manifesto.json"
