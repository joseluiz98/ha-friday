#!/bin/bash
# Sessões guiadas para o ciclo 2 do retreino, numa sentada só (26/10/2026). Roda no HA (add-on SSH), o dono perto do Jabra.
#   Parte A: "Friday" com MÚSICA no satélite, 7 sessões x 20, cada uma com um estilo e um volume (o Jabra picota o "d"
#            enquanto toca; variar estilo e volume substitui sessões em dias diferentes)
#   Parte B (só com `tv`, é do ciclo 2 da SEXTA-FEIRA, que tem treino próprio): liga a TV, confere, volume 30, 2 x 20
#   Depois: religa o programa, devolve volume/TV como estavam e mostra a nota do modelo atual em cada sessão.
# Cada sessão começa com dois tons subindo (volte para perto); bipe curto = diga a palavra; tom grave na metade = ~3 m.
# Os arquivos (~/gravacoes/tentativas_<palavra>_<music|tv>_<data>.wav) entram sozinhos no harvest.
# Uso: guided_sessions.sh [music|tv|all]   (padrão music: ~18 min; tv ~5 min)
set -euo pipefail
PARTE=${1:-music}
PI="jose@$(cat /config/.friday/mk1)"
SSH="ssh -i /data/.ssh/id_ed25519 -o ConnectTimeout=8 $PI"
SAT=media_player.escritorio
TV=media_player.living_room_samsung_un58au7700_2021_crystal_uhd
MA_ENTRY=01M5AQ20ZYZJJJQTCMQNBR9CWK
HERE=$(cd "$(dirname "$0")" && pwd)
API=http://supervisor/core/api; AUTH="Authorization: Bearer $SUPERVISOR_TOKEN"
# Faixas do histórico do satélite (recorder, 19–26/10: 129 faixas, a maioria hard rock/metal; depois funk, pagode e
# sertanejo) | estilo | volume do satélite. "Alô Ex Amor" a 0,82 = o caso que falhou ao vivo em 26/10 11:03.
SESSOES=(
  "library://track/2014|sertanejo: Alô Ex Amor (Humberto & Ronaldo)|0.82"
  "library://track/3942|hard rock: Burn (Deep Purple)|1.0"
  "library://track/710|hard rock: Overdose (AC/DC)|0.8"
  "library://track/1546|metal: No Good (Pantera)|0.8"
  "library://track/3400|grunge: Even Flow (Pearl Jam)|0.6"
  "library://track/2404|funk: Bumbum Bate (MC Pedrinho)|1.0"
  "library://track/1182|pagode: Ela (Ferrugem)|0.8"
)

st() { curl -s -H "$AUTH" "$API/states/$1" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d$2)"; }
call() { curl -s -o /dev/null -w "%{http_code}" -X POST -H "$AUTH" -H "Content-Type: application/json" "$API/services/$1" -d "$2"; }
espera() {  # espera(entidade, estado, segundos)
  for _ in $(seq "$3"); do [ "$(st "$1" '["state"]')" = "$2" ] && return 0; sleep 1; done; return 1
}

E=$(st $SAT '["state"]'); [ "$E" != unavailable ] || { echo "satélite indisponível no MA"; exit 1; }
VOL0=$(st $SAT '["attributes"].get("volume_level")'); REP0=$(st $SAT '["attributes"].get("repeat")'); TV0=$(st $TV '["state"]'); TVVOL0=$(st $TV '["attributes"].get("volume_level")')
GRAVADOS=()

restaurar() {
  echo "== restaurando"
  call media_player/media_pause "{\"entity_id\": \"$SAT\"}" >/dev/null || true
  [ "$REP0" != None ] && call media_player/repeat_set "{\"entity_id\": \"$SAT\", \"repeat\": \"$REP0\"}" >/dev/null || true
  [ "$VOL0" != None ] && call media_player/volume_set "{\"entity_id\": \"$SAT\", \"volume_level\": $VOL0}" >/dev/null || true
  if [ "$PARTE" != music ]; then
    if [ "$TV0" = off ]; then call media_player/turn_off "{\"entity_id\": \"$TV\"}" >/dev/null || true
    elif [ "$TVVOL0" != None ]; then call media_player/volume_set "{\"entity_id\": \"$TV\", \"volume_level\": $TVVOL0}" >/dev/null || true; fi
  fi
  $SSH 'sudo systemctl start friday-satelite; sleep 3; echo "programa: $(systemctl is-active friday-satelite)"' || echo "FALHOU religar o programa"
}

sessao() {  # sessao(palavra, etiqueta, info-json)
  local out
  out=$($SSH "python3 /tmp/pi_guided_session.py --word $1 --tag $2 --info '${3//\'/’}'" | tee /dev/stderr | tail -1)
  case "$out" in /home/jose/gravacoes/tentativas_*.wav) GRAVADOS+=("$out");; *) echo "sessão sem arquivo: $out"; exit 1;; esac
}

