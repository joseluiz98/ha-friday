#!/bin/sh
# Monta o cache da prova da Sexta-feira na VM (07/11/2026): 3 dias de ambiente (03-05/11) e os clipes de prova.
# Uso (na VM): sh featcache_build.sh [--sat <sat_id>]   -> /srv/wwt-featcache/{amb20261103_<sat>,...,sessoes,cv27,...}
# --sat: satélite do ambiente (padrão FRIDAY_SAT_OFFICE de local.env); o cache do dia leva o sat_id (18/11/2026).
[ -f /srv/friday/state/local.env ] && . /srv/friday/state/local.env   # FRIDAY_SAT_OFFICE (house value, never in the repo)
SAT=${FRIDAY_SAT_OFFICE:-}
while [ $# -gt 0 ]; do
  case "$1" in --sat) SAT=${2:?--sat <sat_id>}; shift 2 ;; *) echo "uso: featcache_build.sh [--sat <sat_id>]" >&2; exit 2 ;; esac
done
: "${SAT:?set FRIDAY_SAT_OFFICE (local.env) or pass --sat}"
C=/srv/wwt-featcache; P=/tmp/wwt-prova-c4
CY=/srv/nobackup/wakeword-data/sexta_feira/cycle-20261106b
R="sudo docker run --rm -v $C:/fc -v $P:/p:ro -v /srv/nobackup/wakeword-data:/s:ro -v $CY:/c:ro -v /srv/wakeword-data/incoming/$SAT/gravacoes/ambiente:/a:ro -v /mnt/haos/config/tools/satelite/wakeword-training:/code:ro --entrypoint sh wakeword-trainer:1 -c"
for d in 20261103 20261104 20261105; do
  $R "python /code/featcache.py cache /fc amb${d}_$SAT ambiente /a/ambiente_${d}_*.wav" > "$C/amb${d}_$SAT.log" 2>&1 &
done
$R "python /code/featcache.py cache /fc sessoes sessao /c/holdout \
 && for g in cv27 xtts xttsvar ambiente xttsneg; do python /code/featcache.py cache /fc nat_\$g clipe /c/holdout_native/\$g || exit 1; done \
 && python /code/featcache.py cache /fc sala clipe /s/friday/cycle-20261026/holdout \
 && python /code/featcache.py cache /fc conversa clipe $(cat $P/alvos.txt | tr '\n' ' ')" > $C/clipes.log 2>&1 &
wait
echo FIM $(date +%T)
