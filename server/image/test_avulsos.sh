#!/usr/bin/env bash
# Teste das tarefas avulsas antes do gateway (avulsos.sh).
#   bash /opt/friday/image/test_avulsos.sh
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
falhou=0
ok() { echo "ok   - $1"; }
erro() { echo "FALHA - $1"; falhou=1; }

mkdir -p "$TMP/bin"
cat > "$TMP/bin/sed" <<'EOF'
#!/bin/sh
[ "$1" = "-u" ] && shift
exec /bin/sed "$@"
EOF
chmod +x "$TMP/bin/sed"
export PATH="$TMP/bin:$PATH" AVULSOS="$TMP/avulsos"
log() { echo "[teste] $*" >> "$TMP/log"; }
mkdir -p "$AVULSOS"
echo 'echo "rodou a"' > "$AVULSOS/01-a.sh"
echo 'echo "rodou b"; exit 7' > "$AVULSOS/02-b.sh"
echo 'echo "não sou .sh"' > "$AVULSOS/notas.txt"

( . "$DIR/avulsos.sh"; rodar_avulsos ) > "$TMP/saida" 2>&1
sleep 0.3

grep -q "\[avulso\] rodou a" "$TMP/saida" && ok "roda o script e prefixa a saída" || erro "saída do script a ausente"
grep -q "rodou b" "$TMP/saida" && ok "roda todos os scripts" || erro "script b não rodou"
grep -q "02-b.sh terminou com código 7" "$TMP/log" && ok "registra o código de saída" || erro "código de saída não registrado"
[ -f "$AVULSOS/01-a.sh.feito" ] && [ ! -f "$AVULSOS/01-a.sh" ] && ok "renomeia para .feito (roda uma vez só)" || erro "não renomeou"
[ -f "$AVULSOS/notas.txt" ] && ! grep -q "não sou" "$TMP/saida" && ok "ignora o que não é .sh" || erro "rodou arquivo que não é .sh"

# Segunda chamada: nada a rodar.
( . "$DIR/avulsos.sh"; rodar_avulsos ) > "$TMP/saida2" 2>&1
! grep -q "rodou" "$TMP/saida2" && ok "não roda de novo o que já foi feito" || erro "rodou de novo"

exit $falhou
