"""Varre limiares sobre as notas gravadas por evaluate.py (27/10/2026). Roda no HA, sem numpy e sem a Pi.
Uso: sweep_thresholds.py <manifest.ndjson> <notas.json> <limiar_atual> <limiar_cand>...
Mesma regra do evaluate.py (eval_logic). O modelo atual fica no limiar dele; o candidato em cada limiar pedido."""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval_logic as el


def avaliar(notas_por_arquivo, pos_por_arquivo, limiar):
    det, falsos = set(), 0
    for nome, notas in notas_por_arquivo.items():
        r = el.avaliar_arquivo(notas, pos_por_arquivo.get(nome, []), limiar)
        det.update(r["detectados"]); falsos += r["falsos"]
    return det, falsos


def main(manifest, notas_json, lim_atual, *lims_cand):
    itens = [json.loads(l) for l in open(manifest) if l.strip()]
    d = json.load(open(notas_json))
    arqs = d["arquivos"]
    pos_por_arquivo = {}
    for it in itens:
        if it["label"] == "pos":
            pos_por_arquivo.setdefault(it["name"], []).append(it)
    ids_pos = sorted(i["id"] for i in itens if i["label"] == "pos" and i["split"] == "holdout" and i["name"] in arqs)
    musica = {i for i in ids_pos if "_music_" in i}
    horas = sum(a["dur"] for a in arqs.values()) / 3600
    linha = lambda rot, det, fal, extra="": print(
        f"{rot:24} {len(det):3}/{len(ids_pos)} ({len(det) / len(ids_pos):.1%})  música {len(det & musica):2}/{len(musica)}"
        f"  falsos {fal:3} ({fal / horas:5.1f}/h){extra}")
    det_a, fal_a = avaliar({n: a[d["atual"]] for n, a in arqs.items()}, pos_por_arquivo, float(lim_atual))
    linha(f"atual @ {lim_atual}", det_a, fal_a)
    for lim in lims_cand:
        det_c, fal_c = avaliar({n: a[d["candidato"]] for n, a in arqs.items()}, pos_por_arquivo, float(lim))
        c = el.comparar(ids_pos, det_a, det_c)
        linha(f"candidato @ {lim}", det_c, fal_c, f"  perdas {len(c['regressoes'])} ganhos {len(c['ganhos'])}")


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    main(*sys.argv[1:])
