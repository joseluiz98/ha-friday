#!/bin/bash
# Cache do ambiente para a prova das palavras (08/11/2026, pedido do o dono). Roda NA VM como root, acordado pelo
# friday-wwt-featcache.path quando a Pi grava a marca `.sync_ok` no fim de um envio noturno inteiro e sem erro
# (friday-sync-recordings na Pi, 03:30). Sem marca não roda: dia pela metade nunca entra no cache.
# 1) todo dia de gravação anterior a hoje sem /srv/wwt-featcache/ambAAAAMMDD.json passa uma vez pelo extrator
#    (featcache.py cache, modo ambiente; ~80 min por dia, um por vez, nice 19);
# 2) retenção (o dono, 08/11): áudio de ambiente na VM por 30 dias; o cache sai junto com o áudio.
# Falha avisa no Telegram. Instalação: cópia em /opt/friday/wwt/ (o featcache.py vem do HA pelo Samba).
set -uo pipefail
[ -f /srv/friday/state/local.env ] && . /srv/friday/state/local.env   # FRIDAY_SAT_OFFICE (house value, never in the repo)
C=/srv/wwt-featcache; A=/srv/wakeword-data/incoming/${FRIDAY_SAT_OFFICE:?set FRIDAY_SAT_OFFICE (local.env)}/gravacoes/ambiente
CODE=/mnt/haos/config/tools/satelite/wakeword-training; TG=000000000; HOJE=$(date +%Y%m%d)
RETER_DIAS=30; CORTE=$(date -d "-$RETER_DIAS days" +%Y%m%d)
falha() { oc message send --channel telegram --target "$TG" -m "Cache noturno das palavras falhou ($1). Log: journalctl -u friday-wwt-featcache" >/dev/null 2>&1; exit 1; }
echo "marca da Pi: $(cat /srv/wakeword-data/incoming/${FRIDAY_SAT_OFFICE:?set FRIDAY_SAT_OFFICE (local.env)}/.sync_ok 2>/dev/null)"
[ -f "$CODE/featcache.py" ] || falha "featcache.py inalcançável pelo Samba"
mkdir -p "$C"; chown friday: "$C"
# retenção do áudio primeiro (não gasta extrator com dia que vai sair)
apagados=0
for f in "$A"/ambiente_????????_*.wav; do
  [ -e "$f" ] || continue
  d=$(basename "$f" | cut -c10-17)
  [ "$d" -lt "$CORTE" ] && { rm -f "$f"; apagados=$((apagados + 1)); }
done
feitos=0
for d in $(ls "$A" | sed -nE 's/^ambiente_([0-9]{8})_.*\.wav$/\1/p' | sort -u); do
  [ "$d" -lt "$HOJE" ] || continue
  [ -s "$C/amb$d.json" ] && continue
  echo "== amb$d $(date +%T)"
  nice -n 19 docker run --rm -v "$C":/fc -v "$A":/a:ro -v "$CODE":/code:ro --entrypoint sh wakeword-trainer:1 \
    -c "python /code/featcache.py cache /fc amb$d ambiente /a/ambiente_${d}_*.wav" > "$C/amb$d.log" 2>&1 \
    || { rm -f "$C/amb$d".*.npy "$C/amb$d.json"; falha "dia $d, ver $C/amb$d.log"; }
  feitos=$((feitos + 1))
done
# o cache acompanha o áudio: dia sem áudio sai do cache
for j in "$C"/amb????????.json; do
  [ -e "$j" ] || continue
  d=$(basename "$j" .json); d=${d#amb}
  ls "$A"/ambiente_"$d"_*.wav >/dev/null 2>&1 || { rm -f "$C/amb$d".*; echo "removido do cache: $d (sem áudio)"; }
done
echo "dias novos no cache: $feitos; wavs apagados (> $RETER_DIAS dias): $apagados; cache: $(du -sh "$C" | cut -f1); disco livre: $(df -h / | awk 'NR==2{print $4}')"
touch /srv/friday/state/wwt-featcache.ok
