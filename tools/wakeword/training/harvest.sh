#!/bin/bash
# Coleta (spec 2026-10-24): candidatos da Pi -> notas e transcrição na VM (desde 02/11; antes as notas eram na Pi). Roda no HA.
# Uso: harvest.sh <friday|sexta_feira> <work> [--sat <sat_id>] [--host <ip>]   <work> = /tmp/wwt-harvest-<modelo>-<AAAAMMDD>
# --sat (18/11/2026): satélite da coleta (padrão FRIDAY_SAT_OFFICE); a Pi é --host, senão o "ip" dele em
# /share/friday/satellites.json, senão (sem --sat) /config/.friday/mk1 (sat_target.sh). Todo clipe copiado da Pi ganha
# o prefixo <sat_id>__ no nome e o campo "sat" em sources.ndjson; rulings.json sem prefixo são do escritório.
# O MESMO caminho é criado no HA (NDJSON) e na VM (áudio em <work>/wav). SEM trap: a Task 4 lê o áudio da VM e apaga.
# Saídas no HA: sources.ndjson, scores.ndjson, transcricoes.ndjson, marcas.json, rotulos.json, dedupe.txt.
set -uo pipefail
MODEL=${1:?modelo}; W=${2:?work}; shift 2
case "$MODEL" in friday|sexta_feira) ;; *) echo "modelo: friday|sexta_feira" >&2; exit 2;; esac
SAT_ARG=; HOST_ARG=
while [ $# -gt 0 ]; do case "$1" in
  --sat) SAT_ARG=${2:?--sat <sat_id>}; shift 2;; --host) HOST_ARG=${2:?--host <ip>}; shift 2;;
  *) echo "opção desconhecida: $1" >&2; exit 2;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/sat_target.sh"
sat_target "$SAT_ARG" "$HOST_ARG" || exit 2
PI="jose@$PI_HOST"
echo "== satélite $SAT (Pi $PI_HOST)"
mkdir -p "$W"; chmod 700 "$W"
ST=$W   # na Pi: só links (a Pi guarda os originais); cache do Extrator em ~/wwt-score

echo "== 1/6 lista na Pi"
# rótulos do dono sobre ativações (31/10): /share/wakeword-data/<modelo>/rulings.json -> "falsas_ativacoes"
FALSAS=$(python3 -I - "$HERE" "${FRIDAY_WAKEWORD_DATA:-/share/wakeword-data}/$MODEL/rulings.json" "$SAT" "$OFFICE" <<'PY'
import json, os, sys
sys.path.insert(0, sys.argv[1])
import harvest_lib as h
p, sat, office = sys.argv[2:5]
r = json.load(open(p)) if os.path.exists(p) else {}
print(" ".join(h.falsas_rotuladas(r, sat, office) + h.verdadeiras_rotuladas(r, sat, office)))
PY
) || { echo "rulings.json inválido" >&2; exit 1; }
echo "   falsas rotuladas: $(wc -w <<< "$FALSAS")"
ssh $PI "python3 - $MODEL $FALSAS" < "$HERE/harvest_list_pi.py" > "$W/lista.tsv" || { echo "falha na lista" >&2; exit 1; }
wc -l < "$W/lista.tsv"
python3 -I - "$W" "$SAT" <<PY || exit 1
import json, sys
sys.path.insert(0, "$HERE")
import harvest_lib as h
W, SAT = sys.argv[1:3]
linhas = [tuple(l.rstrip("\n").split("\t")) for l in open(W + "/lista.tsv") if l.strip()]
fontes, desc = h.deduplicar(linhas, sat=SAT)
with open(W + "/sources.ndjson", "w") as f:
    for s in fontes:
        f.write(json.dumps(s) + "\n")
from collections import Counter
c = Counter(s["src"] for s in fontes)
msg = f"{len(linhas)} candidatos; {len(fontes)} arquivos únicos; {desc} duplicatas descartadas; por fonte: {dict(c)}"
open(W + "/dedupe.txt", "w").write(msg + "\n"); print(msg)
PY

echo "== 2/6 links na Pi e cópia para a VM"
python3 -I -c "
import json,sys
for l in open('$W/sources.ndjson'):
    s=json.loads(l); print(s['name'], s['path'])" > "$W/nomes.txt"
