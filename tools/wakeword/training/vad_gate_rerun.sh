#!/usr/bin/env bash
# Reroda o ensaio da porta de voz (vad_gate_trial.py) com o ambiente de um dia já enviado à VM (envio noturno 03:30).
# Uso (no HA): bash vad_gate_rerun.sh AAAAMMDD [--sat <sat_id>]
# --sat: satélite (padrão FRIDAY_SAT_OFFICE, o do escritório, com a Pi de /config/.friday/mk1); outro satélite acha a
# Pi em /share/friday/satellites.json (18/11/2026).
# Saída: tabela por regra + cruzamento com os eventos ao vivo daquele dia (vad_gate_ambiente.py).
set -euo pipefail
[ -f /srv/friday/state/local.env ] && . /srv/friday/state/local.env   # FRIDAY_SAT_OFFICE (house value, never in the repo)
DIA=${1:?AAAAMMDD}; shift
SAT=${FRIDAY_SAT_OFFICE:-}
while [ $# -gt 0 ]; do
  case "$1" in --sat) SAT=${2:?--sat <sat_id>}; shift 2 ;; *) echo "uso: vad_gate_rerun.sh AAAAMMDD [--sat <sat_id>]" >&2; exit 2 ;; esac
done
: "${SAT:?set FRIDAY_SAT_OFFICE (local.env) or pass --sat}"
VM="$(cat /config/.friday/vm)"
if [ "$SAT" = "${FRIDAY_SAT_OFFICE:-}" ]; then
  PI="jose@$(cat /config/.friday/mk1)"
else
  PI="jose@$(python3 -c 'import json,sys; print(json.load(open("/share/friday/satellites.json"))[sys.argv[1]]["ip"])' "$SAT")"
fi
W=/srv/wakeword-data/work/vadgate-$SAT-$DIA
I=/srv/wakeword-data/incoming/$SAT/gravacoes
CODE=/mnt/haos/config/tools/satelite/wakeword-training
T=${CLAUDE_JOB_DIR:-/tmp}/vadgate-$SAT-$DIA; mkdir -p "$T"
ssh $VM "sudo mkdir -p $W && sudo sh -c 'ls $I/ambiente/ambiente_${DIA}_*.wav | sed s#$I#/in# > $W/list_amb.txt; wc -l < $W/list_amb.txt'"
ssh $VM "sudo docker run --rm --name wwt-vadgate-$SAT-$DIA --cpus 2 --memory 2g -v $W:/d -v /opt/friday/wakeword-trainer/models:/m:ro \
  -v $CODE:/code:ro -v /mnt/haos/config/tools/satelite/programa:/programa:ro -v $I:/in:ro wakeword-trainer:1 \
  python /code/vad_gate_trial.py frames /d/frames_amb.ndjson /m @/d/list_amb.txt | tail -1"
ssh $VM "sudo python3 -c \"
import json
n=[json.loads(l)['name'] for l in open('$W/frames_amb.ndjson')]
json.dump({x:{'neg':True} for x in n}, open('$W/rot_amb.json','w'))\"
sudo docker run --rm -v $W:/d -v $CODE:/code:ro wakeword-trainer:1 python /code/vad_gate_trial.py sim /d/frames_amb.ndjson /d/rot_amb.json /d/sim_amb.json >/dev/null
sudo cat $W/sim_amb.json" > "$T/sim_amb.json"
D="${DIA:0:4}-${DIA:4:2}-${DIA:6:2}"
ssh $PI "grep -a '\"$D' ~/gravacoes/eventos_palavra.ndjson | grep -av '\"quase\"'" > "$T/ev.ndjson" || true
python3 "$(dirname "$0")/vad_gate_ambiente.py" "$T/sim_amb.json" "$T/ev.ndjson" 'voz>=0.6' 'voz(0.80s)>=0.6' 'voz(0.80s)>=0.5' 'voz>=0.0'
