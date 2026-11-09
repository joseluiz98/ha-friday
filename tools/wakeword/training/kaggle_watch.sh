#!/bin/bash
# Vigia um kernel do Kaggle e avisa o o dono no Telegram quando ele termina (06/11/2026). Roda NA VM, como root, numa unidade
# transitória (systemd-run): não instala nada e some ao acabar. O Kaggle não tem webhook, então é consulta de status,
# no intervalo que o o dono aceitou (15 min, regra de 06/11). Resultado em /srv/friday/state/kaggle-watch/<slug>.
# Uso: kaggle_watch.sh <owner/kernel> "<próximo passo>" [minutos] [horas_max]     DRY=1 não envia nem espera
set -uo pipefail
KID=${1:?kernel}; NEXT=${2:-}; MIN=${3:-15}; MAXH=${4:-8}
SLUG=${KID##*/}; D=/srv/friday/state/kaggle-watch; ESTADO=$D/$SLUG; TG=000000000
mkdir -p "$D"; rm -f "$ESTADO"
LINK="https://www.kaggle.com/code/$KID"
INICIO=$(date +%s)
[ "${DRY:-}" = 1 ] || sleep 120  # 06/11: logo após o push o status ainda pode ser o COMPLETE da versão anterior
while :; do
  st=$(docker run --rm -e KAGGLE_API_TOKEN="$(cat /srv/friday/state/kaggle_api.token)" friday-kaggle:1 kernels status "$KID" 2>&1 | tail -1)
  case "${st,,}" in
    *complete*) fim=terminou;;
    *error*|*cancel*|*fail*) fim=falhou;;
    *) fim="";;
  esac
  [ -n "$fim" ] && break
  if [ $(( $(date +%s) - INICIO )) -gt $(( MAXH * 3600 )) ]; then fim="passou de ${MAXH} h sem terminar"; break; fi
  [ "${DRY:-}" = 1 ] && { echo "ainda rodando: $st"; exit 0; }
  sleep $(( MIN * 60 ))
done
msg="Kaggle: $SLUG $fim após $(( ($(date +%s) - INICIO) / 60 )) min de vigia. $LINK"
[ -n "$NEXT" ] && [ "$fim" = terminou ] && msg="$msg Próximo: $NEXT"
echo "$fim $(date -Is)" > "$ESTADO"
if [ "${DRY:-}" = 1 ]; then echo "$msg"; rm -f "$ESTADO"; exit 0; fi
oc message send --channel telegram --target "$TG" -m "$msg" >/dev/null 2>&1 || echo "aviso no Telegram falhou" >&2