ssh $PI "rm -rf $ST; mkdir -p $ST/wav; chmod 700 $ST; while read n p; do ln -s \"\$p\" $ST/wav/\$n; done" < "$W/nomes.txt"
ssh $VM "rm -rf $W; mkdir -p $W/wav; chmod 700 $W"
ssh $PI "tar chf - -C $ST/wav ." | ssh $VM "tar xf - -C $W/wav" || { echo "falha na cópia Pi -> VM" >&2; exit 1; }
if [ "$MODEL" = friday ]; then
  scp -q $PI:vozes/${FRIDAY_OWNER_VOICE_ID:?set FRIDAY_OWNER_VOICE_ID}/atual/marcas.json "$W/" \
    || { echo "AVISO: $SAT sem marcas.json do dono: a sessão do dono não gera itens" >&2; echo '[]' > "$W/marcas.json"; }
else
  echo '[]' > "$W/marcas.json"
fi
# rotulos.json (acervo de 15/10) só existe no satélite do escritório; nos outros vai vazio
scp -q $PI:vozes/rotulos.json "$W/" || { echo "AVISO: $SAT sem vozes/rotulos.json" >&2; echo '{"verdadeiras": [], "falsas": []}' > "$W/rotulos.json"; }
scp -q "$W/marcas.json" "$W/rotulos.json" $VM:$W/
echo "   VM: $(ssh $VM "ls $W/wav | wc -l") arquivos"

echo "== 3/6 transcrição e notas, as duas na VM (02/11: a Pi só grava)"
# modelos em uso na Pi (o verificador é retreinado na Pi pela aprendizagem de voz): cópia fresca a cada coleta
MD=/opt/friday/wakeword-trainer/models
VERIF=friday_verificador.pkl; [ "$MODEL" = friday ] || VERIF=${MODEL}_verificador.pkl   # 06/11: cada palavra com o seu
scp -q -p $PI:modelos/$MODEL.onnx $PI:modelos/$VERIF "$W/"
scp -q -p "$W/$MODEL.onnx" "$W/$VERIF" $VM:$MD/
[ "$(ssh $PI "md5sum modelos/$MODEL.onnx" | cut -d' ' -f1)" = "$(ssh $VM "md5sum $MD/$MODEL.onnx" | cut -d' ' -f1)" ] \
  || { echo "modelo da VM difere do da Pi" >&2; exit 1; }
ssh $VM "ls $W/wav/*.wav | sed s#^$W#/d# > $W/list.txt"
(ssh $VM "sudo docker run --rm --name wwt-asr --cpus 2 --memory 3g -v /var/cache/friday-asr:/models -v $W:/d friday-asr:1 /d/wav /d/transcricoes.ndjson medium" > "$W/asr.log" 2>&1; echo $? > "$W/asr.rc") &
ssh $VM "sudo docker run --rm --name wwt-score --cpus 2 --memory 2g -v $W:/d -v $MD:/m:ro -v /mnt/haos/config/tools/satelite/wakeword-training:/code:ro wakeword-trainer:1 python /code/score.py /d/scores.ndjson $MODEL /m @/d/list.txt" > "$W/score.log" 2>&1
echo $? > "$W/score.rc"
wait
echo "   score rc=$(cat "$W/score.rc")  asr rc=$(cat "$W/asr.rc")"

echo "== 4/6 trazendo resultados"
scp -q $VM:$W/scores.ndjson "$W/scores.ndjson"
scp -q $VM:$W/transcricoes.ndjson "$W/transcricoes.ndjson"
[ "$(cat "$W/asr.rc")" = 0 ] && [ "$(cat "$W/score.rc")" = 0 ] || { echo "ERRO: um dos passos falhou (ver $W/asr.log e $W/score.log)" >&2; exit 1; }
N=$(wc -l < "$W/sources.ndjson"); echo "== 5/6 conferência: fontes=$N scores=$(wc -l < "$W/scores.ndjson") transcrições=$(wc -l < "$W/transcricoes.ndjson")"
echo "== 6/6 pronto: $W (áudio na VM em $W/wav; limpeza na Task 4)"
