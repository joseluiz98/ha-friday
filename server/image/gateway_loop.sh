# Religa só o gateway do OpenClaw quando ele sai (01/10). Antes, qualquer saída dele derrubava o
# container inteiro (run.sh: wait -n) e, sem vigia externo, ficava fora até alguém
# religar à mão (incidente de 01/10 20:37: reinício que falhou ao desligar).
# Saídas seguidas com vida curta contam como falhas em série: a espera cresce até GW_ESPERA_MAX.
# Testes: bash /opt/friday/image/test_gateway_loop.sh
GW_ESPERA_BASE="${GW_ESPERA_BASE:-15}"
GW_ESPERA_MAX="${GW_ESPERA_MAX:-300}"
GW_VIDA_ESTAVEL="${GW_VIDA_ESTAVEL:-300}"
# Tempo para o gateway concluir o próprio desligamento na parada. O docker dá 30 s ao container
# (stop_grace_period do compose); um desligamento limpo leva ~1,5–2 s.
GW_PARADA_MAX="${GW_PARADA_MAX:-8}"
# Anotação de reinício no mesmo processo (OPENCLAW_NO_RESPAWN). Esse reinício deixa a memória presa ao
# plugin antigo do Google (01/10: sync falhando até um religamento completo). A porta de status da VM expõe a
# anotação (restarted_in_process) e o HA religa o container às 01:40, antes do sonho; o run.sh apaga a anotação a
# cada partida do zero.
GW_MARCA_REINICIO="${GW_MARCA_REINICIO:-/srv/friday/state/restarted-in-process}"

# Remendo do pool de threads (spec 2026-10-11-friday-latencia-voz §4.1): antes de cada partida do gateway, a chave
# input_boolean.friday_cerebro_sempre_quente no HA decide se o pool fica sem fechar threads ociosas.
GW_DIST="${GW_DIST:-/usr/local/lib/node_modules/openclaw/dist}"
GW_REMENDO="${GW_REMENDO:-/remendo_pool.sh}"
GW_CHAVE_URL="${GW_CHAVE_URL:-${FRIDAY_HA_URL:?FRIDAY_HA_URL missing}/api/states/input_boolean.friday_cerebro_sempre_quente}"
# Estado da última decisão (chave, remendo aplicado ou não, quando): a porta de status lê este arquivo.
GW_ESTADO_QUENTE="${GW_ESTADO_QUENTE:-/srv/friday/state/warm-brain.json}"

# "on" só com prova: HA fora do ar, entidade inexistente ou qualquer outro estado = "off" (D2).
ler_chave_remendo() {
  local estado
  estado="$(curl -fsS -m 5 -H "Authorization: Bearer ${FRIDAY_HA_TOKEN:-}" "$GW_CHAVE_URL" 2>/dev/null \
    | jq -r '.state // empty' 2>/dev/null)"
  [ "$estado" = "on" ] && echo on || echo off
}

preparar_remendo() {
  local chave acao saida aplicado=false
  chave="$(ler_chave_remendo)"
  acao=reverter
  [ "$chave" = on ] && acao=aplicar
  if saida="$(bash "$GW_REMENDO" "$GW_DIST" "$acao" 2>&1)"; then
    log "[remendo-pool] chave=$chave: $saida"
    [ "$acao" = aplicar ] && aplicado=true
  else
    log "[remendo-pool] chave=$chave: falhou ($acao): $saida; o gateway sobe sem remendo"
  fi
  # Falha ao gravar o estado nunca derruba o laço.
  printf '{"key": "%s", "patched": %s, "at": "%s"}\n' "$chave" "$aplicado" "$(date -Iseconds)" \
    > "$GW_ESTADO_QUENTE" 2>/dev/null || true
}

# Prefixa o log do gateway e anota o reinício no mesmo processo. Laço do bash, linha a linha: o awk da
# imagem é o mawk, que segura a entrada de um pipe até encher o buffer (01/10: o log parou e a anotação
# não saiu depois de um reinício no mesmo processo).
prefixar_gateway() {
  local linha
  while IFS= read -r linha || [ -n "$linha" ]; do
    printf '[openclaw] %s\n' "$linha"
    case "$linha" in
      *"restart mode: in-process restart"*) date "+%Y-%m-%dT%H:%M:%S%z" > "$GW_MARCA_REINICIO" ;;
    esac
  done
}

manter_gateway() {
  local falhas=0 ini pid rc espera
  while :; do
    preparar_remendo
    ini=$(date +%s)
    NODE_OPTIONS="--max-old-space-size=1024" openclaw gateway --port "$GW_PORT" \
      > >(prefixar_gateway) 2>&1 &
    pid=$!
    echo "$pid" > "$GW_PID_FILE"
    wait "$pid"
    rc=$?
    if [ $(( $(date +%s) - ini )) -lt "$GW_VIDA_ESTAVEL" ]; then
      falhas=$((falhas + 1))
    else
      falhas=1
    fi
    espera=$(awk -v b="$GW_ESPERA_BASE" -v n="$falhas" -v m="$GW_ESPERA_MAX" \
      'BEGIN { e = b * n; if (e > m) e = m; printf "%g", e }')
    log "gateway saiu (código $rc); religando em ${espera} s (saída seguida nº $falhas)"
    sleep "$espera"
  done
}

gateway_atual() { cat "$GW_PID_FILE" 2>/dev/null; }

# Para o gateway e espera ele concluir o próprio desligamento. Defeito corrigido em 01/10: o run.sh
# esperava só os filhos diretos (o gateway é filho do laço), saía, e o container acabava com o gateway no
# meio do desligamento (nenhuma parada depois de 21:05 registrou "[shutdown] completed cleanly").
# $1 = PID do laço manter_gateway, encerrado primeiro para não religar o gateway.
parar_gateway() {
  local pid i limite
  [ -n "${1:-}" ] && kill "$1" 2>/dev/null
  pid="$(gateway_atual)"
  [ -n "$pid" ] || return 0
  kill "$pid" 2>/dev/null
  limite=$(awk -v s="$GW_PARADA_MAX" 'BEGIN { printf "%d", s * 10 }')
  for (( i = 0; i < limite; i++ )); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.1
  done
  log "gateway não concluiu o desligamento em ${GW_PARADA_MAX} s; forçando"
  kill -9 "$pid" 2>/dev/null
}
