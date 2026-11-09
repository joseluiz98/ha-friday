"""Repete dias de ambiente com as regras do satélite para a Sexta-feira (06/11/2026). Roda na VM, em wakeword-trainer:1.
Vários modelos (cada um em sua pasta, sempre com o nome sexta_feira.onnx), mesmo verificador, como ao vivo: a nota >= 0,5
é trocada pela do verificador; a voz é o máximo do VAD nos últimos 10 quadros (janela_voz_palavra).
Uso: replay_sexta.py nome=<onnx>[,nome=<onnx>...] <verificador.pkl> <saida.ndjson> <wav>...
Saída por bloco: {"f", "dur", "<nome>": [[quadro, nota, voz]...]}, só quadros com nota >= 0,2. Retoma de onde parou.
Simular depois com `simular`, abaixo: paciência 2, limiar 0,5, voz 0,5, rearma < 0,2."""
import collections, json, os, pickle, sys, wave

PASSO, SILENCIO = 1280, 6
QUADRO = 0.08


def simular(quadros, limiar=0.5, limiar_voz=0.5, paciencia=2):
    """Instantes (s) dos disparos; `quadros` só traz notas >= 0,2, e o quadro que falta rearma, como no DetectorPalavra."""
    disparos, armado, seguidos, ant = [], True, 0, None
    for q, nota, voz in quadros:
        if ant is not None and q != ant + 1:
            armado, seguidos = True, 0
        ant = q
        seguidos = seguidos + 1 if nota >= limiar and voz >= limiar_voz else 0
        if armado and seguidos >= paciencia:
            armado = False
            disparos.append(round(q * QUADRO, 2))
    return disparos


def main(modelos, pkl, saida, wavs):
    import numpy as np
    from openwakeword.model import Model
    from openwakeword.vad import VAD
    ver = pickle.load(open(pkl, "rb"))
    ms = {}
    for nome, onnx in (x.split("=") for x in modelos.split(",")):
        m = Model(wakeword_models=[onnx], inference_framework="onnx", custom_verifier_threshold=0.5)
        m.custom_verifier_models = {"sexta_feira": ver}
        ms[nome] = m
    ja = {json.loads(l)["f"] for l in open(saida) if l.strip()} if os.path.exists(saida) else set()
    with open(saida, "a") as out:
        for i, caminho in enumerate(wavs):
            nome = os.path.basename(caminho)
            if nome in ja:
                continue
            with wave.open(caminho) as w:
                assert w.getframerate() == 16000 and w.getnchannels() == 1, caminho
                a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            vad, vozes = VAD(), collections.deque(maxlen=10)
            z = np.zeros(PASSO, dtype=np.int16)
            for m in ms.values():
                m.reset()
            for _ in range(SILENCIO):
                [m.predict(z) for m in ms.values()]
                vad.predict(z, frame_size=640)
            r = {"f": nome, "dur": round(len(a) / 16000, 2), **{n: [] for n in ms}}
            for k, j in enumerate(range(0, len(a) - PASSO + 1, PASSO)):
                x = a[j:j + PASSO]
                vozes.append(float(vad.predict(x, frame_size=640)))
                for n, m in ms.items():
                    p = float(m.predict(x)["sexta_feira"])
                    if p >= 0.2:
                        r[n].append([k, round(p, 3), round(max(vozes), 3)])
            out.write(json.dumps(r) + "\n")
            out.flush()
            print(f"{i + 1}/{len(wavs)} {nome}", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4], sys.argv[4:])
