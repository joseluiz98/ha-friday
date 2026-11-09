"""Prova de muitos modelos da Sexta-feira a partir do featcache.py score (07/11/2026). Roda no HA.
Uso: ablation_report.py <score.json> [<score.json>...] [--csv saida.csv]
Para cada modelo (cabeça) mede, como a prova de sempre e o replay com as regras reais:
  sess  = acertos na reserva das sessões guiadas a 0,5 (eval_logic, igual ao evaluate.py) e falsos lá
  cv/xtts/xvar/amb = clipes nativos da reserva com nota máxima >= 0,5 (igual ao score_clips.py)
  xneg/sala/conv = clipes NEGATIVOS com nota >= 0,5 (quanto menor, melhor)
  rN = replay de 3 dias com verificador, paciência 2, voz 10/0,5: chamadas acertadas (treino+prova, prova) e disparos
       fora das chamadas, nos limiares 0,5 e 0,4."""
import collections, datetime as D, json, os, re, sys

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)
import eval_logic as el
from replay_sexta import simular

BASE = "/share/wakeword-data/sexta_feira"
MANIFESTO_SESSOES = f"{BASE}/cycle-20261106b/manifest.ndjson"
CHAMADAS = f"{BASE}/raw/20261105_ambient_calls/manifesto.json"
NAT = ("cv27", "xtts", "xttsvar", "ambiente", "xttsneg")


def ts(f):
    m = re.match(r"ambiente_(\d{8})_(\d{6})", f)
    return D.datetime.strptime(m[1] + m[2], "%Y%m%d%H%M%S")


def chamadas():
    man = json.load(open(CHAMADAS))
    out = []
    for i in man["itens"]:
        if i["status"] != "ok":
            continue
        b = D.datetime.strptime(i["dia"] + i["hora"], "%Y%m%d%H:%M:%S")
        out.append((b - D.timedelta(seconds=1), b + D.timedelta(seconds=4), i["lado"]))
    return out


def replay(blocos, nome, cham, limiar):
    ev = []
    for f, r in blocos.items():
        for t in simular(r[nome], limiar=limiar):
            ev.append(ts(f) + D.timedelta(seconds=t))
    hit, usados = collections.Counter(), set()
    for a, z, lado in cham:
        h = [e for e in ev if a <= e <= z]
        if h:
            hit[lado] += 1
            usados.update(h)
    return hit["train"] + hit["holdout"], hit["holdout"], sum(1 for e in ev if e not in usados)


def medir(sc, nome, pos_por_arquivo, n_pos_reserva, cham):
    r = {"modelo": nome}
    det, falsos = set(), 0
    for arq, v in sc.get("sessoes", {}).items():
        x = el.avaliar_arquivo(v[nome], pos_por_arquivo.get(arq, []), 0.5)
        det.update(x["detectados"]); falsos += x["falsos"]
    r["sess"] = f"{len(det)}/{n_pos_reserva}"; r["sess_f"] = falsos
    for g in NAT:
        c = sc.get(f"nat_{g}", {})
        r[g] = f"{sum(v[nome] >= 0.5 for v in c.values())}/{len(c)}"
    for g in ("sala", "conversa"):
        c = sc.get(g, {})
        r[g] = sum(v[nome] >= 0.5 for v in c.values())
    blocos = {}
    for c in sorted(k for k in sc if k.startswith("amb")):
        blocos.update(sc[c])
    if blocos:
        for lim in (0.5, 0.4):
            tot, prova, fora = replay(blocos, nome, cham, lim)
            r[f"r{lim}"] = f"{tot}/{len(cham)} p{prova} f{fora}"
    return r


def main(args):
    csv = None
    if "--csv" in args:
        i = args.index("--csv"); csv = args[i + 1]; args = args[:i] + args[i + 2:]
    sc = {}
    for a in args:
        for conj, arqs in json.load(open(a)).items():
            for arq, v in arqs.items():
                sc.setdefault(conj, {}).setdefault(arq, {}).update(v)
    itens = [json.loads(l) for l in open(MANIFESTO_SESSOES) if l.strip()]
    pos = {}
    for it in itens:
        if it["label"] == "pos":
            pos.setdefault(it["name"], []).append(it)
    arqs_sess = set(sc.get("sessoes", {}))
    n_res = sum(1 for i in itens if i["label"] == "pos" and i["split"] == "holdout" and i["name"] in arqs_sess)
    cham = chamadas()
    um = next(iter(next(iter(sc.values())).values()))
    nomes = [k for k in um if k != "dur"]
    linhas = [medir(sc, n, pos, n_res, cham) for n in nomes]
    cols = list(linhas[0])
    print("\t".join(cols))
    for l in linhas:
        print("\t".join(str(l.get(c, "")) for c in cols))
    if csv:
        with open(csv, "w") as f:
            f.write(",".join(cols) + "\n")
            for l in linhas:
                f.write(",".join(str(l.get(c, "")) for c in cols) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
