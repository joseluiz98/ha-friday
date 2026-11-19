"""Monta o dataset de uma volta (spec/plano 2026-10-24, Task 4). Roda na VM, na imagem friday-asr:1 (numpy + wave).
Uso: build_dataset.py <modelo> <work> <out> --seed N [--duplicates N] [--params-dir DIR] [--rulings ARQ] [--ambient PASTA]...
                       [--pin-from MANIFEST]... [--office SAT_ID]
Lê <work>/{sources,transcricoes,scores}.ndjson, marcas.json e <work>/wav/<name>; escreve em <out>:
  manifest.ndjson, upload/{positive,negative}/*.wav, upload/params.json, holdout/<name>, counts.json.
Para o Kaggle sobe SÓ upload/ (recortes de treino); manifest, holdout e counts nunca saem de casa.
18/11/2026: cada item do manifest leva "sat" (o satélite do clipe). Nomes legados (sem `<sat_id>__`) são do satélite do
escritório: --office, senão FRIDAY_SAT_OFFICE (ambiente ou $FRIDAY_LOCAL_CONFIG); --pin-from e --rulings comparam por
dataset_rules.clip_key. Nome legado sem o id do escritório: sai 2."""
import argparse, hashlib, json, os, re, shutil, sys, wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dataset_rules as dr


