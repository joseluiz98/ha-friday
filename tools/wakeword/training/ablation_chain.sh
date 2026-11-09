#!/bin/bash
# Roda variantes de ablação em sequência no Kaggle (07/11/2026): push da rodada cheia, espera a vigia da VM acabar
# (laço de shell, sem modelo), --fetch (confere a marca) e passa para a próxima. Para na primeira falha.
# Uso: ablation_chain.sh <dir_ablacao> v0 v1 ...     (cada <dir>/<v>/upload/params.json já pronto)
H=/config/tools/satelite/wakeword-training; VM="$(cat /config/.friday/vm)"; B=${1:?dir}; shift
for v in "$@"; do
  echo "== $v push $(date '+%d/%m %T')"
  bash "$H/kaggle_run.sh" sexta_feira "$B/$v" > "$B/$v/run.out" 2>&1 || { echo "FALHA no push de $v"; tail -5 "$B/$v/run.out"; exit 1; }
  sleep 60
  ssh "$VM" 'while systemctl is-active --quiet kaggle-watch-wakeword-sexta-feira.service; do sleep 60; done; sudo cat /srv/friday/state/kaggle-watch/wakeword-sexta-feira'
  bash "$H/kaggle_run.sh" sexta_feira "$B/$v" --fetch > "$B/$v/fetch.out" 2>&1 || { echo "FALHA no fetch de $v"; tail -5 "$B/$v/fetch.out"; exit 1; }
  echo "== $v ok $(date '+%d/%m %T'): $(ls "$B/$v/models/ckpts" 2>/dev/null | grep -c onnx) checkpoints"
done
echo "FIM $(date '+%d/%m %T')"
