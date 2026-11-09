#!/bin/bash
# Análise do falso negativo da palavra (24/10/2026). Roda no HA (add-on SSH) e orquestra Pi, VM e HA:
#   1. copia os contexto_*.wav da Pi para a VM (pasta temporária)
#   2. transcreve na VM com a imagem friday-asr (docker run --rm: só gasta CPU/RAM enquanto roda)
#   3. repontua os mesmos WAVs na Pi com o modelo base (systemd-run com teto, nice 19)
#   4. traz falas/eventos da Pi e imprime o relatório (report.py)
# Uso: run.sh [--since AAAAMMDD] [--model /home/jose/modelos/friday.onnx] [--whisper small] [--out DIR]
# --model aceita um .onnx candidato já copiado para a Pi: compara o modelo novo nos mesmos áudios.
# Saída (transcrições = texto de conversas da casa) fica em --out, por padrão /tmp (efêmero, fora do backup).
set -euo pipefail
PI="jose@$(cat /config/.friday/mk1)"; VM="$(cat /config/.friday/vm)"
SINCE=00000000; MODEL=/home/jose/modelos/friday.onnx; WHISPER=small
OUT=/tmp/wakeword-analysis/$(date +%Y%m%d_%H%M%S)
while [ $# -gt 0 ]; do case "$1" in
  --since) SINCE=$2; shift 2;; --model) MODEL=$2; shift 2;; --whisper) WHISPER=$2; shift 2;;
  --out) OUT=$2; shift 2;; *) echo "opção desconhecida: $1" >&2; exit 2;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"; chmod 700 "$OUT"
TMP=/tmp/wwa-$$

LIST=$(ssh $PI "cd ~/gravacoes/ativacoes && ls contexto_*.wav 2>/dev/null" | awk -v s="$SINCE" 'substr($0,10,8) >= s')
N=$(printf '%s\n' "$LIST" | grep -c . || true)
[ "$N" -gt 0 ] || { echo "nenhum contexto_*.wav desde $SINCE"; exit 1; }
echo "== $N contextos desde $SINCE; modelo $MODEL; whisper $WHISPER; saída $OUT"

trap 'ssh $VM "sudo rm -rf $TMP" || true; ssh $PI "rm -rf $TMP" || true' EXIT
echo "== 1/4 Pi -> VM"
ssh $VM "mkdir -p $TMP/wav"
ssh $PI "cd ~/gravacoes/ativacoes && tar cf - $(echo $LIST)" | ssh $VM "tar xf - -C $TMP/wav"

echo "== 2/4 transcrição na VM (friday-asr)"
ssh $VM "sudo docker run --rm --name wwa-asr --cpus 3 --memory 3g -v /var/cache/friday-asr:/models \
  -v $TMP:/d friday-asr:1 /d/wav /d/transcricoes.ndjson $WHISPER" | tail -n +1 | sed 's/^/   /' | tail -3
scp -q $VM:$TMP/transcricoes.ndjson "$OUT/"

echo "== 3/4 repontuação na Pi (lenta de propósito: ~35 s por contexto com teto de 120% de CPU)"
ssh $PI "mkdir -p $TMP"
scp -q "$HERE/rescore.py" $PI:$TMP/
ssh $PI "cd ~/gravacoes/ativacoes && sudo systemd-run --scope -q -p MemoryMax=450M -p CPUQuota=120% \
  nice -n 19 sudo -u jose ~/oww/bin/python $TMP/rescore.py $MODEL $TMP/rescored.ndjson $(echo $LIST)"
scp -q $PI:$TMP/rescored.ndjson "$OUT/"

echo "== 4/4 relatório"
scp -q $PI:gravacoes/falas_palavra.ndjson $PI:gravacoes/eventos_palavra.ndjson "$OUT/"
DESDE=$(printf '%s' "$SINCE" | sed -E 's/^([0-9]{4})([0-9]{2})([0-9]{2})$/\1-\2-\3/; s/^0000-00-00$//')
python3 "$HERE/report.py" "$OUT" --desde "$DESDE" | tee "$OUT/relatorio.txt"
