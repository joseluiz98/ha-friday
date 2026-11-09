#!/bin/sh
# Habilidades ensinadas à voz da Friday: lê e grava o registro pelas rotas do bot (spec 2026-10-23).
# Uso: sh voice_skills.sh list | tools | save <arquivo.json> | delete <id ou nome>
# A senha vem de um arquivo e nunca é impressa nem passa pela linha de comando.
set -u
uso() { echo 'uso: sh voice_skills.sh list | tools | save <arquivo.json> | delete <id ou nome>' >&2; exit 2; }
[ $# -ge 1 ] || uso
URL="${VOICE_SKILLS_URL:-http://127.0.0.1:7860/voice_skills}"
TOKEN_ARQ="${VOICE_SKILLS_TOKEN:-/srv/friday/state/canal_casa.token}"
cmd="$1"
case "$cmd" in
  list|tools) [ $# -eq 1 ] || uso ;;
  save) [ $# -eq 2 ] && [ -r "$2" ] || uso ;;
  delete) [ $# -eq 2 ] && [ -n "$2" ] || uso ;;
  *) uso ;;
esac
hdr=$(mktemp) || exit 2
trap 'rm -f "$hdr"' EXIT
chmod 600 "$hdr"
printf 'Authorization: Bearer %s\n' "$(cat "$TOKEN_ARQ" 2>/dev/null)" > "$hdr"
fora='{"ok": false, "erro": "bot da Friday fora do ar"}'
case "$cmd" in
  list) curl -s -m 30 -H @"$hdr" "$URL" || echo "$fora" ;;
  tools) curl -s -m 30 -H @"$hdr" "$URL/tools" || echo "$fora" ;;
  save) curl -s -m 30 -X POST -H "Content-Type: application/json" -H @"$hdr" --data-binary @"$2" "$URL" || echo "$fora" ;;
  delete)
    chave=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$2") || exit 2
    curl -s -m 30 -X DELETE -H @"$hdr" "$URL/$chave" || echo "$fora" ;;
esac
echo
