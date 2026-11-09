#!/bin/bash
# Envio noturno das gravações do satélite para a VM (02/11/2026): a Pi só grava; harvest, notas e prova rodam na VM.
# Push com chave própria do satélite, que na VM só pode ESCREVER na pasta dele (rrsync -wo em
# satsync@VM:/srv/wakeword-data/incoming/<sat_id>/). Sem --delete: a retenção mora na VM (30 dias de ambiente).
# Leve de propósito (nice 19, ionice ocioso, banda limitada): nunca disputa CPU com a escuta.
# 08/11: só com o envio inteiro OK grava a marca `.sync_ok` (data e hora) na VM. É o contrato com a VM: quem
# processa as gravações lá (cache noturno das palavras) acorda com ela; a Pi não sabe quem consome.
# 09/11: na madrugada de 09/11 o rsync travou num fluxo TCP morto (6,6 de 9,6 MB de um WAV) e ficou 2 h parado até o
# TimeoutStartSec matar; sem marca, a VM não rodou nada (o contrato funcionou). Agora: keepalive no ssh, --timeout de
# I/O no rsync e até 3 tentativas por pasta; o --partial retoma o arquivo de onde parou.
set -uo pipefail
# 09/11: o endereço da VM vem do endereco_friday do config.json (ou do house.json da imagem), nunca do código
HOST=$(python3 -c 'import json,urllib.parse
for p in ("/etc/friday-satelite/config.json", "/etc/friday-satelite/house.json"):
    try:
        u = json.load(open(p)).get("endereco_friday")
    except Exception:
        u = None
    if u:
        print(urllib.parse.urlparse(u).hostname); break')
VM=${SATSYNC_VM:-satsync@$HOST}
[ -n "${SATSYNC_VM:-}$HOST" ] || { echo "sem endereço da VM (endereco_friday)" >&2; exit 1; }
KEY=$HOME/.ssh/id_satsync
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
ok=0
for src in gravacoes vozes; do
  for tentativa in 1 2 3; do
    nice -n 19 ionice -c3 rsync -a --partial --timeout=120 --bwlimit=4000 --exclude '_cache/' -e "$SSH" \
      "$HOME/$src/" "$VM:$src/"
    r=$?
    [ "$r" = 0 ] && break
    echo "sync_recordings $src tentativa $tentativa rc=$r $(date -Is)"
    sleep 60
  done
  [ "$r" = 0 ] || ok=$r
done
if [ "$ok" = 0 ]; then
  # Em forma de pasta: arquivo->arquivo sobre uma marca que já existe falha no `rrsync -no-del` da VM
  # ("delete_file: unlink(4) failed", rc 23, 09/11); a pasta substitui por renomeação e passa.
  m=$(mktemp -d); date -Is > "$m/.sync_ok"
  rsync -rt -e "$SSH" "$m/" "$VM:./" || ok=$?
  rm -rf "$m"
fi
echo "sync_recordings rc=$ok $(date -Is)"
exit $ok
