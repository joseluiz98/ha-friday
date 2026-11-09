"""Cache de representações do openWakeWord para provar MUITOS modelos em segundos (07/11/2026). Roda na VM, em
wakeword-trainer:1. O extrator (melspec + embedding) é o caro e é igual para todo modelo; a cabeça da palavra (16x96 ->
nota) é barata. Então o áudio passa UMA vez pelo extrator, com o mesmo passo do satélite, e cada checkpoint é só a cabeça
aplicada às janelas guardadas.

  featcache.py cache <dir> <conjunto> <modo> <wav ou pasta>...
      modo: ambiente (silêncio antes + VAD, como replay_sexta.py), sessao (silêncio antes, como evaluate.py) ou
      clipe (silêncio antes e 12 passos de silêncio depois, como score_clips.py). Grava <dir>/<conjunto>.emb.npy
      (linhas de 96), .vad.npy (modo ambiente) e .json (por arquivo: nome, dur, linha0, passos). Retoma por arquivo
      não; refaz o conjunto inteiro.
  featcache.py score <dir> <saida.json> <conjuntos,...> <verificador.pkl|-> nome=<onnx> [nome=<onnx>...]
      ambiente -> {"f","dur",nome:[[quadro, nota, voz]...]} com nota >= 0,2, nota >= 0,5 trocada pela do verificador
      (como ao vivo); sessao -> notas por quadro; clipe -> nota máxima. Saída: {conjunto: {arquivo: {nome: ...}}}.

A janela do quadro k (k = 0 no 1º passo de áudio real, depois do silêncio) são as linhas [k, k+16) do conjunto a partir
de linha0: as 15 primeiras vêm do aquecimento. `cache` confere essa conta contra get_features(16) do próprio extrator."""
import collections, glob, json, os, pickle, sys, wave

import numpy as np

PASSO, SILENCIO, DEPOIS, JANELA_VOZ = 1280, 6, 12, 10
MODOS = ("ambiente", "sessao", "clipe")


def voz_janela(vad, n=JANELA_VOZ):
    """Máximo do VAD nos últimos n quadros, incluindo o atual (deque maxlen=n do replay)."""
    out, d = [], collections.deque(maxlen=n)
    for v in vad:
        d.append(float(v))
        out.append(max(d))
    return out


def janelas(emb, linha0, passos):
    """(passos, 16, 96) sem cópia: a janela do quadro k termina na linha linha0 + 15 + k."""
    v = np.lib.stride_tricks.sliding_window_view(emb[linha0:linha0 + passos + 15], 16, axis=0)
    return v.transpose(0, 2, 1)


def ambiente_quadros(notas, voz, piso=0.2):
    return [[k, round(float(n), 3), round(float(voz[k]), 3)] for k, n in enumerate(notas) if n >= piso]


def arquivos(alvos):
    out = []
    for t in alvos:
        out += sorted(glob.glob(os.path.join(t, "**", "*.wav"), recursive=True)) if os.path.isdir(t) else [t]
    return out


def cache(d, conjunto, modo, alvos):
    from openwakeword.utils import AudioFeatures
    from openwakeword.vad import VAD
    assert modo in MODOS, modo
    os.makedirs(d, exist_ok=True)
    pre = AudioFeatures(inference_framework="onnx")
    z = np.zeros(PASSO, dtype=np.int16)
    linhas, vads, idx, total = [], [], [], 0
    rnd = np.random.default_rng(7)
    for i, caminho in enumerate(arquivos(alvos)):
        with wave.open(caminho) as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1, caminho
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if modo == "clipe":
            a = np.concatenate([a, np.zeros(PASSO * DEPOIS, dtype=np.int16)])
        pre.reset()
        vad = VAD() if modo == "ambiente" else None
        for _ in range(SILENCIO):
            pre(z)
            if vad:
                vad.predict(z, frame_size=640)
        bloco = [pre.feature_buffer[-15:].astype(np.float32)]
        checar = set(rnd.integers(0, max(1, len(a) // PASSO), size=3).tolist()) | {0}
        v = []
        for k, j in enumerate(range(0, len(a) - PASSO + 1, PASSO)):
            x = a[j:j + PASSO]
            if vad:
                v.append(float(vad.predict(x, frame_size=640)))
            pre(x)
            bloco.append(pre.feature_buffer[-1:].astype(np.float32))
            if k in checar:  # a conta da janela tem de bater com o que o Model veria
                assert np.array_equal(np.vstack(bloco)[-16:], pre.get_features(16)[0]), (caminho, k)
        passos = len(bloco) - 1
        linhas += bloco
        vads += v
        idx.append({"nome": os.path.basename(caminho), "caminho": caminho, "dur": round(len(a) / 16000, 2),
                    "linha0": total, "passos": passos, "vad0": len(vads) - len(v)})
        total += passos + 15
        print(f"{i + 1} {os.path.basename(caminho)} {passos}", flush=True)
    np.save(os.path.join(d, f"{conjunto}.emb.npy"), np.vstack(linhas))
    if modo == "ambiente":
        np.save(os.path.join(d, f"{conjunto}.vad.npy"), np.array(vads, dtype=np.float32))
    json.dump({"modo": modo, "arquivos": idx}, open(os.path.join(d, f"{conjunto}.json"), "w"))


def score(d, saida, conjuntos, pkl, modelos):
    import onnxruntime as ort
    ver = pickle.load(open(pkl, "rb")) if pkl != "-" else None
    sess = {}
    for nome, onnx in (m.split("=", 1) for m in modelos):
        so = ort.SessionOptions(); so.intra_op_num_threads = os.cpu_count()
        sess[nome] = ort.InferenceSession(onnx, so, providers=["CPUExecutionProvider"])
    out = {}
    for c in conjuntos.split(","):
        meta = json.load(open(os.path.join(d, f"{c}.json")))
        emb = np.load(os.path.join(d, f"{c}.emb.npy"), mmap_mode="r")
        vad = np.load(os.path.join(d, f"{c}.vad.npy")) if meta["modo"] == "ambiente" else None
        out[c] = {}
        for it in meta["arquivos"]:
            w = janelas(emb, it["linha0"], it["passos"])
            r = {"dur": it["dur"]}
            voz = voz_janela(vad[it["vad0"]:it["vad0"] + it["passos"]]) if vad is not None else None
            for nome, s in sess.items():
                notas = np.concatenate([s.run(None, {s.get_inputs()[0].name: np.ascontiguousarray(w[i:i + 8192])})[0]
                                        .reshape(-1) for i in range(0, len(w), 8192)]) if len(w) else np.zeros(0)
                if meta["modo"] == "ambiente":
                    if ver is not None:
                        for k in np.nonzero(notas >= 0.5)[0]:
                            notas[k] = ver.predict_proba(np.ascontiguousarray(w[k:k + 1]))[0][-1]
                    r[nome] = ambiente_quadros(notas, voz)
                elif meta["modo"] == "sessao":
                    r[nome] = [round(float(n), 4) for n in notas]
                else:
                    r[nome] = round(float(notas.max()), 4) if len(notas) else 0.0
            out[c][it["nome"]] = r
        print(f"{c}: {len(meta['arquivos'])} arquivos", flush=True)
    json.dump(out, open(saida, "w"))


if __name__ == "__main__":
    if sys.argv[1] == "cache":
        cache(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
    elif sys.argv[1] == "score":
        score(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6:])
    else:
        sys.exit(__doc__)
