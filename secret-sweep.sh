#!/usr/bin/env bash
# Falha (exit 1) se achar segredo no working tree (exceto .git, .superpowers, este script e arquivos .example).
# BusyBox-compatível: o grep do HAOS não suporta --exclude-dir/--include (GNU),
# o que silenciosamente quebrava a varredura. Aqui usamos -rEnI + filtro por grep -v.
set -euo pipefail
cd "$(dirname "$0")"
PATTERN='Bearer [A-Za-z0-9._-]{20,}|amzn1\.ask\.account\.[A-Z0-9]{40,}|eyJ[A-Za-z0-9._-]{20,}|"token"[[:space:]]*:[[:space:]]*"[^"]{16,}"|senha_porta_local|PVEAPIToken'
# -r recursivo, -E regex estendida, -n nº de linha, -I pula binários (ex.: .git/objects).
HITS=$(grep -rEnI "$PATTERN" . 2>/dev/null | grep -v '/\.git/' | grep -v '^\./\.superpowers/' | grep -v '^\./secret-sweep\.sh:' | grep -v '\.example\.' || true)
if [ -n "$HITS" ]; then
  echo "❌ Possível segredo encontrado:"
  echo "$HITS"
  exit 1
fi
echo "✅ Sweep limpo."
