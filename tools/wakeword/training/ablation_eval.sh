#!/bin/bash
# Avalia todos os checkpoints de cada variante assim que o --fetch dela chega (07/11/2026): copia para a VM, pontua no
# featcache (3 dias de ambiente + clipes de prova, regras reais) e grava <dir>/<v>/report.tsv. Espera com laço de shell.
# Uso: ablation_eval.sh <dir_ablacao> v0 v1 ...
H=/config/tools/satelite/wakeword-training; VM="$(cat /config/.friday/vm)"; B=${1:?dir}; shift
C=/srv/wwt-featcache; P=/tmp/wwt-prova-c4
S=amb20261103,amb20261104,amb20261105,conversa,nat_ambiente,nat_cv27,nat_xtts,nat_xttsneg,nat_xttsvar,sala,sessoes
for v in "$@"; do
  until [ -s "$B/$v/models/ckpts/ckpts.json" ] || grep -q "FALHA" "$B/chain.log" 2>/dev/null; do sleep 120; done
  [ -s "$B/$v/models/ckpts/ckpts.json" ] || { echo "cadeia falhou antes de $v"; exit 1; }
  echo "== $v $(date '+%d/%m %T')"
  ssh $VM "rm -rf $P/abl/$v; mkdir -p $P/abl/$v"
  scp -q "$B/$v/models/ckpts/"*.onnx "$B/$v/models/sexta_feira.onnx" $VM:$P/abl/$v/
  M=$(ls "$B/$v/models/ckpts/" | grep onnx | sed -E "s#ckpt_([0-9]+)\.onnx#${v}k\1=/p/abl/$v/ckpt_\1.onnx#" | tr '\n' ' ')
  ssh $VM "sudo docker run --rm -v $C:/fc -v $P:/p:ro -v /mnt/haos/config/tools/satelite/wakeword-training:/code:ro --entrypoint python wakeword-trainer:1 /code/featcache.py score /fc /fc/score_$v.json $S /p/sexta_feira_verificador.pkl ${v}final=/p/abl/$v/sexta_feira.onnx $M" > "$B/$v/score.log" 2>&1 || { echo "falha no score de $v"; exit 1; }
  scp -q $VM:$C/score_$v.json "$B/$v/score.json"
  python3 "$H/ablation_report.py" "$B/$v/score.json" > "$B/$v/report.tsv" && echo "   report: $B/$v/report.tsv"
done
echo "FIM $(date '+%d/%m %T')"
