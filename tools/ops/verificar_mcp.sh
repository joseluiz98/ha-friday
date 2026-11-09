#!/usr/bin/env bash
# Confere o que a voz enxerga pelo MCP do HA: o inventário da casa (prompt "Assist") e as ferramentas.
# Uso: bash verificar_mcp.sh "Luz Escritório" "sem:Office Lights" "ferramenta:script__tv_netflix" ...
#   texto -> precisa aparecer no inventário; sem:texto -> não pode aparecer; ferramenta:nome -> precisa existir.
set -uo pipefail
U=http://supervisor/core/api/mcp
H=(-H "Authorization: Bearer $SUPERVISOR_TOKEN" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream")
inv=$(curl -s "${H[@]}" "$U" -d '{"jsonrpc":"2.0","id":1,"method":"prompts/get","params":{"name":"Assist"}}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(m["content"]["text"] for m in d["result"]["messages"]))')
fer=$(curl -s "${H[@]}" "$U" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 -c 'import json,sys; print("\n".join(t["name"] for t in json.load(sys.stdin)["result"]["tools"]))')
echo "inventário da casa: $(printf %s "$inv" | wc -c) caracteres; ferramentas: $(printf '%s\n' "$fer" | grep -c .)"
falhou=0
for alvo in "$@"; do
  case "$alvo" in
    ferramenta:*) printf '%s\n' "$fer" | grep -qxF "${alvo#ferramenta:}" && echo "ok   - $alvo" || { echo "FALHA - $alvo"; falhou=1; } ;;
    sem:*) if printf '%s' "$inv" | grep -qF "${alvo#sem:}"; then echo "FALHA - $alvo (ainda aparece)"; falhou=1; else echo "ok   - $alvo"; fi ;;
    *) printf '%s' "$inv" | grep -qF "$alvo" && echo "ok   - $alvo" || { echo "FALHA - $alvo"; falhou=1; } ;;
  esac
done
exit $falhou
