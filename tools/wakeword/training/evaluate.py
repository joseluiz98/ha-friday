"""Roda NA PI, sob systemd-run com teto (spec/plano 2026-10-24, Task 6): compara o modelo atual e o candidato na
reserva de prova, com áudio contínuo (como na captura) e o detector de hoje.
Uso: evaluate.py <manifest.ndjson> <holdout_dir> <limiar> <atual.onnx> <candidato.onnx> <saida.json> [notas.json]
Os dois modelos têm nomes de arquivo DIFERENTES e rodam no mesmo Model (extrator compartilhado)."""
import json, os, sys, wave

import numpy as np
from openwakeword.model import Model

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval_logic as el

PASSO, SILENCIO = 1280, 6


def notas_do_arquivo(modelo, caminho, nomes):
    with wave.open(caminho) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, caminho
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    modelo.reset()
    for _ in range(SILENCIO):  # os primeiros quadros do modelo saem zerados
        modelo.predict(np.zeros(PASSO, dtype=np.int16))
    saida = {n: [] for n in nomes}
    for i in range(0, len(a) - PASSO + 1, PASSO):
        p = modelo.predict(a[i:i + PASSO])
        for n in nomes:
            saida[n].append(float(p[n]))
    return saida, len(a) / 16000


def main(manifest, holdout_dir, limiar, atual, cand, saida, notas_saida=None):
    limiar = float(limiar)
    n_atual, n_cand = (os.path.splitext(os.path.basename(p))[0] for p in (atual, cand))
    assert n_atual != n_cand, "atual e candidato precisam de nomes de arquivo diferentes"
    itens = [json.loads(l) for l in open(manifest) if l.strip()]
    pos_por_arquivo = {}
    for it in itens:
        if it["label"] == "pos":
            pos_por_arquivo.setdefault(it["name"], []).append(it)
    modelo = Model(wakeword_models=[atual, cand], inference_framework="onnx")
    res = {n_atual: {"det": set(), "falsos": 0, "fonte": {}}, n_cand: {"det": set(), "falsos": 0, "fonte": {}}}
    horas, todas = 0.0, {}
    arquivos = sorted(os.listdir(holdout_dir))
    for j, nome in enumerate(arquivos):
        notas, dur = notas_do_arquivo(modelo, os.path.join(holdout_dir, nome), (n_atual, n_cand))
        horas += dur / 3600
        if notas_saida:  # 27/10: guarda as notas para varrer limiares fora da Pi (sweep_thresholds.py)
            todas[nome] = {"dur": dur, n_atual: notas[n_atual], n_cand: notas[n_cand]}
        for n in (n_atual, n_cand):
            r = el.avaliar_arquivo(notas[n], pos_por_arquivo.get(nome, []), limiar)
            res[n]["det"].update(r["detectados"]); res[n]["falsos"] += r["falsos"]
            if r["falsos"]:  # diagnóstico: de que fonte vêm os disparos "falsos" (ativou/quase sem rótulo contam como falsos)
                f_ = nome.split("__")[0]; res[n]["fonte"][f_] = res[n]["fonte"].get(f_, 0) + r["falsos"]
        print(f"{j + 1}/{len(arquivos)} {nome}", flush=True)
    ids_pos = sorted(i["id"] for i in itens if i["label"] == "pos" and i["split"] == "holdout"
                     and i["name"] in set(arquivos))
    total = len(ids_pos)

    def lado(n, caminho):
        d = res[n]["det"]
        return {"nome": os.path.basename(caminho), "pos_total": total, "pos_detectados": len(d),
                "recall": round(len(d) / total, 4) if total else 0.0, "disparos_falsos": res[n]["falsos"],
                "fp_por_hora": round(res[n]["falsos"] / horas, 3) if horas else 0.0,
                "falsos_por_fonte": res[n]["fonte"]}

    out = {"limiar": limiar, "horas": round(horas, 4), "atual": lado(n_atual, atual), "candidato": lado(n_cand, cand)}
    out.update(el.comparar(ids_pos, res[n_atual]["det"], res[n_cand]["det"]))
    json.dump(out, open(saida, "w"), ensure_ascii=False, indent=1)
    if notas_saida:
        json.dump({"atual": n_atual, "candidato": n_cand, "arquivos": todas}, open(notas_saida, "w"))
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
