"""Ciclo derivado com os trechos só de música como negativos (26/10/2026), sem refazer o harvest. Roda no HA.

Uso: add_music_negatives.py <ciclo_origem> <ciclo_novo> <pasta_das_sessoes_cruas>
Copia o ciclo de origem (upload, holdout, manifest, counts) para o ciclo novo e, em cada sessão guiada COM MÚSICA que
está no lado de treino, corta os negativos de dataset_rules.negativos_musica (a mesma regra que o build_dataset usa
daqui em diante) a partir do WAV cru. O params.json do upload vem de params/<modelo>.json (pesos por etiqueta).
Sessão da prova não gera nada: a prova continua intocada."""
import array, json, math, os, re, shutil, sys, wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dataset_rules as dr


def safe(iid):  # igual ao build_dataset.safe (lá ele vem com numpy, que o HA não tem)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", iid.replace("@", "_at_"))


def main(origem, novo, cruas):
    if os.path.exists(novo):
        print(f"{novo} já existe: não sobrescrevo", file=sys.stderr)
        return 2
    shutil.copytree(origem, novo, ignore=shutil.ignore_patterns("models*", "eval_*", "kaggle_last.log"))
    man = [json.loads(l) for l in open(f"{novo}/manifest.ndjson") if l.strip()]
    params = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "params", "friday.json")))
    sessoes = sorted({i["name"] for i in man if "_music_" in i["name"] and i["src"] == "sessao"})
    novos, por_sessao = [], {}
    for nome in sessoes:
        itens = [i for i in man if i["name"] == nome]
        lados = {i["split"] for i in itens}
        assert len(lados) == 1, (nome, lados)
        if lados != {"train"}:
            por_sessao[nome] = "prova (nada)"
            continue
        assert not any(i["label"] == "neg" for i in itens), f"{nome} já tem negativos"
        with wave.open(os.path.join(cruas, nome.split("__", 1)[1])) as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
            audio, dur = w.readframes(w.getnframes()), w.getnframes() / 16000
        amostras = array.array("h", audio)
        h = int(dr.ENV_S * 16000)
        env = [math.sqrt(sum(x * x for x in amostras[j:j + h]) / h) for j in range(0, len(amostras) - h + 1, h)]
        fim_pos = {i["k"]: i["fim"] for i in itens if i["label"] == "pos"}
        ep = itens[0]["episode"]
        for k, a, b in dr.negativos_musica(dr.recortes_tentativas(dur), fim_pos):
            if not dr.trecho_limpo(env, a, b):
                continue
            it = {"id": f"{nome}@{a:.2f}", "name": nome, "src": "sessao", "ini": a, "fim": b, "label": "neg",
                  "why": "música sem a palavra", "k": k, "episode": ep, "split": "train"}
            with wave.open(f"{novo}/upload/negative/{safe(it['id'])}.wav", "wb") as o:
                o.setnchannels(1); o.setsampwidth(2); o.setframerate(16000)
                o.writeframes(audio[int(a * 16000) * 2:int(b * 16000) * 2])
            novos.append(it)
        por_sessao[nome] = sum(1 for n in novos if n["name"] == nome)
    ids = {i["id"] for i in man}
    assert not any(n["id"] in ids for n in novos), "id repetido"
    with open(f"{novo}/manifest.ndjson", "w") as f:
        for it in man + novos:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    json.dump(dr.counts(man + novos), open(f"{novo}/counts.json", "w"), ensure_ascii=False, indent=1)
    json.dump(params, open(f"{novo}/upload/params.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(por_sessao, ensure_ascii=False, indent=1))
    print(f"negativos de música: {len(novos)}; upload: {len(os.listdir(novo + '/upload/positive'))} positivos, "
          f"{len(os.listdir(novo + '/upload/negative'))} negativos")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
