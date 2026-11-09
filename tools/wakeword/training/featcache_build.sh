#!/bin/sh
# Monta o cache da prova da Sexta-feira na VM (07/11/2026): 3 dias de ambiente (03-05/11) e os clipes de prova.
# Uso (na VM): sh featcache_build.sh      -> /srv/wwt-featcache/{amb20261103,...,sessoes,cv27,...}
C=/srv/wwt-featcache; P=/tmp/wwt-prova-c4
CY=/mnt/haos/share/wakeword-data/sexta_feira/cycle-20261106b
R="sudo docker run --rm -v $C:/fc -v $P:/p:ro -v /mnt/haos/share/wakeword-data:/s:ro -v $CY:/c:ro -v /srv/wakeword-data/incoming/${FRIDAY_SAT_OFFICE:?set FRIDAY_SAT_OFFICE (local.env)}/gravacoes/ambiente:/a:ro -v /mnt/haos/config/tools/satelite/wakeword-training:/code:ro --entrypoint sh wakeword-trainer:1 -c"
for d in 20261103 20261104 20261105; do
  $R "python /code/featcache.py cache /fc amb$d ambiente /a/ambiente_${d}_*.wav" > $C/amb$d.log 2>&1 &
done
$R "python /code/featcache.py cache /fc sessoes sessao /c/holdout \
 && for g in cv27 xtts xttsvar ambiente xttsneg; do python /code/featcache.py cache /fc nat_\$g clipe /c/holdout_native/\$g || exit 1; done \
 && python /code/featcache.py cache /fc sala clipe /s/friday/cycle-20261026/holdout \
 && python /code/featcache.py cache /fc conversa clipe $(cat $P/alvos.txt | tr '\n' ' ')" > $C/clipes.log 2>&1 &
wait
echo FIM $(date +%T)
