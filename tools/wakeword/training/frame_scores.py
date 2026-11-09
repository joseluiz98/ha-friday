"""Notas por quadro de UM modelo em clipes ou arquivos inteiros (02/11/2026), com o mesmo passo do score_clips.py
(silêncio antes, para o extrator aquecer, e depois, para a palavra terminar dentro da janela). Base para medir regras do
detector que olham mais de um quadro (paciência) sem nova prova.
Uso: frame_scores.py <modelo.onnx> <saida.json> <pasta_ou_wav>...
Saída: {arquivo: [nota por quadro de 80 ms, 3 casas]}."""
import glob, json, os, sys, wave

import numpy as np
from openwakeword.model import Model

PASSO, SILENCIO, DEPOIS = 1280, 6, 12


def notas(modelo, chave, caminho):
    with wave.open(caminho) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, caminho
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    a = np.concatenate([a, np.zeros(PASSO * DEPOIS, dtype=np.int16)])
    modelo.reset()
    for _ in range(SILENCIO):
        modelo.predict(np.zeros(PASSO, dtype=np.int16))
    return [round(float(modelo.predict(a[i:i + PASSO])[chave]), 3) for i in range(0, len(a) - PASSO + 1, PASSO)]


def main(onnx, saida, alvos):
    chave = os.path.splitext(os.path.basename(onnx))[0]
    modelo = Model(wakeword_models=[onnx], inference_framework="onnx")
    arquivos = []
    for a in alvos:
        arquivos += sorted(glob.glob(os.path.join(a, "*.wav"))) if os.path.isdir(a) else [a]
    json.dump({f: notas(modelo, chave, f) for f in arquivos}, open(saida, "w"))
    print(f"{len(arquivos)} arquivos")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
