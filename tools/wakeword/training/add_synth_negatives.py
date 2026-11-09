"""Acrescenta a um ciclo da Sexta-feira os negativos nativos falados pelo XTTS (06/11/2026).
Fonte: pasta da geração (aceitas/ + manifesto.json, ver tts_samples/run_xtts_neg.sh), depois da escuta do dono; fala
reprovada recebe "excluida": true no manifesto. A prova é POR FALANTE: <ciclo>/holdout_native/xttsneg/ nunca sobe e mede
falsos com vozes novas. Treino: <ciclo>/upload/negative/xttsneg__*.wav (peso em real_copies_by_tag).
Uso (depois do add_native_positives, que reescreve o native.ndjson):
  add_synth_negatives.py <ciclo> <pasta_da_geracao> --seed N [--prova 0.05]"""
import argparse, json, os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from add_native_positives import sorteia_falantes, sha1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ciclo"); ap.add_argument("geracao")
    ap.add_argument("--seed", type=int, required=True); ap.add_argument("--prova", type=float, default=0.05)
    a = ap.parse_args(argv)
    neg = os.path.join(a.ciclo, "upload", "negative")
    if not os.path.isdir(neg):
        print(f"sem {neg} (rode o build_dataset antes)", file=sys.stderr)
        return 2
    itens = [g for g in json.load(open(os.path.join(a.geracao, "manifesto.json")))["geradas"]
             if g.get("arquivo") and not g.get("excluida")]
    falta = [g["arquivo"] for g in itens if not os.path.exists(os.path.join(a.geracao, "aceitas", g["arquivo"]))]
    if falta:
        print(f"arquivos do manifesto ausentes: {falta[:5]}", file=sys.stderr)
        return 2
    prova = sorteia_falantes([g["falante"] for g in itens], a.prova, a.seed, "xttsneg")
    registro = []
    for g in itens:
        lado = "holdout" if g["falante"] in prova else "train"
        destino = os.path.join(a.ciclo, "holdout_native", "xttsneg") if lado == "holdout" else neg
        os.makedirs(destino, exist_ok=True)
        orig = os.path.join(a.geracao, "aceitas", g["arquivo"])
        shutil.copy(orig, os.path.join(destino, g["arquivo"]))
        registro.append({"etq": "xttsneg", "falante": g["falante"], "nome": g["arquivo"], "texto": g.get("texto"),
                         "split": lado, "sha1": sha1(orig)})
    npath = os.path.join(a.ciclo, "native.ndjson")
    outros = []
    if os.path.exists(npath):
        outros = [l for l in open(npath) if l.strip() and json.loads(l).get("etq") != "xttsneg"]
    with open(npath, "w") as f:
        f.writelines(outros)
        for r in registro:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    cpath = os.path.join(a.ciclo, "counts.json")
    c = json.load(open(cpath)) if os.path.exists(cpath) else {}
    c["negativos_sinteticos"] = {"train": sum(r["split"] == "train" for r in registro),
                                 "holdout": sum(r["split"] == "holdout" for r in registro),
                                 "falantes_prova": len(prova), "seed": a.seed}
    json.dump(c, open(cpath, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(c["negativos_sinteticos"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