def read_ndjson(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def read_wav(path):
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, path
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def envelope(path):
    """RMS em quadros de dr.ENV_S s e o piso de ruído (percentil 20) de um WAV, para dr.fala_na_janela."""
    a = read_wav(path).astype(np.float32)
    h = int(dr.ENV_S * 16000)
    n = len(a) // h
    rms = np.sqrt((a[:n * h].reshape(n, h) ** 2).mean(axis=1))
    return {"frames": [float(x) for x in rms], "floor": float(np.percentile(rms, 20)) if n else 0.0}


def write_wav(path, a):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(np.asarray(a, dtype=np.int16).tobytes())


def safe(iid):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", iid.replace("@", "_at_"))


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("model"); ap.add_argument("work"); ap.add_argument("out")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--duplicates", type=int, default=0)
    ap.add_argument("--pin-from", action="append", default=[],
                    help="manifest.ndjson de um ciclo anterior (repetível): arquivos já usados mantêm o lado")
    ap.add_argument("--rulings", help="rulings.json do modelo: \"excluidas\" = arquivos que ficam fora (02/11)")
    ap.add_argument("--ambient", action="append", default=[],
                    help="raw/<data>_ambient_calls (repetível): chamadas reais que entram por add_ambient_positives.py")
    ap.add_argument("--office", help="sat_id do satélite do escritório (dono dos nomes legados); padrão FRIDAY_SAT_OFFICE")
    ap.add_argument("--params-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "params"))
    a = ap.parse_args(argv)
    try:
        return _main(a)
    except ValueError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 2


def _main(a):
    W, O = a.work, a.out
    office = a.office or dr.office_id()
    fontes = {s["name"]: s for s in read_ndjson(f"{W}/sources.ndjson")}
    trans = {t["arq"]: t["palavras"] for t in read_ndjson(f"{W}/transcricoes.ndjson")}
    notas = {s["name"]: s for s in read_ndjson(f"{W}/scores.ndjson")}
    marcas = json.load(open(f"{W}/marcas.json"))
    itens, sem_dados = [], []
    for name, s in fontes.items():
        if name not in trans or name not in notas:
            sem_dados.append(name)
            continue
        env = envelope(f"{W}/wav/{name}") if "tentativas_" in name else None
        itens += dr.items_for(s, trans[name], notas[name], a.model, marcas if name.endswith("sessao.wav") else None, env)
    if sem_dados:
        print(f"AVISO: {len(sem_dados)} arquivos sem transcrição/nota (ignorados): {sem_dados[:5]}", file=sys.stderr)
    if a.rulings and os.path.exists(a.rulings):
        n = dr.apply_exclusions(itens, json.load(open(a.rulings)).get("excluidas", []), office=office)
        print(f"excluídos pelo rulings: {n} itens", file=sys.stderr)
    info = {n: {"ts": fontes[n]["ts"], "dur": notas[n]["dur"], "src": fontes[n]["src"]} for n in notas if n in fontes}
    chamadas = []
    for pasta in a.ambient:
        for c in json.load(open(f"{pasta}/manifesto.json"))["itens"]:
            d = c["dia"]
            chamadas.append(f"{d[:4]}-{d[4:6]}-{d[6:]}T{c['hora']}")
    n = dr.protege_chamadas(itens, info, chamadas)
    print(f"protegidos por chamadas reais: {n} itens", file=sys.stderr)
    eps = dr.episodes(itens, info)
    lado = dr.split(itens, eps, a.model, a.seed)
    if a.pin_from:
        ms = [read_ndjson(p) for p in a.pin_from]
        anteriores = dr.prev_sides(ms, office=office)
        lado = dr.pin_split(lado, itens, eps, anteriores, dr.prev_item_sides(ms, office=office), office=office)
        fixados = sum(1 for it in itens if dr.clip_key(it["name"], office) in anteriores)
        print(f"lados fixados pelos ciclos anteriores: {fixados} itens de {len(anteriores)} arquivos", file=sys.stderr)
    for it in itens:
        it["episode"], it["split"] = eps[it["id"]], lado[it["id"]]
        it["sat"] = fontes[it["name"]].get("sat") or dr.clip_sat(it["name"], office)
    subir = dr.upload_ids(itens)

    shutil.rmtree(O, ignore_errors=True)
    for d in ("upload/positive", "upload/negative", "holdout"):
        os.makedirs(f"{O}/{d}", mode=0o700, exist_ok=True)
    cache = {}
    for it in itens:
        if it["id"] not in subir:
            continue
        if it["name"] not in cache:
            cache = {it["name"]: read_wav(f"{W}/wav/{it['name']}")}
        audio = cache[it["name"]]
        b0, b1 = dr.cut_bounds(it, len(audio) / 16000)
        pasta = "positive" if it["label"] == "pos" else "negative"
        write_wav(f"{O}/upload/{pasta}/{safe(it['id'])}.wav", audio[int(b0 * 16000):int(b1 * 16000)])
    hold = sorted({it["name"] for it in itens if it["split"] == "holdout"})
    for name in hold:
        shutil.copy(f"{W}/wav/{name}", f"{O}/holdout/{name}")
    params = json.load(open(f"{a.params_dir}/{a.model}.json"))
    json.dump(params, open(f"{O}/upload/params.json", "w"), ensure_ascii=False, indent=1)
    with open(f"{O}/manifest.ndjson", "w") as f:
        for it in itens:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    c = dr.counts(itens, a.duplicates)
    json.dump(c, open(f"{O}/counts.json", "w"), ensure_ascii=False, indent=1)

    # confere o vazamento (Foco 4): nenhum arquivo da reserva é fonte de item de treino enviado
    sha_hold = {sha1(f"{O}/holdout/{n}") for n in hold}
    vazou = sorted({it["name"] for it in itens if it["id"] in subir and fontes[it["name"]]["sha1"] in sha_hold
                    and it["name"] not in hold}) if a.model == "friday" else []
    mesmo_arquivo = sorted({it["name"] for it in itens if it["id"] in subir and it["name"] in hold})
    if a.model == "friday" and (vazou or mesmo_arquivo):
        print(f"VAZAMENTO: {vazou} {mesmo_arquivo}", file=sys.stderr)
        return 3
    print(json.dumps(c, ensure_ascii=False, indent=1))
    print(f"upload: {len(list(os.scandir(O + '/upload/positive')))} positivos, "
          f"{len(list(os.scandir(O + '/upload/negative')))} negativos; reserva: {len(hold)} arquivos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
