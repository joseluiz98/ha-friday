"""Notebook de GERAÇÃO de "sexta-feira" com XTTS-v2 clonando vozes pt-BR do Common Voice (27/10/2026).
  python3 build_xtts_gen.py <pasta> <marca_do_dataset> <run_id> [takes]
Lê o dataset privado joseluiz98/ptbr-voice-refs (refs/*.wav + vozes.json + marca.json). A célula 1 PARA se a marca
servida não for <marca_do_dataset> ou se a contagem não bater (regra do o dono, 27/10). Cada voz gera `takes` falas;
fica só a fala cuja transcrição (faster-whisper small, pt) é exatamente "sexta-feira"/"sexta feira", aparada no tempo
das palavras e com duração >= 0,6 s (a escuta de 27/10 mostrou cortes que a transcrição sozinha deixa passar)."""
import json, os, sys

KERNEL = "joseluiz98/xtts-sexta-gen"
DATASET = "joseluiz98/ptbr-voice-refs"

C0 = """# 0. instala XTTS (coqui-tts) e faster-whisper
import subprocess, sys
for pk in (['coqui-tts'], ['faster-whisper']):
    r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', *pk], capture_output=True, text=True)
    print(pk, 'rc', r.returncode, r.stderr[-400:])"""

C1 = """# 1. CONFERE O DATASET antes de gastar GPU: marca e contagem
import glob, json, os, zipfile
MARCA_ESPERADA = '%(marca)s'
RUN_ID = '%(run_id)s'
TAKES = %(takes)d
_m = sorted(glob.glob('/kaggle/input/**/marca.json', recursive=True))
assert _m, 'marca.json não encontrado em /kaggle/input'
MARCA = json.load(open(_m[0]))
DIR = os.path.dirname(_m[0])
print('  marca servida:', MARCA, '| esperada:', MARCA_ESPERADA)
assert MARCA['marca'] == MARCA_ESPERADA, 'DATASET ERRADO: a marca servida não é a esperada'
for z in glob.glob(f'{DIR}/*.zip'):
    zipfile.ZipFile(z).extractall('/kaggle/tmp/refs_data')
REFS = '/kaggle/tmp/refs_data' if os.path.isdir('/kaggle/tmp/refs_data') else DIR
wavs = glob.glob(f'{REFS}/**/*.wav', recursive=True)
vozes = json.load(open(f'{DIR}/vozes.json'))
print(f'  {len(wavs)} wav de referência, {len(vozes)} vozes')
assert len(wavs) + 1 == MARCA['arquivos'], f'contagem não bate: {len(wavs)} wav + vozes.json != {MARCA["arquivos"]}'
por_nome = {os.path.basename(w): w for w in wavs}
for v in vozes:
    v['caminhos'] = [por_nome[a] for a in v['arquivos']]
OUT = '/kaggle/working'
os.makedirs(f'{OUT}/aceitas', exist_ok=True)"""

C2 = """# 2. XTTS-v2: TAKES falas por voz, textos variados
import random, torch
os.environ['COQUI_TOS_AGREED'] = '1'
from TTS.api import TTS
TEXTOS = ['sexta-feira', 'Sexta-feira.', 'Sexta-feira!', 'Sexta-feira?']
xt = TTS('tts_models/multilingual/multi-dataset/xtts_v2').to('cuda' if torch.cuda.is_available() else 'cpu')
rnd = random.Random(20261027)
geradas = []
os.makedirs(f'{OUT}/brutas', exist_ok=True)  # na saída: se o filtro falhar, a geração não se perde
for v in vozes:
    for t in range(TAKES):
        txt = rnd.choice(TEXTOS)
        p = f"{OUT}/brutas/{v['falante']}_{t}.wav"
        try:
            xt.tts_to_file(text=txt, speaker_wav=v['caminhos'], language='pt', file_path=p, split_sentences=False)
            geradas.append({'falante': v['falante'], 'genero': v['genero'], 'take': t, 'texto': txt, 'bruta': p})
        except Exception as e:
            geradas.append({'falante': v['falante'], 'take': t, 'erro': f'{type(e).__name__}: {str(e)[:200]}'})
print('  geradas:', sum(1 for g in geradas if 'bruta' in g), '| erros:', sum(1 for g in geradas if 'erro' in g))"""

