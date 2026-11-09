#!/bin/sh
# Entrega uma mensagem pela voz da casa (satélite) e imprime o resultado em JSON (spec 2026-10-12 §3).
# Uso: sh entregar.sh <jose | satelite:sat-xxxxxx> <lembrete | aviso> "<texto>"
# A senha do /deliver vem de um arquivo e nunca é impressa nem passa pela linha de comando.
set -u
[ $# -eq 3 ] || { echo 'uso: sh entregar.sh <jose|satelite:ID> <lembrete|aviso> "<texto>"' >&2; exit 2; }
URL="${FALAR_URL:-http://127.0.0.1:7860/deliver}"
TOKEN_ARQ="${FALAR_TOKEN:-/srv/friday/state/canal_casa.token}"
corpo=$(python3 -c 'import json, sys; print(json.dumps({"destino": sys.argv[1], "tipo": sys.argv[2], "texto": sys.argv[3]}))' "$1" "$2" "$3") || exit 2
hdr=$(mktemp) || exit 2
trap 'rm -f "$hdr"' EXIT
chmod 600 "$hdr"
printf 'Authorization: Bearer %s\n' "$(cat "$TOKEN_ARQ" 2>/dev/null)" > "$hdr"
# A espera inclui a Pi abrir a conversa (até 90 s) e a conversa terminar (até 5 min).
if ! curl -s -m 420 -X POST -H "Content-Type: application/json" -H @"$hdr" --data-binary "$corpo" "$URL"; then
  echo '{"resultado": "erro", "erro": "bot da Friday fora do ar"}'
fi
echo
