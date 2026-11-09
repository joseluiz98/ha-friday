#!/usr/bin/env bash
# Teste do laço que religa o gateway (gateway_loop.sh), com um "openclaw" falso.
#   bash /opt/friday/image/test_gateway_loop.sh
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
falhou=0
export FRIDAY_HA_URL="${FRIDAY_HA_URL:-http://192.0.2.10:8123}"   # 09/11: o laço exige o endereço do HA (configuração local)
ok() { echo "ok   - $1"; }
erro() { echo "FALHA - $1"; falhou=1; }

# "openclaw" falso: registra cada partida e sai depois de 0,2 s com código 3.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/openclaw" <<'EOF'
#!/bin/sh
echo "partida" >> "$PARTIDAS"
sleep 0.2
exit 3
EOF
chmod +x "$TMP/bin/openclaw"
# O sed do BusyBox (add-on SSH) não tem -u; a imagem do add-on (Debian) tem. Só para o teste.
cat > "$TMP/bin/sed" <<'EOF'
#!/bin/sh
[ "$1" = "-u" ] && shift
exec /bin/sed "$@"
EOF
chmod +x "$TMP/bin/sed"

export PATH="$TMP/bin:$PATH" PARTIDAS="$TMP/partidas" GW_PORT=1 GW_PID_FILE="$TMP/gw.pid"
export GW_ESPERA_BASE=0.1 GW_ESPERA_MAX=0.3
log() { echo "[teste] $*" >> "$TMP/log"; }
export -f log
# Remendo e chave falsos (spec 2026-10-11 §4.1): sem isto o laço chamaria o curl de verdade.
cat > "$TMP/bin/remendo_falso" <<'EOF'
#!/bin/sh
echo "$2" >> "$REMENDO_LOG"
if [ "$2" = aplicar ] && [ "${REMENDO_FALHA:-0}" = 1 ]; then echo "assinatura diferente"; exit 1; fi
echo "ok $2"
EOF
cat > "$TMP/bin/curl" <<'EOF'
#!/bin/sh
echo "$@" >> "${CURL_ARGS:-/dev/null}"
[ "${CHAVE_FALSA:-off}" = falha ] && exit 7
printf '{"entity_id":"input_boolean.friday_cerebro_sempre_quente","state":"%s"}' "${CHAVE_FALSA:-off}"
EOF
chmod +x "$TMP/bin/remendo_falso" "$TMP/bin/curl"
export GW_REMENDO="$TMP/bin/remendo_falso" REMENDO_LOG="$TMP/remendo.log" GW_DIST="$TMP/dist"

# 1. Religa o gateway depois que ele sai.
( . "$DIR/gateway_loop.sh"; manter_gateway ) &
LOOP=$!
sleep 1.5
kill "$LOOP" 2>/dev/null; wait "$LOOP" 2>/dev/null
n=$(wc -l < "$TMP/partidas" 2>/dev/null || echo 0)
[ "$n" -ge 3 ] && ok "religou o gateway ($n partidas em 1,5 s)" || erro "esperava >= 3 partidas, teve $n"

# 2. Registra o motivo no log.
grep -q "gateway saiu (código 3)" "$TMP/log" && ok "log com o código de saída" || erro "log sem o código de saída"

# 3. A espera cresce e respeita o teto.
grep -q "religando em 0.3 s" "$TMP/log" && ok "espera chega ao teto" || erro "espera não chegou ao teto de 0.3 s"
! grep -q -E "religando em (0\.[4-9]|[1-9])" "$TMP/log" && ok "espera nunca passa do teto" || erro "espera passou do teto"

# 4. Grava o PID do gateway atual (o stop_all do run.sh usa).
[ -s "$TMP/gw.pid" ] && ok "PID do gateway gravado" || erro "PID do gateway não gravado"

