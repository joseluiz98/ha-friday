#!/usr/bin/env bash
# F.R.I.D.A.Y. em produção (spec 2026-10-18): OpenClaw (gateway) + bot Pipecat/Gemini Live + terminador TLS.
# Estado persistente em $BASE (/srv/friday na VM); o código do bot fica em $BASE/app para iterar sem rebuild da
# imagem. Segredos em arquivo, em $BASE/secrets (600); nada de Supervisor.
set -uo pipefail

BASE="${FRIDAY_BASE:-/srv/friday}"
APP="$BASE/app"
GW_PORT=18789
BOT_PORT=7860
TLS_PORT=7443
LOCAL_ENV="${FRIDAY_LOCAL_CONFIG:-/srv/friday/state/local.env}"   # valores da casa (fora do repo): FRIDAY_VM_IP, FRIDAY_HA_URL...
[ -f "$LOCAL_ENV" ] && . "$LOCAL_ENV"
HOST_IP=${FRIDAY_VM_IP:?FRIDAY_VM_IP missing from the local config}

# HOME pelo apelido legado do compose (FRIDAY_OC_HOME, o mesmo diretório que $BASE/home): o OpenClaw guarda no
# SQLite chaves derivadas do HOME (cron_jobs.store_key), que precisam continuar iguais às de antes da migração.
export HOME="${FRIDAY_OC_HOME:-$BASE/home}"
mkdir -p "$HOME" "$BASE/certs" "$APP"

log() { echo "[friday $(date +%H:%M:%S)] $*"; }

# Overrides sem rebuild (FRIDAY_OPENCLAW_MODEL, FRIDAY_GEMINI_MODEL, FRIDAY_VOICE).
if [ -f "$BASE/friday.env" ]; then
  set -a
  . "$BASE/friday.env"
  set +a
fi

ler_segredo() { cat "$BASE/secrets/$1" 2>/dev/null; }

# Chave da VOZ (Gemini Live, bot Pipecat). O cérebro do OpenClaw usa só as credenciais que o dono
# configurar no onboarding dele; esta chave não é repassada ao processo do OpenClaw.
GEMINI_API_KEY="$(ler_segredo gemini_api_key)"
if [ -z "$GEMINI_API_KEY" ]; then
  log "falta $BASE/secrets/gemini_api_key"
  exit 1
fi

# Chave da própria F.R.I.D.A.Y. no Home Assistant (usuário administrador dela): bot, cérebro e mcporter a leem
# pela variável FRIDAY_HA_TOKEN. Nunca é impressa.
FRIDAY_HA_TOKEN="$(ler_segredo ha_token)"
if [ -z "$FRIDAY_HA_TOKEN" ]; then
  log "falta $BASE/secrets/ha_token"
  exit 1
fi
export FRIDAY_HA_TOKEN FRIDAY_HA_URL="${FRIDAY_HA_URL:?FRIDAY_HA_URL missing from the local config}"

# Atalhos para as pastas do Samba que não estão na lista fixa da imagem (pasta nova no HAOS = atalho na próxima
# partida). Pula o apelido do HOME. Não espera o Samba: com ele fora, desiste em 5 s.
[ -f /haos_links.sh ] && bash /haos_links.sh /share /mnt/haos/share "$(basename "$(dirname "$HOME")")"

# O onboarding é do dono: docker exec -it friday openclaw onboard (ou o atalho `oc` da VM)
# (modo local, gateway loopback, porta 18789, auth por token). Sem configuração, o container para.
OC_CONF="$HOME/.openclaw/openclaw.json"
if [ ! -f "$OC_CONF" ]; then
  log "OpenClaw sem configuração em $OC_CONF"
  exit 1
fi

# A voz fala com o gateway usando o token que o dono escolheu no onboarding.
OPENCLAW_GATEWAY_TOKEN="$(jq -r '.gateway.auth.token // empty | strings' "$OC_CONF" 2>/dev/null)"
if [ -z "$OPENCLAW_GATEWAY_TOKEN" ]; then
  log "a config do OpenClaw não tem token de gateway em texto (escolha auth por token no onboarding); aguardando."
  exec sleep infinity
fi
export OPENCLAW_GATEWAY_TOKEN

# Com gateway.tls.enabled (pareamento do app iOS com acesso completo), o listener do gateway só fala
# HTTPS/WSS, inclusive em loopback. O TLS usa o mesmo certificado da Playground (abaixo).
GW_SCHEME=http
if [ "$(jq -r '.gateway.tls.enabled // false' "$OC_CONF" 2>/dev/null)" = "true" ]; then
  GW_SCHEME=https
fi

# Único ajuste do container na config do OpenClaw: o endpoint compatível com OpenAI, que a voz usa
# para chamar o cérebro. Modelo, skills e política ficam com o onboarding do dono.
openclaw config set gateway.http.endpoints.chatCompletions.enabled true >/dev/null 2>&1 \
  || log "não consegui ligar gateway.http.endpoints.chatCompletions"
if [ -n "${FRIDAY_OPENCLAW_MODEL:-}" ]; then
  openclaw config set agents.defaults.model.primary "$FRIDAY_OPENCLAW_MODEL" >/dev/null 2>&1
  log "cérebro forçado por friday.env: $FRIDAY_OPENCLAW_MODEL"
fi

# Chave do Gemini também para o gateway: a busca de memória usa embeddings do Gemini
# (memory.search.provider=gemini), que leem GEMINI_API_KEY.
export GEMINI_API_KEY

