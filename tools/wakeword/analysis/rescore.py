"""Roda NA PI: repontua WAVs contínuos só com o modelo base (sem verificador) e grava a nota de cada quadro de 80 ms.

Uso: rescore.py <modelo.onnx> <saida.ndjson> <wav>...   (sempre dentro de systemd-run com teto; ver run.sh)
Áudio contínuo de propósito: recortar cada tentativa num WAV derruba a nota (08/10)."""
import json, os, sys, wave

import numpy as np
from openwakeword.model import Model

model_path, out_path, paths = sys.argv[1], sys.argv[2], sys.argv[3:]
key = os.path.splitext(os.path.basename(model_path))[0]
m = Model(wakeword_models=[model_path], inference_framework="onnx")
with open(out_path, "w") as out:
    for p in sorted(paths):
        m.reset()
        a = np.frombuffer(wave.open(p).readframes(10 ** 9), dtype=np.int16)
        s = [float(m.predict(a[i:i + 1280])[key]) for i in range(0, len(a) - 1279, 1280)]
        out.write(json.dumps({"arq": os.path.basename(p), "s": [round(x, 3) for x in s]}) + "\n")
        out.flush()