# 5. A parada espera o gateway concluir o próprio desligamento e o laço não o religa (defeito de 01/10:
#    o run.sh saía sem esperar e o gateway morria no meio do desligamento).
mkdir -p "$TMP/bin2"
cat > "$TMP/bin2/openclaw" <<'EOF'
#!/bin/sh
echo "partida" >> "$PARTIDAS2"
trap 'sleep 1; echo limpo > "$LIMPO"; exit 0' TERM
while :; do sleep 0.1; done
EOF
chmod +x "$TMP/bin2/openclaw"
export PARTIDAS2="$TMP/partidas2" LIMPO="$TMP/limpo" GW_PID_FILE="$TMP/gw2.pid" GW_PARADA_MAX=4
rm -f "$TMP/gw2.pid"
( export PATH="$TMP/bin2:$PATH"; . "$DIR/gateway_loop.sh"; manter_gateway ) &
LOOP2=$!
for _ in $(seq 1 30); do [ -s "$TMP/gw2.pid" ] && break; sleep 0.1; done
sleep 0.3
t0=$(date +%s)
( . "$DIR/gateway_loop.sh"; parar_gateway "$LOOP2" )
pid2=$(cat "$TMP/gw2.pid")
[ -f "$TMP/limpo" ] && ok "parada esperou o desligamento limpo do gateway" || erro "parada voltou antes de o gateway concluir"
! kill -0 "$pid2" 2>/dev/null && ok "gateway encerrado" || erro "gateway ainda vivo"
sleep 1
[ "$(wc -l < "$TMP/partidas2")" -eq 1 ] && ok "laço não religou depois da parada" || erro "laço religou o gateway"

# 6. Se o gateway não sai no prazo, a parada força.
mkdir -p "$TMP/bin3"
cat > "$TMP/bin3/openclaw" <<'EOF'
#!/bin/sh
trap '' TERM
while :; do sleep 0.1; done
EOF
chmod +x "$TMP/bin3/openclaw"
export GW_PID_FILE="$TMP/gw3.pid" GW_PARADA_MAX=1
( export PATH="$TMP/bin3:$PATH"; . "$DIR/gateway_loop.sh"; manter_gateway ) &
LOOP3=$!
for _ in $(seq 1 30); do [ -s "$TMP/gw3.pid" ] && break; sleep 0.1; done
( . "$DIR/gateway_loop.sh"; parar_gateway "$LOOP3" )
sleep 0.3
! kill -0 "$(cat "$TMP/gw3.pid")" 2>/dev/null && ok "forçou o gateway que não saía" || erro "gateway teimoso continua vivo"
grep -q "forçando" "$TMP/log" && ok "registrou que forçou" || erro "não registrou que forçou"

# 7. Prefixa o log e anota o reinício no mesmo processo.
export GW_MARCA_REINICIO="$TMP/estado/reinicio-no-mesmo-processo"
mkdir -p "$TMP/estado"
saida=$(printf 'linha comum\n[gateway] restart mode: in-process restart (OPENCLAW_NO_RESPAWN)\n' | ( . "$DIR/gateway_loop.sh"; prefixar_gateway ))
echo "$saida" | grep -q '^\[openclaw\] linha comum$' && ok "prefixa o log do gateway" || erro "log sem prefixo"
[ -s "$GW_MARCA_REINICIO" ] && ok "anotou o reinício no mesmo processo" || erro "não anotou o reinício"

# 8. Sem reinício no mesmo processo, nada é anotado.
rm -f "$GW_MARCA_REINICIO"
printf 'linha comum\n[gateway] ready\n' | ( . "$DIR/gateway_loop.sh"; prefixar_gateway ) > /dev/null
[ ! -e "$GW_MARCA_REINICIO" ] && ok "sem anotação quando não houve reinício" || erro "anotou sem reinício"

