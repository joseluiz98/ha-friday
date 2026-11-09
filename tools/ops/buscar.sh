#!/bin/sh
# Busca na memória do cérebro da F.R.I.D.A.Y. (agente `friday`) pelo Claude Code.
# Uso: buscar.sh "<consulta>" [n=5]
# Imprime, para cada resultado, `caminho:linhas (nota)` e o trecho. Roda `oc memory search` na VM por ssh; a consulta
# passa por `printf %q` (nunca é interpolada crua). Não guarda nenhum segredo no HA. Spec 2026-10-21 §5.3.
set -eu
if [ $# -lt 1 ] || [ -z "$1" ]; then
  echo 'uso: buscar.sh "<consulta>" [n=5]' >&2
  exit 2
fi
consulta=$1
n=${2:-5}
case "$n" in ''|*[!0-9]*) echo "n precisa ser um número" >&2; exit 2 ;; esac
q=$(printf '%q' "$consulta" 2>/dev/null || true)
if [ -z "$q" ] && [ -n "$consulta" ]; then
  # sh sem %q (BusyBox): escapa com aspas simples
  q="'$(printf '%s' "$consulta" | sed "s/'/'\\\\''/g")'"
fi
ssh -o BatchMode=yes "$(cat /config/.friday/vm)" \
  "sudo oc memory search --agent friday --json --max-results $n $q 2>/dev/null" |
python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    resultados = json.loads(raw).get("results", [])
except ValueError:
    sys.exit("resposta da busca não é JSON")
if not resultados:
    print("(nenhum resultado)")
for r in resultados:
    print("%s:%s-%s (nota %.2f)" % (r.get("path"), r.get("startLine"), r.get("endLine"), r.get("score") or 0))
    print((r.get("snippet") or "").rstrip())
    print("---")
'
