"""Roda NA PI (28/10/2026): nota máxima de dois modelos em clipes curtos ou arquivos inteiros, com o mesmo passo do
evaluate.py (silêncio antes, para o extrator aquecer, e depois, para a palavra terminar dentro da janela).
Uso: score_clips.py <atual.onnx> <candidato.onnx> <saida.json> <pasta_ou_wav>...
Saída: {arquivo: {"atual": max, "candidato": max, "dur": s}}."""
import glob, json, os, sys, wave

import numpy as np
from openwakeword.model import Model

PASSO, SILENCIO, DEPOIS = 1280, 6, 12


def notas(modelo, nomes, caminho):
    with wave.open(caminho) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, caminho
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    a = np.concatenate([a, np.zeros(PASSO * DEPOIS, dtype=np.int16)])
    modelo.reset()
    for _ in range(SILENCIO):
        modelo.predict(np.zeros(PASSO, dtype=np.int16))
    m = {n: 0.0 for n in nomes}
    for i in range(0, len(a) - PASSO + 1, PASSO):
        p = modelo.predict(a[i:i + PASSO])
        for n in nomes:
            m[n] = max(m[n], float(p[n]))
    return m, len(a) / 16000


def main(atual, cand, saida, alvos):
    nomes = [os.path.splitext(os.path.basename(x))[0] for x in (atual, cand)]
    assert nomes[0] != nomes[1], "os dois modelos precisam de nomes de arquivo diferentes"
    modelo = Model(wakeword_models=[atual, cand], inference_framework="onnx")
    arqs = []
    for t in alvos:
        arqs += sorted(glob.glob(os.path.join(t, "**", "*.wav"), recursive=True)) if os.path.isdir(t) else [t]
    out = {}
    for c in arqs:
        m, d = notas(modelo, nomes, c)
        out[c] = {"atual": m[nomes[0]], "candidato": m[nomes[1]], "dur": round(d, 2)}
    json.dump(out, open(saida, "w"), indent=1)
    print(f"{len(out)} arquivos")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:])