# 9. Cada linha sai na hora, com o gateway ainda escrevendo (defeito de 01/10: o awk da imagem é o mawk,
#    que segura a entrada de um pipe até encher o buffer; o log parou e a anotação não foi gravada).
#    Usa o mawk quando existe, como na imagem (no add-on SSH: apk add mawk).
mkdir -p "$TMP/bin4"
command -v mawk >/dev/null && ln -sf "$(command -v mawk)" "$TMP/bin4/awk"
rm -f "$GW_MARCA_REINICIO"
( printf 'linha comum\n[gateway] restart mode: in-process restart (OPENCLAW_NO_RESPAWN)\n'; sleep 3 ) \
  | ( export PATH="$TMP/bin4:$PATH"; . "$DIR/gateway_loop.sh"; prefixar_gateway ) > "$TMP/saida9" &
ESCRITOR=$!
sleep 1.5
grep -q 'restart mode' "$TMP/saida9" && ok "linha sai na hora com o pipe aberto" || erro "linha presa no buffer com o pipe aberto"
[ -s "$GW_MARCA_REINICIO" ] && ok "anota na hora com o pipe aberto" || erro "anotação presa no buffer com o pipe aberto"
wait "$ESCRITOR" 2>/dev/null

# 10–12. A chave no HA decide o remendo antes de cada partida (spec 2026-10-11 §4.1, D2: na dúvida, sem remendo).
for caso in "on aplicar" "off reverter" "falha reverter"; do
  set -- $caso
  : > "$REMENDO_LOG"
  ( export CHAVE_FALSA="$1"; . "$DIR/gateway_loop.sh"; preparar_remendo )
  [ "$(cat "$REMENDO_LOG")" = "$2" ] && ok "chave $1 -> remendo $2" || erro "chave $1: esperava $2, teve '$(cat "$REMENDO_LOG")'"
done
grep -q "\[remendo-pool\] chave=on: ok aplicar" "$TMP/log" && ok "log registra a chave e o resultado" || erro "log sem a linha do remendo"

# 13. Remendo que falha não impede a partida do gateway.
: > "$REMENDO_LOG"; : > "$TMP/partidas"
export GW_PID_FILE="$TMP/gw13.pid"
( export CHAVE_FALSA=on REMENDO_FALHA=1; . "$DIR/gateway_loop.sh"; manter_gateway ) &
LOOP13=$!
sleep 1
kill "$LOOP13" 2>/dev/null; wait "$LOOP13" 2>/dev/null
[ -s "$TMP/partidas" ] && ok "gateway sobe mesmo com o remendo falhando" || erro "remendo falho impediu a partida"
grep -q "falhou (aplicar)" "$TMP/log" && ok "log registra a falha do remendo" || erro "log sem a falha do remendo"


# Produção: chave do cérebro quente pela rede, com a chave da F.R.I.D.A.Y.; estado do remendo em arquivo (spec 2026-10-18)
export CURL_ARGS="$TMP/curl_args" FRIDAY_HA_URL="http://ha.teste:8123" FRIDAY_HA_TOKEN="tok-teste"
( unset GW_CHAVE_URL; . "$DIR/gateway_loop.sh"; ler_chave_remendo >/dev/null )
grep -q "Authorization: Bearer tok-teste" "$TMP/curl_args" && ok "chave com FRIDAY_HA_TOKEN" || erro "cabeçalho sem FRIDAY_HA_TOKEN"
grep -q "http://ha.teste:8123/api/states/input_boolean.friday_cerebro_sempre_quente" "$TMP/curl_args" && ok "URL da chave" || erro "URL errada"
m="$( unset GW_MARCA_REINICIO; . "$DIR/gateway_loop.sh"; echo "$GW_MARCA_REINICIO" )"
[ "$m" = "/srv/friday/state/restarted-in-process" ] && ok "marca no lugar novo" || erro "marca em $m"
export GW_ESTADO_QUENTE="$TMP/warm-brain.json" CHAVE_FALSA=on
( . "$DIR/gateway_loop.sh"; preparar_remendo )
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["key"]=="on" and d["patched"] is True and d["at"]' "$TMP/warm-brain.json" \
  && ok "estado do cérebro quente gravado" || erro "warm-brain.json ausente ou errado"

exit $falhou