C3 = """# 3. filtro: transcrição exata + aparo pelo tempo das palavras + duração mínima; saída 16 kHz
import re
import numpy as np, soundfile as sf
from scipy.signal import resample_poly
from faster_whisper import WhisperModel
try:
    asr = WhisperModel('small', device='cuda', compute_type='float16')
except Exception as e:
    print('  whisper na CPU:', e); asr = WhisperModel('small', device='cpu', compute_type='int8')
OK = re.compile(r'^sexta[- ]?feira[.!?…]*$')
for g in geradas:
    if 'bruta' not in g:
        continue
    # 27/10: o faster-whisper abre arquivo pelo PyAV, que no Kaggle é incompatível (metadata_errors): passa o áudio já
    # lido, a 16 kHz, e uma falha numa fala não derruba as outras
    a, sr = sf.read(g['bruta'], dtype='float32')
    if a.ndim > 1: a = a.mean(axis=1)
    if sr != 16000: a = resample_poly(a, 16000, sr).astype('float32')
    try:
        segs, _ = asr.transcribe(a, language='pt', word_timestamps=True, beam_size=5)
        palavras = [w for s in segs for w in (s.words or [])]
    except Exception as e:
        g['motivo'] = f'asr: {type(e).__name__}'; continue
    g['transcricao'] = ''.join(w.word for w in palavras).strip()
    if not palavras or not OK.match(g['transcricao'].lower()):
        g['motivo'] = 'transcrição'; continue
    i0, i1 = max(0.0, palavras[0].start - 0.15), min(len(a) / 16000, palavras[-1].end + 0.20)
    a = a[int(i0 * 16000):int(i1 * 16000)]
    if len(a) < 0.6 * 16000:
        g['motivo'] = 'curta'; continue
    g['arquivo'] = f"xtts__{g['falante']}_{g['take']}.wav"
    sf.write(f"{OUT}/aceitas/{g['arquivo']}", np.clip(a, -1, 1), 16000, subtype='PCM_16')
    g['dur_s'] = round(len(a) / 16000, 2)
acc = [g for g in geradas if 'arquivo' in g]
import collections
print('  aceitas:', len(acc), 'de', len(geradas), '| vozes com pelo menos 1:', len({g['falante'] for g in acc}),
      '| rejeições:', collections.Counter(g.get('motivo') or ('erro' if 'erro' in g else '') for g in geradas if 'arquivo' not in g))
for g in geradas: g['bruta'] = os.path.basename(g['bruta']) if 'bruta' in g else None
json.dump({'run_id': RUN_ID, 'marca_dataset': MARCA['marca'], 'geradas': geradas},
          open(f'{OUT}/manifesto.json', 'w'), ensure_ascii=False, indent=1)"""


def code(src):
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": src}


def build(marca, run_id, takes=2):
    sub = {"marca": marca, "run_id": run_id, "takes": takes}
    return {"cells": [code(C0), code(C1 % sub), code(C2), code(C3)],
            "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}},
            "nbformat": 4, "nbformat_minor": 5}


def meta():
    return {"id": KERNEL, "title": "xtts-sexta-gen", "code_file": "xtts_gen.ipynb", "language": "python",
            "kernel_type": "notebook", "is_private": True, "enable_gpu": True, "enable_internet": True,
            "dataset_sources": [DATASET], "competition_sources": [], "kernel_sources": []}


if __name__ == "__main__":
    pasta, marca, run_id = sys.argv[1:4]
    takes = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    os.makedirs(pasta, exist_ok=True)
    json.dump(build(marca, run_id, takes), open(os.path.join(pasta, "xtts_gen.ipynb"), "w"), ensure_ascii=False, indent=1)
    json.dump(meta(), open(os.path.join(pasta, "kernel-metadata.json"), "w"), indent=1)
    print("gerado:", pasta, marca, run_id)
