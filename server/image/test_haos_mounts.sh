#!/usr/bin/env bash
# Desacoplamento do Samba (19/10, desenho A aprovado pelo dono): o container sobe sem o Samba e o HOME nunca passa por
# ele. O Samba do HAOS entra só em /mnt/haos (pasta-mãe local, rslave); /share é local no container, com o apelido do
# HOME e atalhos para /mnt/haos/share/<pasta>; /homeassistant é atalho para /mnt/haos/config.
# bash /opt/friday/image/test_haos_mounts.sh
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"; falhou=0
ok() { echo "ok   - $1"; }; erro() { echo "FALHA - $1"; falhou=1; }
C="$DIR/docker-compose.yml"; D="$DIR/Dockerfile"

grep -q 'source: /share$' "$C" && erro "compose ainda monta o /share do Samba" || ok "compose sem /share do Samba"
grep -q 'source: /homeassistant$' "$C" && erro "compose ainda monta o /homeassistant do Samba" || ok "compose sem /homeassistant do Samba"
grep -A4 'source: /mnt/haos$' "$C" | grep -q 'target: /mnt/haos$' && grep -A4 'source: /mnt/haos$' "$C" | grep -q 'propagation: rslave' \
  && ok "compose monta /mnt/haos com rslave" || erro "compose sem /mnt/haos rslave"
grep -q 'target: /share/openclaw-poc' "$C" && ok "apelido do HOME mantido" || erro "compose sem o apelido"
grep -q 'max-size' "$C" && grep -q 'max-file' "$C" && ok "log do Docker com rotação" || erro "compose sem rotação de log"

grep -q 'ln -s /mnt/haos/config /homeassistant' "$D" && ok "Dockerfile: /homeassistant é atalho" || erro "Dockerfile sem o atalho /homeassistant"
for n in .claude CLAUDE.md claude-voice friday alexa-rotinas alexa-uso; do
  grep -q -- " $n" "$D" && ok "Dockerfile: atalho fixo para $n" || erro "Dockerfile sem atalho fixo para $n"
done
grep -q 'COPY haos_links.sh /haos_links.sh' "$D" && ok "Dockerfile copia haos_links.sh" || erro "Dockerfile sem haos_links.sh"
grep -q '/haos_links.sh' "$DIR/run.sh" && ok "run.sh chama haos_links.sh" || erro "run.sh não chama haos_links.sh"

# haos_links.sh <pasta_de_atalhos> <share_do_haos> <nome_a_pular>
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/haos/b" "$T/haos/pular" "$T/haos/.oculta" "$T/haos/a" "$T/links/pular"
ln -s /outro/lugar "$T/links/a"
bash "$DIR/haos_links.sh" "$T/links" "$T/haos" pular; rc=$?
[ $rc -eq 0 ] && ok "haos_links sai com 0" || erro "haos_links saiu com $rc"
[ "$(readlink "$T/links/b")" = "$T/haos/b" ] && ok "cria atalho para pasta nova" || erro "sem atalho para b"
[ "$(readlink "$T/links/.oculta")" = "$T/haos/.oculta" ] && ok "inclui ocultas (.claude)" || erro "sem atalho para .oculta"
[ -d "$T/links/pular" ] && [ ! -L "$T/links/pular" ] && ok "não toca no apelido" || erro "mexeu no apelido"
[ "$(readlink "$T/links/a")" = /outro/lugar ] && ok "não troca atalho existente" || erro "trocou atalho existente"

bash "$DIR/haos_links.sh" "$T/links" "$T/nao-existe" pular; rc=$?
[ $rc -eq 0 ] && ok "Samba ausente: sai com 0" || erro "Samba ausente: saiu com $rc"

# Samba travado: a listagem não pode prender a partida (ls falso que dorme 30 s)
mkdir -p "$T/bin"; printf '#!/bin/sh\nsleep 30\n' > "$T/bin/ls"; chmod +x "$T/bin/ls"
ini=$(date +%s); PATH="$T/bin:$PATH" bash "$DIR/haos_links.sh" "$T/links" "$T/haos" pular; rc=$?; dur=$(( $(date +%s) - ini ))
[ $rc -eq 0 ] && [ $dur -le 8 ] && ok "Samba travado: desiste em ${dur}s" || erro "Samba travado: rc=$rc em ${dur}s"
exit $falhou
