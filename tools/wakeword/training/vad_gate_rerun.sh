#!/usr/bin/env bash
# Reroda o ensaio da porta de voz (vad_gate_trial.py) com o ambiente de um dia já enviado à VM (envio noturno 03:30).
# Uso (no HA): bash vad_gate_rerun.sh AAAAMMDD
# Saída: tabela por regra + cruzamento com os eventos ao vivo daquele dia (vad_gate_ambiente.py).
set -euo pipefail
[ -f /srv/friday/state/local.env ] && . /srv/friday/state/local.env   # FRIDAY_SAT_OFFICE (house value, never in the repo)
DIA=${1:?AAAAMMDD}
VM="$(cat /config/.friday/vm)"
PI="jose@$(cat /config/.friday/mk1)"
W=/srv/wakeword-data/work/vadgate-$DIA
I=/srv/wakeword-data/incoming/${FRIDAY_SAT_OFFICE:?set FRIDAY_SAT_OFFICE (local.env)}/gravacoes
CODE=/mnt/haos/config/tools/satelite/wakeword-training
T=${CLAUDE_JOB_DIR:-/tmp}/vadgate-$DIA; mkdir -p "$T"
ssh $VM "sudo mkdir -p $W && sudo sh -c 'ls $I/ambiente/ambiente_${DIA}_*.wav | sed s#$I#/in# > $W/list_amb.txt; wc -l < $W/list_amb.txt'"
ssh $VM "sudo docker run --rm --name wwt-vadgate-$DIA --cpus 2 --memory 2g -v $W:/d -v /opt/friday/wakeword-trainer/models:/m:ro \
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
