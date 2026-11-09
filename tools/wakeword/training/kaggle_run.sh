#!/bin/bash
# Treina uma palavra no Kaggle pela CLI oficial (spec 2026-10-24). Roda no HA; o docker e o token ficam na VM.
# Uso: kaggle_run.sh <modelo: friday|sexta_feira> <cycle_dir> [--smoke]           sobe o dataset, confere a marca, dispara
#      kaggle_run.sh <modelo> <cycle_dir> --fetch [--smoke]                       baixa a saída e confere a marca
# 02/11 (regra do dono, revista em 06/11): nada de polling aqui. O disparo grava <cycle_dir>/kaggle_pending[_smoke].json, imprime
# o link e deixa o kaggle_watch.sh na VM (status a cada 15 min, aviso no Telegram); depois do aviso, o --fetch.
#   <cycle_dir>/upload/        -> dataset privado joseluiz98/wakeword-<slug>-data (só recortes de treino + params.json)
#   <cycle_dir>/models/        <- saída da rodada cheia  (models_smoke/ com --smoke)
# Falha (sai != 0) com o estado e o fim do log, sem copiar nenhum .onnx (Foco 5 do plano).
# O token do Kaggle nunca passa pelo HA: o ssh remoto o lê de /srv/friday/state/kaggle_api.token.
set -uo pipefail
MODEL=${1:?modelo}; CYCLE=${2:?cycle_dir}; SMOKE=0; FETCH=0
for a in "${@:3}"; do case "$a" in --smoke) SMOKE=1;; --fetch) FETCH=1;; *) echo "opção desconhecida: $a" >&2; exit 2;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
VM="$(cat /config/.friday/vm)"; OWNER=joseluiz98
SLUG=${MODEL//_/-}
KID=$OWNER/wakeword-$SLUG; DID=$OWNER/wakeword-$SLUG-data
T=/tmp/wwt-kaggle-$$            # na VM
L=/tmp/wwt-kaggle-local-$$      # no HA
PEND=$CYCLE/kaggle_pending.json; [ $SMOKE = 1 ] && PEND=$CYCLE/kaggle_pending_smoke.json
[ -d "$CYCLE/upload" ] || { echo "sem $CYCLE/upload (rode o build_dataset antes)" >&2; exit 2; }
[ -f "$CYCLE/upload/params.json" ] || { echo "sem upload/params.json" >&2; exit 2; }
cleanup() { ssh "$VM" "sudo rm -rf $T" >/dev/null 2>&1; rm -rf "$L"; }
trap cleanup EXIT

# K <args do kaggle sem espaços>: roda a CLI na VM. O token é lido lá; nada dele é ecoado aqui.
K() { ssh "$VM" "sudo docker run --rm -e KAGGLE_API_TOKEN=\"\$(sudo cat /srv/friday/state/kaggle_api.token)\" -v $T:/w -w /w friday-kaggle:1 $*"; }

if [ $FETCH = 0 ]; then
echo "== preparando ($MODEL, smoke=$SMOKE)"
mkdir -p "$L/data"
cp -r "$CYCLE"/upload/. "$L/data/"
cp "$HERE/kaggle/wakeword_v2.ipynb" "$L/" || { echo "notebook ausente (rode build_notebook.py)" >&2; exit 2; }
python3 -I "$HERE/kaggle_meta.py" meta "$MODEL" "$L" || exit 2
# marca única desta subida: o kernel só pode partir quando o Kaggle servir ESTE params.json, e o metrics.json
# tem de trazê-la de volta. Sem isso, em 26/10 o kernel treinou com a versão anterior do dataset (status já "ready").
MARCA="$(basename "$CYCLE")-$(date +%Y%m%d%H%M%S)-$(ls "$L/data/positive" | wc -l)p$(ls "$L/data/negative" | wc -l)n"
python3 -I - "$L/data/params.json" "$MARCA" <<'PY2'
import json, sys
p = json.load(open(sys.argv[1])); p["upload_id"] = sys.argv[2]; json.dump(p, open(sys.argv[1], "w"), indent=1)
PY2
echo "   marca: $MARCA"
if [ $SMOKE = 1 ]; then
  python3 -I - "$L/data/params.json" <<'PY'
import json, sys
p = json.load(open(sys.argv[1])); p["smoke"] = True; json.dump(p, open(sys.argv[1], "w"), indent=1)
PY
fi
ssh "$VM" "mkdir -p $T" && tar cf - -C "$L" . | ssh "$VM" "tar xf - -C $T" || { echo "falha ao copiar para a VM" >&2; exit 1; }

echo "== dataset $DID"
if K datasets status "$DID" >/dev/null 2>&1; then
  K datasets version -p /w/data -r zip -m "cycle-$(date +%Y%m%d)" || { echo "falha no datasets version" >&2; exit 1; }
else
  K datasets create -p /w/data -r zip || { echo "falha no datasets create" >&2; exit 1; }
fi
# "status ready" não basta (vale para a versão anterior enquanto a nova processa): espera o params.json servido
# pelo Kaggle trazer a marca desta subida
st=""
for i in $(seq 1 40); do   # até 20 min
  sleep 30
  K datasets download "$DID" -f params.json -p /w/chk --force >/dev/null 2>&1
  st=$(ssh "$VM" "sudo cat $T/chk/params.json 2>/dev/null" | python3 -I -c 'import json,sys
try: print(json.load(sys.stdin).get("upload_id", ""))
except Exception: print("")')
  [ "$st" = "$MARCA" ] && break
done
[ "$st" = "$MARCA" ] || { echo "o Kaggle não passou a servir a versão nova em 20 min (serve: '${st:-sem marca}')" >&2; exit 1; }
echo "   dataset novo no ar ($(( i*30 )) s)"

echo "== push do kernel $KID"
K kernels push -p /w --accelerator NvidiaTeslaT4 || { echo "falha no kernels push" >&2; exit 1; }
python3 -I -c 'import json,sys; json.dump({"marca": sys.argv[1], "kernel": sys.argv[2]}, open(sys.argv[3], "w"))' "$MARCA" "$KID" "$PEND"
# 06/11: unidade com nome fixo por kernel; a vigia anterior sai antes, senão duas vigiam o mesmo kernel e a velha lê o status da rodada nova
ssh $VM "sudo systemctl stop kaggle-watch-${KID##*/}.service 2>/dev/null; sudo systemd-run --quiet --collect --unit=kaggle-watch-${KID##*/} /bin/bash /mnt/haos/config/tools/satelite/wakeword-training/kaggle_watch.sh $KID 'buscar o resultado'" \
  || echo "AVISO: vigia do Kaggle não subiu; acompanhe pelo link" >&2
echo "DISPARADO. Acompanhe em https://www.kaggle.com/code/$KID ; quando terminar: kaggle_run.sh $MODEL $CYCLE --fetch$([ $SMOKE = 1 ] && echo ' --smoke')"
exit 0
fi

[ -f "$PEND" ] || { echo "sem $PEND (nada disparado deste ciclo)" >&2; exit 2; }
MARCA=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["marca"])' "$PEND")
ssh "$VM" "mkdir -p $T"
txt=$(K kernels status "$KID" 2>&1)
verdict=$(python3 -I "$HERE/kaggle_meta.py" decide "$txt")
[ "$verdict" = running ] && { echo "o kernel ainda está rodando: avise quando terminar (sem polling)" >&2; exit 4; }
DEST=$CYCLE/models; [ $SMOKE = 1 ] && DEST=$CYCLE/models_smoke
echo "== saída do kernel ($verdict)"
K kernels output "$KID" -p /w/out -o >/dev/null 2>&1
# o log completo fica no ciclo (600) para diagnóstico; o terminal recebe só o fim, já legível
ssh "$VM" "sudo sh -c 'cat $T/out/*.log 2>/dev/null'" > "$CYCLE/kaggle_last.log"; chmod 600 "$CYCLE/kaggle_last.log"
if [ "$verdict" != complete ]; then
  echo "KERNEL $verdict: $txt (log completo: $CYCLE/kaggle_last.log)" >&2
  ssh "$VM" "sudo sh -c 'tail -n 40 $T/out/*.log 2>/dev/null'" >&2
  exit 1
fi
rm -rf "$DEST"; mkdir -p "$DEST"; chmod 700 "$DEST"
ssh "$VM" "sudo tar cf - -C $T/out ." | tar xf - -C "$DEST"
for f in "$MODEL.onnx" metrics.json; do
  [ -s "$DEST/$f" ] || { echo "faltou $f na saída ($DEST)" >&2; ssh "$VM" "sudo sh -c 'tail -n 40 $T/out/*.log 2>/dev/null'" >&2; exit 1; }
done
chmod -R go-rwx "$DEST"
USADA=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("params", {}).get("upload_id", ""))' "$DEST/metrics.json")
if [ "$USADA" != "$MARCA" ]; then
  mv "$DEST" "$DEST.dados_errados.$(date +%H%M%S)"
  echo "O KERNEL TREINOU COM OUTRA VERSÃO DO DATASET (marca '${USADA:-nenhuma}', esperada '$MARCA'): modelo descartado" >&2
  exit 1
fi
echo "OK: $DEST"; ls -l "$DEST"
cat "$DEST/metrics.json"
