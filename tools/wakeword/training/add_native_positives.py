"""Acrescenta positivos pt-BR nativos a um ciclo da Sexta-feira já montado (27/10/2026).

Fontes (aprovadas pelo o dono na escuta):
  - recortes reais do Common Voice 27.0 (raw/cv27_sexta; só os que a retranscrição confirma, `confere`);
  - falas XTTS-v2 recortadas (xtts_gen_20261027/recorte_v3; classes estrita e variante, menos as `excluida`).
A separação treino/prova é POR FALANTE (sorteio com semente): a prova mede vozes que o modelo nunca ouviu.
Treino vai para <ciclo>/upload/positive com prefixo de etiqueta (peso em params real_copies_by_tag):
  cv27__  xtts__  xttsvar__
Prova vai para <ciclo>/holdout_native/{cv27,xtts,xttsvar}/ e nunca sobe.
Uso: add_native_positives.py <ciclo> <cv_dir> <xtts_dir> --seed N [--cv-prova 0.2] [--xtts-prova 0.05]
"""
import argparse, hashlib, json, os, random, shutil, sys


def sorteia_falantes(falantes, frac, seed, sal):
    fs = sorted(set(falantes))
    r = random.Random(f"{seed}-{sal}")
    r.shuffle(fs)
    return set(fs[:max(1, round(len(fs) * frac))]) if fs and frac > 0 else set()


def itens_cv(cv_dir):
    for m in json.load(open(os.path.join(cv_dir, "manifesto.json"))):
        if m.get("confere"):
            yield {"etq": "cv27", "falante": m["falante"], "genero": m.get("genero"),
                   "orig": os.path.join(cv_dir, m["arquivo"]), "nome": f"cv27__{m['arquivo']}"}


def itens_xtts(xtts_dir):
    for i in json.load(open(os.path.join(xtts_dir, "manifesto.json")))["itens"]:
        if not i.get("classe") or i.get("excluida"):
            continue
        etq = "xtts" if i["classe"] == "estrita" else "xttsvar"
        yield {"etq": etq, "falante": i["falante"], "genero": i.get("genero"),
               "orig": os.path.join(xtts_dir, i["arquivo"]), "nome": f"{etq}__{i['falante']}_{i['take']}.wav"}


def sha1(p):
    return hashlib.sha1(open(p, "rb").read()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ciclo"); ap.add_argument("cv_dir"); ap.add_argument("xtts_dir")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--cv-prova", type=float, default=0.2)
    ap.add_argument("--xtts-prova", type=float, default=0.05)
    a = ap.parse_args(argv)
    pos = os.path.join(a.ciclo, "upload", "positive")
    if not os.path.isdir(pos):
        print(f"sem {pos} (rode o build_dataset antes)", file=sys.stderr)
        return 2
    cv, xt = list(itens_cv(a.cv_dir)), list(itens_xtts(a.xtts_dir))
    prova_cv = sorteia_falantes([i["falante"] for i in cv], a.cv_prova, a.seed, "cv27")
    prova_xt = sorteia_falantes([i["falante"] for i in xt], a.xtts_prova, a.seed, "xtts")
    registro, cont = [], {}
    for i in cv + xt:
        prova = i["falante"] in (prova_cv if i["etq"] == "cv27" else prova_xt)
        destino = os.path.join(a.ciclo, "holdout_native", i["etq"]) if prova else pos
        os.makedirs(destino, exist_ok=True)
        shutil.copy(i["orig"], os.path.join(destino, i["nome"]))
        lado = "holdout" if prova else "train"
        registro.append({**{k: i[k] for k in ("etq", "falante", "genero", "nome")}, "split": lado, "sha1": sha1(i["orig"])})
        cont.setdefault(i["etq"], {"train": 0, "holdout": 0})[lado] += 1
    # vazamento: nenhum falante da prova no treino, nenhum arquivo dos dois lados
    tr = {(r["etq"], r["falante"]) for r in registro if r["split"] == "train"}
    ho = {(r["etq"], r["falante"]) for r in registro if r["split"] == "holdout"}
    sha_tr = {r["sha1"] for r in registro if r["split"] == "train"}
    sha_ho = {r["sha1"] for r in registro if r["split"] == "holdout"}
    if tr & ho or sha_tr & sha_ho:
        print(f"VAZAMENTO: falantes {sorted(tr & ho)[:5]} arquivos {len(sha_tr & sha_ho)}", file=sys.stderr)
        return 3
    with open(os.path.join(a.ciclo, "native.ndjson"), "w") as f:
        for r in registro:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    cpath = os.path.join(a.ciclo, "counts.json")
    c = json.load(open(cpath)) if os.path.exists(cpath) else {}
    c["positivos_nativos"] = {"por_etiqueta": cont, "seed": a.seed,
                              "falantes_prova": {"cv27": len(prova_cv), "xtts": len(prova_xt)}}
    json.dump(c, open(cpath, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(cont, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
