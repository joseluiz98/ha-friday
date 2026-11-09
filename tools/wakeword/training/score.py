"""Pontuador do retreino FORA da Pi (02/11/2026): as mesmas notas do pi_score.py (base, verif, vad por quadro de 80 ms),
para rodar na VM, na imagem wakeword-trainer:1 (versões iguais às da Pi). Mesma conta do Extrator de
treino_verificador.py, com duas diferenças:
  - o modelo-base é o da palavra pedida. O pi_score.py usa o Extrator, que carrega SEMPRE friday.onnx: no harvest da
    Sexta-feira (ciclos 1 e 2) as notas "base" eram as da Friday, e o recorte da sombra ficava no pico da Friday;
  - sem cache (a VM é rápida) e sem o programa do satélite no caminho.
Uso: score.py <saida.ndjson> <friday|sexta_feira> <pasta_modelos> <wav>... | @lista.txt [--base-onnx ARQ]
<pasta_modelos> tem <modelo>.onnx e <modelo>_verificador.pkl (se a palavra tiver verificador; 06/11: a Sexta tem). --base-onnx troca o modelo-base (só na prova de igualdade
com o pi_score.py). Saída, uma linha por arquivo: {"name", "dur", "base", "verif", "vad"}, alinhada ao COMEÇO do arquivo
(o quadro k cobre o áudio que termina em (k+1)*0.08 s), valores com 3 casas. Retoma: pula o que já está na saída."""
import argparse, json, os, pickle, wave

import numpy as np
import scipy.io.wavfile
from openwakeword.model import Model
from openwakeword.vad import VAD

PASSO, SILENCIO_ANTES = 1280, 6   # como no Extrator: 6 pedaços de silêncio antes; os 5 primeiros do modelo saem zerados


def notas(m, chave, caminho):
    taxa, dat = scipy.io.wavfile.read(caminho)
    if taxa != 16000 or dat.ndim != 1:
        raise ValueError(f"{caminho}: precisa ser 16 kHz mono")
    dat = np.concatenate([np.zeros(PASSO * SILENCIO_ANTES, dtype=np.int16), dat.astype(np.int16)])
    m.reset()
    vad = VAD()
    base, vz, feats = [], [], []
    for i in range(0, len(dat) - PASSO + 1, PASSO):
        x = dat[i:i + PASSO]
        base.append(float(m.predict(x)[chave]))
        vz.append(float(vad.predict(x, frame_size=640)))
        feats.append(m.preprocessor.get_features(m.model_inputs[chave])[0])
    return base[SILENCIO_ANTES:], vz[SILENCIO_ANTES:], np.asarray(feats[SILENCIO_ANTES:], dtype=np.float16)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("saida"); ap.add_argument("modelo"); ap.add_argument("modelos"); ap.add_argument("wavs", nargs="+")
    ap.add_argument("--base-onnx")
    a = ap.parse_args(argv)
    wavs = a.wavs
    if len(wavs) == 1 and wavs[0].startswith("@"):
        wavs = [l.strip() for l in open(wavs[0][1:]) if l.strip()]
    onnx = a.base_onnx or os.path.join(a.modelos, a.modelo + ".onnx")
    chave = os.path.splitext(os.path.basename(onnx))[0]
    m = Model(wakeword_models=[onnx], inference_framework="onnx")
    pkl = os.path.join(a.modelos, a.modelo + "_verificador.pkl")
    clf = pickle.load(open(pkl, "rb")) if os.path.exists(pkl) else None
    ja = {json.loads(l)["name"] for l in open(a.saida) if l.strip()} if os.path.exists(a.saida) else set()
    with open(a.saida, "a") as out:
        for i, caminho in enumerate(wavs):
            name = os.path.basename(caminho)
            if name in ja:
                continue
            base, vad, feats = notas(m, chave, os.path.realpath(caminho))
            verif = clf.predict_proba(feats)[:, -1].tolist() if (clf is not None and len(feats)) else []
            with wave.open(os.path.realpath(caminho)) as w:
                dur = w.getnframes() / w.getframerate()
            out.write(json.dumps({"name": name, "dur": round(dur, 3),
                                  "base": [round(v, 3) for v in base], "verif": [round(v, 3) for v in verif],
                                  "vad": [round(v, 3) for v in vad]}) + "\n")
            out.flush()
            print(f"{i + 1}/{len(wavs)} {name}", flush=True)


if __name__ == "__main__":
    main()
