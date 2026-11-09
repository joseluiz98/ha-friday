#!/usr/bin/env bash
# Produção (spec 2026-10-18): sem Supervisor, sem POC, segredos em arquivo. bash /opt/friday/image/test_run_env.sh
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"; falhou=0
export FRIDAY_LOCAL_CONFIG=/nonexistent FRIDAY_VM_IP=192.0.2.20   # 09/11: valores da casa vêm da configuração local
ok() { echo "ok   - $1"; }; erro() { echo "FALHA - $1"; falhou=1; }
for f in run.sh gateway_loop.sh avulsos.sh Dockerfile oc; do
  [ -f "$DIR/$f" ] || { erro "$f não existe"; continue; }
  for p in SUPERVISOR supervisor secrets.yaml openclaw-poc POC_; do
    if grep -q -- "$p" "$DIR/$f"; then erro "$f contém $p"; else ok "$f sem $p"; fi
  done
done
grep -q 'HOST_IP=${FRIDAY_VM_IP:?' "$DIR/run.sh" && ok "IP da VM (configuração local)" || erro "run.sh sem HOST_IP vindo de FRIDAY_VM_IP"
grep -q 'chromium' "$DIR/Dockerfile" && ok "chromium" || erro "Dockerfile sem chromium"
grep -q 'openssh-client' "$DIR/Dockerfile" && ok "ssh" || erro "Dockerfile sem openssh-client"
grep -q 'TZ: America/Sao_Paulo' "$DIR/docker-compose.yml" && ok "fuso" || erro "compose sem TZ"
grep -q 'target: /share/openclaw-poc' "$DIR/docker-compose.yml" && ok "apelido legado" || erro "compose sem o apelido /share/openclaw-poc"
if ! grep -q 'FRIDAY_BASE' "$DIR/run.sh"; then
  erro "run.sh ainda é o original: não executo (leria segredos reais)"
else
  T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
  out="$(FRIDAY_BASE="$T" timeout 10 bash "$DIR/run.sh" 2>&1)"; rc=$?
  { [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'secrets/gemini_api_key'; } && ok "sem chave do Gemini: erro claro" || erro "sem chave do Gemini: rc=$rc"
  mkdir -p "$T/secrets"; echo x > "$T/secrets/gemini_api_key"
  out="$(FRIDAY_BASE="$T" timeout 10 bash "$DIR/run.sh" 2>&1)"; rc=$?
  { [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'secrets/ha_token'; } && ok "sem chave do HA: erro claro" || erro "sem chave do HA: rc=$rc"
fi
exit $falhou
