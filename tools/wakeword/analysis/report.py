"""Relatório do falso negativo da palavra (24/10/2026): cruza a transcrição dos `contexto_*.wav` com o registro da Pi
(`falas_palavra.ndjson`, `eventos_palavra.ndjson`) e com a repontuação offline (rescore.py).

Uso: report.py <pasta_dados> [--modelo NOME]   (a pasta tem os 4 arquivos que run.sh traz)
Leitura: "Friday" transcrito que não ativou, com nota offline < 0,3 = o modelo base não ouviu (retreino);
0,3–0,6 = limiar/quase; barrada = verificador. Offline ≈ ao vivo descarta o pipeline da Pi."""
import argparse, collections, datetime as dt, json, os, re, statistics

WAKE = re.compile(r"^(friday|freddy|freddie|fred|firo)$", re.I)
FRAME_S = 0.08
CONTEXT_S = 30.0


def load(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def file_time(name):
    """contexto_20261021_202454.wav -> instante da ativação (fim do áudio)."""
    return dt.datetime.strptime(re.search(r"(\d{8}_\d{6})", name).group(1), "%Y%m%d_%H%M%S")


def window_max(scores, start, end):
    """Maior nota offline do quadro que cobre a palavra até 1,2 s depois (o modelo responde com atraso)."""
    k0, k1 = max(int((start - 0.2) / FRAME_S), 0), int((end + 1.2) / FRAME_S) + 1
    return max(scores[k0:k1] or [0.0])


def wake_words(transcripts, rescored):
    out = []
    for t in transcripts:
        s = rescored.get(t["arq"])
        dur = len(s) * FRAME_S if s else CONTEXT_S
        end = file_time(t["arq"])
        for w in t["palavras"]:
            if WAKE.match(w["w"].strip(" ,.!?¿¡")):
                out.append({"t": end - dt.timedelta(seconds=dur - w["i"]), "arq": t["arq"], "i": w["i"], "p": w["p"],
                            "offline": window_max(s, w["i"], w["f"]) if s else None,
                            "ativadora": dur - w["i"] <= 2.2})
    return sorted(out, key=lambda x: x["t"])


def dedupe(words, gap=0.5):
    """Contextos se sobrepõem (ativações a menos de 30 s): a mesma palavra aparece em dois arquivos."""
    out = []
    for w in words:
        if out and abs((w["t"] - out[-1]["t"]).total_seconds()) < gap:
            out[-1]["ativadora"] = out[-1]["ativadora"] or w["ativadora"]
            continue
        out.append(dict(w))
    return out


def classify(word, events):
    """Desfecho ao vivo de uma palavra: o evento da Pi logo depois dela (até 3 s)."""
    if word["ativadora"]:
        return "ativou"
    for e in events:
        d = (e["t"] - word["t"]).total_seconds()
        if -0.5 <= d <= 3.0:
            if e["tipo"] in ("ativou", "sussurro"):
                return "ativou"
            if e["tipo"] == "barrada_verificador" and e["voz"] >= 0.5:
                return "barrada"
            if e["tipo"] == "quase":
                return "quase"
    return "nada"


def band(x):
    return "sem" if x is None else "<0,3" if x < 0.3 else "0,3–0,6" if x < 0.6 else ">=0,6"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dados")
    ap.add_argument("--desde", default="")
    a = ap.parse_args(argv)
    j = lambda n: os.path.join(a.dados, n)
    events = [dict(e, t=dt.datetime.fromisoformat(e["ts"][:19])) for e in load(j("eventos_palavra.ndjson"))
              if e["ts"] >= a.desde]
    rescored = {r["arq"]: r["s"] for r in load(j("rescored.ndjson"))}
    words = dedupe(wake_words(load(j("transcricoes.ndjson")), rescored))
    by = collections.defaultdict(list)
    for w in words:
        by[classify(w, events)].append(w)
    print(f"contextos: {len(load(j('transcricoes.ndjson')))}  'Friday' distintos: {len(words)}")
    for k in ("ativou", "nada", "quase", "barrada"):
        ws = by.get(k, [])
        offs = [w["offline"] for w in ws if w["offline"] is not None]
        print(f"  {k:8s} {len(ws):3d}  nota offline: {dict(collections.Counter(band(w['offline']) for w in ws))}"
              + (f"  mediana {statistics.median(offs):.2f}" if offs else ""))
    perdidas = [w for w in words if classify(w, events) != "ativou"]
    if perdidas:
        surdo = sum(1 for w in perdidas if w["offline"] is not None and w["offline"] < 0.3)
        print(f"perdidas: {len(perdidas)}; modelo base < 0,3: {surdo} ({100 * surdo / len(perdidas):.0f}%)")
    print("\nperdidas (para ouvir / usar como positivas no retreino):")
    for w in perdidas:
        print(f"  {w['arq']}  {w['i']:5.1f}s  whisper p={w['p']:.2f}  offline={w['offline']}")


if __name__ == "__main__":
    main()
