"""Notebook de GERAÇÃO de NEGATIVOS nativos com XTTS-v2 (06/11/2026): as frases de `ptbr_neg_texts` ditas pelas 300 vozes
pt-BR do dataset joseluiz98/ptbr-voice-refs, as mesmas dos positivos. Os dias da semana pesam 3× no sorteio.
Mesma conferência de marca da geração dos positivos (célula 1). O filtro é o neg_rules.py, embutido aqui.
  python3 build_xtts_neg.py <pasta> <marca_do_dataset> <run_id> [takes]"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_xtts_gen as g

KERNEL = "joseluiz98/xtts-sexta-neg-gen"
QUI = os.path.dirname(os.path.abspath(__file__))

C2 = """# 2. XTTS-v2: TAKES falas por voz, frases parecidas com a palavra
import json, random, torch
os.environ['COQUI_TOS_AGREED'] = '1'
from TTS.api import TTS
TEXTOS = %(textos)s
PESOS = [3 if '-feira' in t else 1 for t in TEXTOS]
xt = TTS('tts_models/multilingual/multi-dataset/xtts_v2').to('cuda' if torch.cuda.is_available() else 'cpu')
rnd = random.Random(20261106)
geradas = []
os.makedirs(f'{OUT}/brutas', exist_ok=True)  # na saída: se o filtro falhar, a geração não se perde
for v in vozes:
    for t in range(TAKES):
        txt = rnd.choices(TEXTOS, PESOS)[0]
        p = f"{OUT}/brutas/{v['falante']}_{t}.wav"
        try:
            xt.tts_to_file(text=txt, speaker_wav=v['caminhos'], language='pt', file_path=p, split_sentences=False)
            geradas.append({'falante': v['falante'], 'genero': v['genero'], 'take': t, 'texto': txt, 'bruta': p})
        except Exception as e:
            geradas.append({'falante': v['falante'], 'take': t, 'texto': txt, 'erro': f'{type(e).__name__}: {str(e)[:200]}'})
print('  geradas:', sum(1 for g in geradas if 'bruta' in g), '| erros:', sum(1 for g in geradas if 'erro' in g))"""

C3 = """# 3. filtro: transcrição próxima do texto e sem a palavra; aparo, 0,10 s de silêncio antes, fades; saída 16 kHz
import collections
import numpy as np, soundfile as sf
from scipy.signal import resample_poly
from faster_whisper import WhisperModel
%(regras)s
try:
    asr = WhisperModel('small', device='cuda', compute_type='float16')
except Exception as e:
    print('  whisper na CPU:', e); asr = WhisperModel('small', device='cpu', compute_type='int8')
for g in geradas:
    if 'bruta' not in g:
        continue
    a, sr = sf.read(g['bruta'], dtype='float32')
    if a.ndim > 1: a = a.mean(axis=1)
    if sr != 16000: a = resample_poly(a, 16000, sr).astype('float32')
    try:
        segs, _ = asr.transcribe(a, language='pt', word_timestamps=True, beam_size=5)
        palavras = [w for s in segs for w in (s.words or [])]
    except Exception as e:
        g['motivo'] = f'asr: {type(e).__name__}'; continue
    g['transcricao'] = ''.join(w.word for w in palavras).strip()
    if not palavras or not aceita(g['texto'], g['transcricao']):
        g['motivo'] = 'transcrição'; continue
    i0, i1 = max(0.0, palavras[0].start - 0.05), min(len(a) / 16000, palavras[-1].end + 0.20)
    a = a[int(i0 * 16000):int(i1 * 16000)].copy()
    if len(a) < 0.25 * 16000:
        g['motivo'] = 'curta'; continue
    n0, n1 = int(0.005 * 16000), min(len(a), int(0.04 * 16000))
    a[:n0] *= np.linspace(0, 1, n0); a[-n1:] *= np.linspace(1, 0, n1)
    a = np.concatenate([np.zeros(int(0.10 * 16000), dtype='float32'), a])
    g['arquivo'] = f"xttsneg__{g['falante']}_{g['take']}.wav"
    sf.write(f"{OUT}/aceitas/{g['arquivo']}", np.clip(a, -1, 1), 16000, subtype='PCM_16')
    g['dur_s'] = round(len(a) / 16000, 2)
acc = [g for g in geradas if 'arquivo' in g]
print('  aceitas:', len(acc), 'de', len(geradas), '| vozes:', len({g['falante'] for g in acc}),
      '| por texto:', collections.Counter(g['texto'] for g in acc).most_common(8),
      '| rejeições:', collections.Counter(g.get('motivo') or ('erro' if 'erro' in g else '') for g in geradas if 'arquivo' not in g))
for g in geradas: g['bruta'] = os.path.basename(g['bruta']) if 'bruta' in g else None
json.dump({'run_id': RUN_ID, 'marca_dataset': MARCA['marca'], 'geradas': geradas},
          open(f'{OUT}/manifesto.json', 'w'), ensure_ascii=False, indent=1)"""


def regras():
    with open(os.path.join(QUI, "neg_rules.py")) as f:
        src = f.read()
    return src.split('"""', 2)[2].strip()


def textos():
    with open(os.path.join(QUI, "..", "params", "sexta_feira.json")) as f:
        return json.load(f)["ptbr_neg_texts"]


def build(marca, run_id, takes=2):
    sub = {"marca": marca, "run_id": run_id, "takes": takes}
    c2 = C2 % {"textos": json.dumps(textos(), ensure_ascii=False)}
    c3 = C3.replace("%(regras)s", regras())
    return {"cells": [g.code(g.C0), g.code(g.C1 % sub), g.code(c2), g.code(c3)],
            "metadata": g.build(marca, run_id, takes)["metadata"], "nbformat": 4, "nbformat_minor": 5}


def meta():
    m = g.meta()
    m.update(id=KERNEL, title="xtts-sexta-neg-gen")
    return m


if __name__ == "__main__":
    pasta, marca, run_id = sys.argv[1:4]
    takes = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    os.makedirs(pasta, exist_ok=True)
    json.dump(build(marca, run_id, takes), open(os.path.join(pasta, "xtts_gen.ipynb"), "w"), ensure_ascii=False, indent=1)
    json.dump(meta(), open(os.path.join(pasta, "kernel-metadata.json"), "w"), indent=1)
    print("gerado:", pasta, marca, run_id)
