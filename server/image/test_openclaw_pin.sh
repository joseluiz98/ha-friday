#!/usr/bin/env bash
# O OpenClaw é fixado em package.json + package-lock.json (o Dependabot lê esses arquivos, não o Dockerfile).
#   bash server/image/test_openclaw_pin.sh
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
falhou=0
ok() { echo "ok   - $1"; }
erro() { echo "FALHA - $1"; falhou=1; }

grep -q 'openclaw@' "$DIR/Dockerfile" && erro "Dockerfile voltou a ter openclaw@ (a versão mora no package.json)" || ok "Dockerfile sem openclaw@"
grep -q 'mcporter@' "$DIR/Dockerfile" && erro "Dockerfile voltou a ter mcporter@" || ok "Dockerfile sem mcporter@"
grep -q 'npm ci' "$DIR/Dockerfile" && ok "Dockerfile instala com npm ci" || erro "Dockerfile sem npm ci"
for dep in openclaw mcporter; do
  v="$(jq -r --arg d "$dep" '.dependencies[$d] // empty' "$DIR/package.json")"
  if [ -z "$v" ]; then erro "package.json sem $dep"
  elif [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then ok "$dep fixado em $v"
  else erro "$dep com versão não exata no package.json: $v"; fi
  l="$(jq -r --arg d "$dep" '.packages["node_modules/" + $d].version // empty' "$DIR/package-lock.json")"
  [ "$l" = "$v" ] && ok "lock de $dep confere" || erro "package-lock.json fora do package.json ($dep: '$l' != '$v')"
done
antigo='/usr/local/lib/node_modules/'openclaw  # montado em duas partes: este arquivo não pode casar consigo mesmo
grep -rq "$antigo" "$DIR"/*.sh "$DIR"/oc 2>/dev/null \
  && erro "algo ainda aponta para o caminho global antigo do npm" || ok "nada usa o caminho global antigo"

exit "$falhou"
