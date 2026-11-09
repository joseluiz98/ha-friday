"""Roda NA PI (spec 2026-10-24): nota por quadro de 80 ms de cada WAV, para a coleta (harvest.sh) e o dataset.
Uso: pi_score.py <saida.ndjson> <modelo: friday|sexta_feira> <wav>... | @lista.txt   (sempre sob systemd-run com teto)
`@lista.txt` = um caminho por linha (a lista inteira em argv derrubou o sudo/python com SIGSEGV em 24/10).
Saída, uma linha por arquivo: {"name", "dur": s, "base": [...], "verif": [...], "vad": [...]}, alinhada ao COMEÇO do
arquivo: o quadro k cobre o áudio que termina em (k+1)*0.08 s. Valores com 3 casas.
`name` = basename do argumento (o harvest passa links com o nome final). Retoma: pula o que já está na saída.
O Extrator (treino_verificador.py) já descarta os SILENCIO_ANTES quadros de silêncio que ele mesmo prefixa
(base/vad/feats chegam alinhados ao início do arquivo): NÃO descartar de novo (o plano mandava; o código real já faz).
O cache do Extrator vai para uma pasta própria (RAIZ), apagada pela Task 4, para não crescer ~/vozes/_cache."""
import json, os, pickle, sys, wave

sys.path.insert(0, "/home/jose/satelite")
import numpy as np
import treino_verificador as tv
from treino_verificador import Extrator

CACHE_RAIZ = "/home/jose/wwt-score"
VERIFICADOR = "/home/jose/modelos/friday_verificador.pkl"


def main(saida, modelo, wavs):
    tv.RAIZ = CACHE_RAIZ
    ja = set()
    if os.path.exists(saida):
        ja = {json.loads(l)["name"] for l in open(saida) if l.strip()}
    clf = pickle.load(open(VERIFICADOR, "rb")) if modelo == "friday" else None
    ext = Extrator()
    with open(saida, "a") as out:
        for i, caminho in enumerate(wavs):
            name = os.path.basename(caminho)
            if name in ja:
                continue
            r = ext(os.path.realpath(caminho))
            base, vad, feats = r["base"], r["vad"], np.asarray(r["feats"], dtype=np.float32)
            verif = clf.predict_proba(feats)[:, -1].tolist() if (clf is not None and len(feats)) else []
            with wave.open(os.path.realpath(caminho)) as w:
                dur = w.getnframes() / w.getframerate()
            out.write(json.dumps({"name": name, "dur": round(dur, 3),
                                  "base": [round(v, 3) for v in base], "verif": [round(v, 3) for v in verif],
                                  "vad": [round(v, 3) for v in vad]}) + "\n")
            out.flush()
            print(f"{i + 1}/{len(wavs)} {name}", flush=True)


if __name__ == "__main__":
    args = sys.argv[3:]
    if len(args) == 1 and args[0].startswith("@"):
        args = [l.strip() for l in open(args[0][1:]) if l.strip()]
    main(sys.argv[1], sys.argv[2], args)