# Reconstrução do índice de memória sob demanda: criar $BASE/reindex-memory.flag e reiniciar o container.
# Roda aqui dentro porque o índice vetorial precisa das dependências nativas do container.
if [ -f "$BASE/reindex-memory.flag" ]; then
  log "reconstruindo o índice de memória (chama o provedor de embedding)"
  if openclaw memory status --index --agent "${FRIDAY_OPENCLAW_AGENT:-friday}" > >(sed -u 's/^/[memory] /') 2>&1; then
    rm -f "$BASE/reindex-memory.flag"
    log "índice de memória reconstruído"
  else
    log "falha ao reconstruir o índice de memória; o sinal fica para a próxima tentativa"
  fi
fi

# Entrada do MCP do HA para a skill mcporter (vale só se o dono habilitar a skill). O token é
# resolvido do ambiente na hora da chamada, não fica em disco.
mcporter config add homeassistant --url "$FRIDAY_HA_URL/api/mcp" \
  --header 'Authorization=Bearer ${FRIDAY_HA_TOKEN}' --scope home >/dev/null 2>&1 || true

# Certificado autoassinado para o navegador liberar o microfone na LAN. O gateway usa o mesmo
# (gateway.tls.certPath/keyPath): apagar e regenerar muda a impressão digital e obriga a parear o
# app do iPhone de novo.
CERT="$BASE/certs/poc.crt"
KEY="$BASE/certs/poc.key"
if [ ! -s "$CERT" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout "$KEY" -out "$CERT" \
    -subj "/CN=friday" -addext "subjectAltName=IP:$HOST_IP,DNS:friday.lan" >/dev/null 2>&1
  chmod 600 "$KEY"
fi

# Cache de compilação dos módulos do Node: recomendação oficial do OpenClaw para hosts fracos
# (docs/install/raspberry-pi.md e docs/vps.md). O gateway descarta as threads auxiliares depois de
# 60 s paradas e, sem cache, cada uma recompila o JavaScript do zero na próxima pergunta (medido em
# 01/10: 29 s fria contra 4 s quente). Fica em /data do container ($BASE/cache na VM): é regenerável
# e se refaz sozinho se sumir.
export NODE_COMPILE_CACHE=/data/node-compile-cache
mkdir -p "$NODE_COMPILE_CACHE"
# Reinícios de rotina do gateway (mudança de configuração, recuperação) ficam no mesmo processo:
# sem isso o OpenClaw pode passar o reinício a um processo novo, e o PID acompanhado aqui some.
# Recomendação oficial para hosts fracos, sugerida pelo próprio `openclaw doctor` em 01/10.
export OPENCLAW_NO_RESPAWN=1

# Tarefas avulsas do CLI que precisam do container, com o gateway ainda parado (avulsos.sh).
AVULSOS="$BASE/antes-do-gateway"
mkdir -p "$AVULSOS"
. /avulsos.sh
rodar_avulsos

# Gateway do OpenClaw (loopback), religado pelo laço de gateway_loop.sh se sair.
GW_PID_FILE=/tmp/openclaw-gateway.pid
. /gateway_loop.sh
# Partida do zero: processo novo, então a anotação de reinício no mesmo processo deixa de valer.
mkdir -p "$(dirname "$GW_MARCA_REINICIO")"
rm -f "$GW_MARCA_REINICIO"
manter_gateway &
GW_LOOP_PID=$!
for _ in $(seq 1 90); do
  curl -fsk -o /dev/null -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" \
    "$GW_SCHEME://127.0.0.1:$GW_PORT/v1/models" && break
  sleep 2
done
log "gateway do OpenClaw no ar em $GW_SCHEME://127.0.0.1:$GW_PORT"

# Bot Pipecat (HTTP só em loopback) + TLS na frente.
export FRIDAY_GW_URL="$GW_SCHEME://127.0.0.1:$GW_PORT"
if [ ! -f "$APP/bot.py" ]; then
  log "falta $APP/bot.py; aguardando"
  exec sleep infinity
fi
cd "$APP"
GEMINI_API_KEY="$GEMINI_API_KEY" \
  /opt/pc/bin/python "$APP/bot.py" -t webrtc --host 127.0.0.1 --port "$BOT_PORT" \
  > >(sed -u 's/^/[bot] /') 2>&1 &
BOT_PID=$!
socat "OPENSSL-LISTEN:$TLS_PORT,reuseaddr,fork,cert=$CERT,key=$KEY,verify=0" "TCP:127.0.0.1:$BOT_PORT" &
TLS_PID=$!

# Parada pedida pelo docker (SIGTERM): encerra os filhos e sai com 0.
# O gateway é esperado até concluir o próprio desligamento (parar_gateway, em gateway_loop.sh).
stop_all() {
  log "parada solicitada; encerrando"
  parar_gateway "$GW_LOOP_PID"
  kill "$BOT_PID" "$TLS_PID" 2>/dev/null
  wait
  exit 0
}
trap stop_all TERM INT

log "pronto: abra https://$HOST_IP:$TLS_PORT/client/ e aceite o aviso do certificado"
# O laço do gateway só termina na parada; se o bot ou o TLS cair, o container encerra como antes.
wait -n "$GW_LOOP_PID" "$BOT_PID" "$TLS_PID"
log "um processo terminou; encerrando o container"
parar_gateway "$GW_LOOP_PID"
kill "$BOT_PID" "$TLS_PID" 2>/dev/null
exit 1