call media_player/media_pause "{\"entity_id\": \"$SAT\"}" >/dev/null; sleep 1
$SSH 'sudo systemctl stop friday-satelite'
trap restaurar EXIT
scp -q -i /data/.ssh/id_ed25519 "$HERE/pi_guided_session.py" "$PI:/tmp/pi_guided_session.py"

if [ "$PARTE" != tv ]; then
  echo "== PARTE A: 'Friday' com música (${#SESSOES[@]} sessões)"
  # a faixa se repete se acabar antes da sessão (~2 min 10 s): nunca fica silêncio no meio
  call media_player/repeat_set "{\"entity_id\": \"$SAT\", \"repeat\": \"one\"}" >/dev/null
  for s in "${SESSOES[@]}"; do
    IFS='|' read -r URI ROT V <<<"$s"
    call media_player/volume_set "{\"entity_id\": \"$SAT\", \"volume_level\": $V}" >/dev/null
    call music_assistant/play_media "{\"entity_id\": \"$SAT\", \"media_id\": \"$URI\", \"media_type\": \"track\", \"enqueue\": \"replace\"}" >/dev/null
    espera $SAT playing 20 || { echo "a música não começou ($ROT)"; exit 1; }
    # pula a introdução (longa no hard rock, e baixa em quase todo estilo): começa em 25% da faixa, no mínimo 30 s,
    # mas sem passar de duração - 150 s, para a sessão inteira caber antes do fim (se não couber, a repetição cobre)
    sleep 2  # a duração da faixa anterior ainda pode estar nos atributos logo depois do play
    for _ in $(seq 10); do DUR=$(st $SAT '["attributes"].get("media_duration")'); [ "$DUR" != None ] && break; sleep 1; done
    POS=$(python3 -c "d=float('${DUR/None/0}'); print(int(min(max(0.25*d, 30), max(d-150, 30))) if d > 60 else 0)")
    [ "$POS" -gt 0 ] && call media_player/media_seek "{\"entity_id\": \"$SAT\", \"seek_position\": $POS}" >/dev/null
    sleep 3
    echo "   faixa de ${DUR}s, começando em ${POS}s"
    echo "== sessão $ROT, volume $V"
    sessao friday music "{\"style\": \"$ROT\", \"uri\": \"$URI\", \"ma_volume\": $V, \"start_s\": $POS}"
    call media_player/media_pause "{\"entity_id\": \"$SAT\"}" >/dev/null
  done
fi

if [ "$PARTE" != music ]; then
  echo "== PARTE B: 'Sexta-feira' com TV"
  call script/tv_on '{}' >/dev/null
  espera $TV on 60 || { echo "a TV não ligou em 60 s"; exit 1; }
  call media_player/volume_set "{\"entity_id\": \"$TV\", \"volume_level\": 0.3}" >/dev/null; sleep 3
  TVV=$(st $TV '["attributes"].get("volume_level")'); APP=$(st sensor.tv_app_atual '["state"]')
  echo "   TV ligada; volume $TVV; app/canal: $APP"
  python3 -c "import sys; sys.exit(0 if abs(float('$TVV') - 0.3) < 0.011 else 1)" || { echo "volume da TV não ficou em 30"; exit 1; }
  for r in 1 2; do
    echo "== sessão TV $r/2"
    sessao sexta_feira tv "{\"tv_volume\": $TVV, \"tv_app\": \"$APP\"}"
  done
fi

trap - EXIT; restaurar

echo "== nota do modelo atual em cada sessão (Pi, uma por vez, com teto de CPU)"
$SSH "mkdir -p /tmp/gsess"
scp -q -i /data/.ssh/id_ed25519 "$HERE/../wakeword-analysis/rescore.py" "$PI:/tmp/gsess/"
for f in "${GRAVADOS[@]}"; do
  case "$f" in *tentativas_sexta_feira_*) M=sexta_feira;; *) M=friday;; esac
  $SSH "sudo systemd-run --scope -q -p MemoryMax=450M -p CPUQuota=120% nice -n 19 sudo -u jose \
    ~/oww/bin/python /tmp/gsess/rescore.py /home/jose/modelos/$M.onnx /tmp/gsess/s.ndjson $f && cat /tmp/gsess/s.ndjson ${f%.wav}.json" \
  | python3 "$HERE/score_sessions.py"
done
$SSH "rm -rf /tmp/gsess /tmp/pi_guided_session.py /tmp/msess_*.wav"
echo "== ${#GRAVADOS[@]} sessões gravadas em ~/gravacoes da Pi (entram no próximo harvest)"
