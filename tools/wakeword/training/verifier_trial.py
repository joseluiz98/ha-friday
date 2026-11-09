"""Ensaio offline de um verificador de voz para uma palavra extra (02/11/2026), na VM (wakeword-trainer:1).
O verificador do openWakeWord (o mesmo da Friday) aprende, sobre as entradas do modelo da palavra, "esta voz dizendo a
palavra" contra "todo o resto". Ao vivo ele troca a nota-base (quando >= lv) pela dele, e o DetectorPalavra decide.
Validação por grupo: cada sessão de positivos fica de fora uma vez; os negativos se dividem por DIA (metade par/ímpar),
para a prova nunca ver o dia que treinou.
Uso: verifier_trial.py <modelo.onnx> <saida.json> <manifest.ndjson> <pasta_wav_harvest> <excluidas.json>
     <neg_dir>... [--lv 0.5 --limiar 0.5 --limiar-voz 0.6] [--salvar verificador.pkl]
<pasta_wav_harvest>: sessao__*.wav (positivos, janelas do manifest) e sombra__*.wav (negativos; os de excluidas.json
são testes do dono = positivos). <neg_dir>: pastas de WAV só negativos (sala, conversa)."""
import argparse, glob, json, os, re, sys

import numpy as np
from openwakeword.custom_verifier_model import train_verifier_model

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/programa")
import score  # noqa: E402  (mesma extração do harvest: base, vad, feats)
import verificador as vf  # noqa: E402  (vf.aceita = a decisão ao vivo; quadros_positivos)
from openwakeword.model import Model  # noqa: E402

NEG_PASSO, NEG_MAX = 2, 6000
FRAME = 0.08


def dia(nome):
    m = re.findall(r"(\d{8})_\d{6}", nome)
    return m[-1] if m else "sem_data"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("onnx"); ap.add_argument("saida"); ap.add_argument("manifest"); ap.add_argument("harvest")
    ap.add_argument("excluidas"); ap.add_argument("negs", nargs="+")
    ap.add_argument("--lv", type=float, default=0.5); ap.add_argument("--limiar", type=float, default=0.5)
    ap.add_argument("--limiar-voz", type=float, default=0.6)
    ap.add_argument("--salvar", help="treina com TUDO no fim e grava o .pkl aqui (o verificador a instalar)")
    a = ap.parse_args()
    chave = os.path.splitext(os.path.basename(a.onnx))[0]
    m = Model(wakeword_models=[a.onnx], inference_framework="onnx")
    ext = lambda p: dict(zip(("base", "vad", "feats"), score.notas(m, chave, p)))
    jose = set(json.load(open(a.excluidas))["excluidas"])
    clipes = []  # {grupo, rotulo, base, vad, feats, nome}
    man = [json.loads(l) for l in open(a.manifest)]
    for sess in sorted({i["name"] for i in man if i["src"] == "sessao"}):
        e = ext(os.path.join(a.harvest, sess))
        for it in (i for i in man if i["name"] == sess and i["label"] == "pos"):
            k0, k1 = max(int((it["ini"] - 0.2) / FRAME), 0), int((it["fim"] + 1.2) / FRAME) + 1
            clipes.append({"grupo": "s:" + sess, "rotulo": 1, "nome": it["id"],
                           **{k: e[k][k0:k1] for k in ("base", "vad", "feats")}})
    for p in sorted(glob.glob(os.path.join(a.harvest, "sombra__*.wav"))):
        orig = os.path.basename(p).split("__", 1)[1]
        rot = 1 if orig in jose else 0
        clipes.append({"grupo": ("s:testes_jose" if rot else "d:" + dia(orig)), "rotulo": rot, "nome": orig, **ext(p)})
    for d in a.negs:
        for p in sorted(glob.glob(os.path.join(d, "*.wav"))):
            if "fala_livre" in p:  # fala livre do dono: pode ter a palavra
                continue
            clipes.append({"grupo": "d:" + dia(os.path.basename(p)), "rotulo": 0, "nome": os.path.basename(p), **ext(p)})

    def treinar(lista):
        pos = [c["feats"][i] for c in lista if c["rotulo"] for i in vf.quadros_positivos(c["base"])]
        neg = [c["feats"][i] for c in lista if not c["rotulo"] for i in range(0, len(c["feats"]), NEG_PASSO)]
        if len(neg) > NEG_MAX:
            neg = [neg[int(j)] for j in np.linspace(0, len(neg) - 1, NEG_MAX)]
        X = np.asarray(pos + neg, dtype=np.float32)
        y = np.asarray([1] * len(pos) + [0] * len(neg))
        return train_verifier_model(X, y), len(pos), len(neg)

    def decide(modelo, c):
        notas = modelo.predict_proba(np.asarray(c["feats"], dtype=np.float32))[:, -1].tolist() if len(c["feats"]) else []
        return vf.aceita(c["base"], notas, c["vad"], a.lv, a.limiar, a.limiar_voz)

    def sem_verificador(c):
        return vf.aceita(c["base"], c["base"], c["vad"], 2.0, a.limiar, a.limiar_voz)  # lv > 1: nunca troca

    grupos_pos = sorted({c["grupo"] for c in clipes if c["rotulo"]})
    dias = sorted({c["grupo"] for c in clipes if not c["rotulo"]})
    res = {"pos": {"total": 0, "sem": 0, "com": 0}, "neg": {"total": 0, "sem": 0, "com": 0}, "folds": []}
    for gp in grupos_pos:
        for metade in (0, 1):
            prova_dias = {d for i, d in enumerate(dias) if i % 2 == metade}
            treino = [c for c in clipes if (c["rotulo"] and c["grupo"] != gp) or (not c["rotulo"] and c["grupo"] not in prova_dias)]
            modelo, npos, nneg = treinar(treino)
            prova_pos = [c for c in clipes if c["rotulo"] and c["grupo"] == gp] if metade == 0 else []
            prova_neg = [c for c in clipes if not c["rotulo"] and c["grupo"] in prova_dias]
            fp = {"positivos_de": gp, "metade": metade, "treino_quadros": [npos, nneg]}
            for nome, lista in (("pos", prova_pos), ("neg", prova_neg)):
                s = sum(sem_verificador(c) for c in lista); v = sum(decide(modelo, c) for c in lista)
                res[nome]["total"] += len(lista); res[nome]["sem"] += s; res[nome]["com"] += v
                fp[nome] = [len(lista), s, v]
            res["folds"].append(fp)
    json.dump(res, open(a.saida, "w"), indent=1)
    if a.salvar:
        import pickle
        modelo, npos, nneg = treinar(clipes)
        pickle.dump(modelo, open(a.salvar, "wb"))
        res["final"] = {"quadros": [npos, nneg], "pkl": a.salvar}
        json.dump(res, open(a.saida, "w"), indent=1)
        print(f"verificador final: {npos} quadros positivos, {nneg} negativos -> {a.salvar}")
    p, n = res["pos"], res["neg"]
    print(f"positivos aceitos: sem verificador {p['sem']}/{p['total']}, com {p['com']}/{p['total']}")
    print(f"negativos que disparam (somando as {len(grupos_pos)} rodadas): sem {n['sem']}/{n['total']}, com {n['com']}/{n['total']}")


if __name__ == "__main__":
    main()
