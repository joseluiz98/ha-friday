#!/bin/bash
# Sobe uma pasta DA VM como dataset privado do Kaggle e só termina quando o Kaggle SERVE a versão nova (27/10/2026).
# Uso: kaggle_dataset_push.sh <pasta_na_vm> <owner/slug> <título>   -> imprime "MARCA=<marca>" na última linha
# Grava <pasta>/marca.json {"marca", "arquivos"}; depois baixa o marca.json servido pelo Kaggle e compara. Regra do o dono
# (27/10): nenhum notebook roda sem provar antes que o dataset é o correto; o notebook confere a mesma marca.
set -uo pipefail
P=${1:?pasta}; DID=${2:?owner/slug}; TIT=${3:?titulo}
VM="$(cat /config/.friday/vm)"; T=/tmp/kds-$$
K() { ssh "$VM" "sudo docker run --rm -e KAGGLE_API_TOKEN=\"\$(sudo cat /srv/friday/state/kaggle_api.token)\" -v $P:/w/data -v $T:/w/chk -w /w friday-kaggle:1 $*"; }
trap 'ssh "$VM" "sudo rm -rf $T" >/dev/null 2>&1' EXIT
MARCA="$(basename "$DID")-$(date +%Y%m%d%H%M%S)"
ssh "$VM" "mkdir -p $T && cd $P && n=\$(find . -type f ! -name marca.json ! -name dataset-metadata.json | wc -l) && \
  printf '{\"marca\": \"%s\", \"arquivos\": %s}\n' '$MARCA' \$n > marca.json && \
  printf '{\"id\": \"%s\", \"title\": \"%s\", \"licenses\": [{\"name\": \"other\"}]}\n' '$DID' '$TIT' > dataset-metadata.json && cat marca.json" || exit 1
if K datasets status "$DID" >/dev/null 2>&1; then
  K datasets version -p /w/data -r zip -m "$MARCA" || exit 1
else
  K datasets create -p /w/data -r zip || exit 1
fi
st=""
for i in $(seq 1 40); do
  sleep 30
  K datasets download "$DID" -f marca.json -p /w/chk --force >/dev/null 2>&1
  st=$(ssh "$VM" "sudo cat $T/marca.json 2>/dev/null" | python3 -I -c 'import json,sys
try: print(json.load(sys.stdin).get("marca", ""))
except Exception: print("")')
  [ "$st" = "$MARCA" ] && break
done
[ "$st" = "$MARCA" ] || { echo "o Kaggle não passou a servir a versão nova em 20 min (serve: '${st:-nada}')" >&2; exit 1; }
echo "   Kaggle serve a versão nova ($(( i*30 )) s)"
echo "MARCA=$MARCA"
