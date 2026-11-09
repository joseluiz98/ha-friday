"""Acrescenta a um ciclo da Sexta-feira as chamadas reais achadas no áudio de ambiente (06/11/2026).

O harvest da Sexta só lista sessões guiadas e sombra; as chamadas do dia a dia (perdidas e atendidas) nascem daqui.
Cada dia tem uma pasta em /share/wakeword-data/sexta_feira/raw/<data>_ambient_calls/ com treino/<nome>.wav e
manifesto.json. O lado de cada episódio é fixo no manifesto (nunca muda entre ciclos). Só entra status "ok":
"pendente_escuta" e "excluida" ficam fora, nem treino nem prova.
Treino vai para <ciclo>/upload/positive/ambiente__<dia>_<nome>.wav (sem etiqueta em real_copies_by_tag = real_pos_copies);
prova vai para <ciclo>/holdout_native/ambiente/ e nunca sobe.
Uso (depois do build_dataset e do add_native_positives, que reescreve o native.ndjson):
  add_ambient_positives.py <ciclo> <pasta_do_dia> [<pasta_do_dia> ...]
Saída: 0 ok; 2 ciclo sem upload/; 3 vazamento (nada é copiado)."""
import hashlib, json, os, shutil, sys


def sha1(p):
    with open(p, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def itens_de(pasta):
    for i in json.load(open(os.path.join(pasta, "manifesto.json")))["itens"]:
        yield pasta, i


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ciclo, pastas = argv[0], argv[1:]
    pos = os.path.join(ciclo, "upload", "positive")
    if not os.path.isdir(pos):
        print(f"sem {pos} (rode o build_dataset antes)", file=sys.stderr)
        return 2
    todos = [x for p in pastas for x in itens_de(p)]
    bons = [(p, i, sha1(os.path.join(p, "treino", i["nome"] + ".wav"))) for p, i in todos if i["status"] == "ok"]
    lados_ep, lados_sha = {}, {}
    for _, i, s in bons:
        lados_ep.setdefault((i["dia"], i["episodio"]), set()).add(i["lado"])
        lados_sha.setdefault(s, set()).add(i["lado"])
    vaz_ep = [k for k, v in lados_ep.items() if len(v) > 1]
    vaz_sha = [k for k, v in lados_sha.items() if len(v) > 1]
    if vaz_ep or vaz_sha:
        print(f"VAZAMENTO: episódios {vaz_ep[:5]} arquivos {len(vaz_sha)}", file=sys.stderr)
        return 3
    registro = []
    for p, i, s in bons:
        destino = pos if i["lado"] == "train" else os.path.join(ciclo, "holdout_native", "ambiente")
        os.makedirs(destino, exist_ok=True)
        nome = f"ambiente__{i['dia']}_{i['nome']}.wav"
        shutil.copy(os.path.join(p, "treino", i["nome"] + ".wav"), os.path.join(destino, nome))
        registro.append({"etq": "ambiente", "nome": nome, "split": i["lado"], "episodio": i["episodio"],
                         "desfecho": i.get("desfecho"), "causa": i.get("causa"), "sha1": s})
    npath = os.path.join(ciclo, "native.ndjson")
    outros = []
    if os.path.exists(npath):
        outros = [l for l in open(npath) if l.strip() and json.loads(l).get("etq") != "ambiente"]
    with open(npath, "w") as f:
        f.writelines(outros)
        for r in registro:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    cpath = os.path.join(ciclo, "counts.json")
    c = json.load(open(cpath)) if os.path.exists(cpath) else {}
    c["positivos_ambiente"] = {"train": sum(r["split"] == "train" for r in registro),
                               "holdout": sum(r["split"] == "holdout" for r in registro),
                               "fora": len(todos) - len(registro)}
    json.dump(c, open(cpath, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(c["positivos_ambiente"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
