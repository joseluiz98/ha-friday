"""Filtra na VM as falas brutas dos negativos do XTTS (06/11/2026): o Whisper na GPU do Kaggle falhou nas 600 falas
(asr: RuntimeError), e a geração em si saiu boa. Mesma regra do notebook (neg_rules.py), na imagem friday-asr.
Uso: filtrar_xtts_neg.py <pasta_da_geracao>   -> grava aceitas/ e atualiza manifesto.json (backup manifesto.json.bak)"""
import collections, json, os, shutil, sys

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel
from scipy.signal import resample_poly

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from neg_rules import aceita


def main(pasta):
    mp = os.path.join(pasta, "manifesto.json")
    if not os.path.exists(mp + ".bak"):
        shutil.copy2(mp, mp + ".bak")
    m = json.load(open(mp))
    os.makedirs(os.path.join(pasta, "aceitas"), exist_ok=True)
    asr = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=int(os.environ.get("THREADS", "3")),
                       download_root="/models")
    for g in m["geradas"]:
        g.pop("motivo", None)
        if not g.get("bruta"):
            continue
        a, sr = sf.read(os.path.join(pasta, "brutas", g["bruta"]), dtype="float32")
        if a.ndim > 1:
            a = a.mean(axis=1)
        if sr != 16000:
            a = resample_poly(a, 16000, sr).astype("float32")
        segs, _ = asr.transcribe(a, language="pt", word_timestamps=True, beam_size=5)
        palavras = [w for s in segs for w in (s.words or [])]
        g["transcricao"] = "".join(w.word for w in palavras).strip()
        if not palavras or not aceita(g["texto"], g["transcricao"]):
            g["motivo"] = "transcrição"
            continue
        i0, i1 = max(0.0, palavras[0].start - 0.05), min(len(a) / 16000, palavras[-1].end + 0.20)
        a = a[int(i0 * 16000):int(i1 * 16000)].copy()
        if len(a) < 0.25 * 16000:
            g["motivo"] = "curta"
            continue
        n0, n1 = int(0.005 * 16000), min(len(a), int(0.04 * 16000))
        a[:n0] *= np.linspace(0, 1, n0)
        a[-n1:] *= np.linspace(1, 0, n1)
        a = np.concatenate([np.zeros(int(0.10 * 16000), dtype="float32"), a])
        g["arquivo"] = f"xttsneg__{g['falante']}_{g['take']}.wav"
        sf.write(os.path.join(pasta, "aceitas", g["arquivo"]), np.clip(a, -1, 1), 16000, subtype="PCM_16")
        g["dur_s"] = round(len(a) / 16000, 2)
    json.dump(m, open(mp, "w"), ensure_ascii=False, indent=1)
    ac = [g for g in m["geradas"] if "arquivo" in g]
    print("aceitas", len(ac), "de", len(m["geradas"]), "| vozes", len({g["falante"] for g in ac}))
    print("por texto", collections.Counter(g["texto"] for g in ac).most_common())
    print("rejeições", collections.Counter(g.get("motivo") or "erro" for g in m["geradas"] if "arquivo" not in g))


if __name__ == "__main__":
    main(sys.argv[1])
