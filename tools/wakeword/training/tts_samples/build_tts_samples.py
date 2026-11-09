"""Notebook de AMOSTRAS de "sexta-feira" com pronúncia nativa (27/10/2026). Não treina nada: gera ~20 falas para o
o dono ouvir antes de qualquer célula de positivos entrar no treino.
  python3 build_tts_samples.py <pasta>  -> <pasta>/tts_samples.ipynb + kernel-metadata.json (kernel joseluiz98/tts-samples-ptbr)
Vozes: leitores do Multilingual LibriSpeech português (CC-BY 4.0), metade F e metade M, 7 s de referência cada.
Sintetizadores: F5-TTS-pt-br (firstpixel, CC-BY-NC-4.0) e XTTS-v2 (Coqui Public Model License, não comercial).
Célula 1 confere o material de entrada (marca RUN_ID, nº de vozes, gêneros, duração) e PARA se não bater."""
import json, os, sys

KERNEL = "joseluiz98/tts-samples-ptbr"

C0 = """# 0. instala (F5-TTS e coqui-tts); cada sintetizador roda isolado, a falha de um não derruba o outro
import subprocess, sys
for pk in (['f5-tts', 'safetensors', 'num2words'], ['coqui-tts']):
    r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', *pk], capture_output=True, text=True)
    print(pk, 'rc', r.returncode, r.stderr[-600:])"""

C1 = """# 1. material de entrada: vozes de referência do MLS português, conferidas antes de gastar GPU
import io, json, os, random, re, tarfile, urllib.request
import numpy as np, soundfile as sf
RUN_ID = '%(run_id)s'
N_VOZES = %(n_vozes)d
OUT = '/kaggle/working'
os.makedirs(f'{OUT}/refs', exist_ok=True)
_api = 'https://huggingface.co/api/datasets/facebook/multilingual_librispeech/tree/main'
_base = 'https://huggingface.co/datasets/facebook/multilingual_librispeech/resolve/main/data/mls_portuguese'
_arqs = json.load(urllib.request.urlopen(f'{_api}/data/mls_portuguese/train/audio', timeout=60))
_meta = urllib.request.urlopen(f'{_base}/metainfo.txt', timeout=60).read().decode('utf-8').splitlines()[1:]
genero = {l.split('|')[0].strip(): l.split('|')[1].strip() for l in _meta if l.count('|') >= 2}
pacotes = {}
for x in _arqs:  # o menor pacote de cada leitor (download rápido)
    n = x['path'].rsplit('/', 1)[1]; s = n.split('_')[0]
    if x.get('size', 0) > 2e5 and (s not in pacotes or x['size'] < pacotes[s][1]):
        pacotes[s] = (n, x['size'])
rnd = random.Random(20261027)
fem = sorted(s for s in pacotes if genero.get(s) == 'F'); mas = sorted(s for s in pacotes if genero.get(s) == 'M')
rnd.shuffle(fem); rnd.shuffle(mas)
escolhidos = fem[:N_VOZES // 2] + mas[:N_VOZES - N_VOZES // 2]
refs = []
for s in escolhidos:
    with urllib.request.urlopen(f'{_base}/train/audio/{pacotes[s][0]}', timeout=600) as r:
        with tarfile.open(fileobj=r, mode='r|gz') as t:
            for m in t:
                if not m.name.endswith('.flac'):
                    continue
                a, sr = sf.read(io.BytesIO(t.extractfile(m).read()), dtype='float32')
                if sr == 16000 and len(a) >= 8 * sr:
                    p = f'{OUT}/refs/{s}.wav'
                    sf.write(p, a[sr // 2: sr // 2 + 7 * sr], 16000)
                    refs.append({'voz': s, 'genero': genero[s], 'ref': p, 'origem': os.path.basename(m.name)})
                    break
print('  RUN_ID', RUN_ID, '| vozes:', [(r['voz'], r['genero']) for r in refs])
assert len(refs) == N_VOZES, f'esperava {N_VOZES} vozes, achei {len(refs)}'
assert len({r['voz'] for r in refs}) == N_VOZES, 'voz repetida'
assert {r['genero'] for r in refs} == {'F', 'M'}, 'faltou um dos gêneros'
for r in refs:
    assert abs(sf.info(r['ref']).duration - 7.0) < 0.01, r
TEXTOS = %(textos)s
json.dump({'run_id': RUN_ID, 'refs': refs, 'textos': TEXTOS}, open(f'{OUT}/entrada.json', 'w'), indent=1)"""

C2 = """# 2. F5-TTS-pt-br (checkpoint v0, mesmo tokenizador do F5-TTS original)
saidas = []
try:
    from huggingface_hub import hf_hub_download
    from f5_tts.api import F5TTS
    ck = hf_hub_download('firstpixel/F5-TTS-pt-br', 'pt-br/model_last.safetensors')
    f5 = F5TTS(model='F5TTS_Base', ckpt_file=ck)
    for i, r in enumerate(refs):
        txt = TEXTOS[i % len(TEXTOS)]
        p = f"{OUT}/f5_{r['voz']}_{r['genero']}.wav"
        f5.infer(ref_file=r['ref'], ref_text='', gen_text=txt, file_wave=p, seed=i)
        saidas.append({'motor': 'f5', 'voz': r['voz'], 'genero': r['genero'], 'texto': txt, 'arquivo': os.path.basename(p)})
    print('  F5 ok:', sum(1 for s in saidas if s['motor'] == 'f5'))
except Exception as e:
    import traceback; traceback.print_exc()
    print('  F5 FALHOU:', type(e).__name__, str(e)[:300])
    saidas.append({'motor': 'f5', 'erro': f'{type(e).__name__}: {str(e)[:300]}'})"""

C3 = """# 3. XTTS-v2 (clonagem com 7 s de referência, idioma pt)
os.environ['COQUI_TOS_AGREED'] = '1'
try:
    import torch
    from TTS.api import TTS
    xt = TTS('tts_models/multilingual/multi-dataset/xtts_v2').to('cuda' if torch.cuda.is_available() else 'cpu')
    for i, r in enumerate(refs):
        txt = TEXTOS[(i + 1) % len(TEXTOS)]
        p = f"{OUT}/xtts_{r['voz']}_{r['genero']}.wav"
        xt.tts_to_file(text=txt, speaker_wav=[r['ref']], language='pt', file_path=p, split_sentences=False)
        saidas.append({'motor': 'xtts', 'voz': r['voz'], 'genero': r['genero'], 'texto': txt, 'arquivo': os.path.basename(p)})
    print('  XTTS ok:', sum(1 for s in saidas if s['motor'] == 'xtts' and 'arquivo' in s))
except Exception as e:
    import traceback; traceback.print_exc()
    print('  XTTS FALHOU:', type(e).__name__, str(e)[:300])
    saidas.append({'motor': 'xtts', 'erro': f'{type(e).__name__}: {str(e)[:300]}'})"""

C4 = """# 4. saída: 16 kHz mono (como o treino usa) + manifesto com a marca
from scipy.signal import resample_poly
for s in saidas:
    if 'arquivo' not in s:
        continue
    p = f"{OUT}/{s['arquivo']}"
    a, sr = sf.read(p, dtype='float32')
    if a.ndim > 1:
        a = a.mean(axis=1)
    if sr != 16000:
        a = resample_poly(a, 16000, sr)
    sf.write(p, a, 16000)
    s['dur_s'] = round(len(a) / 16000, 2)
json.dump({'run_id': RUN_ID, 'saidas': saidas}, open(f'{OUT}/manifesto.json', 'w'), ensure_ascii=False, indent=1)
print(json.dumps({'run_id': RUN_ID, 'ok': sum(1 for s in saidas if 'arquivo' in s),
                  'erros': [s['erro'] for s in saidas if 'erro' in s]}, ensure_ascii=False))"""


def code(src):
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": src}


def build(run_id, n_vozes=10, textos=("sexta-feira", "Sexta-feira!", "sexta-feira?")):
    sub = {"run_id": run_id, "n_vozes": n_vozes, "textos": json.dumps(list(textos), ensure_ascii=False)}
    cells = [code(C0), code(C1 % sub), code(C2), code(C3), code(C4)]
    return {"cells": cells, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}},
            "nbformat": 4, "nbformat_minor": 5}


def meta():
    return {"id": KERNEL, "title": "tts-samples-ptbr", "code_file": "tts_samples.ipynb", "language": "python",
            "kernel_type": "notebook", "is_private": True, "enable_gpu": True, "enable_internet": True,
            "dataset_sources": [], "competition_sources": [], "kernel_sources": []}


if __name__ == "__main__":
    pasta, run_id = sys.argv[1], sys.argv[2]
    os.makedirs(pasta, exist_ok=True)
    json.dump(build(run_id), open(os.path.join(pasta, "tts_samples.ipynb"), "w"), ensure_ascii=False, indent=1)
    json.dump(meta(), open(os.path.join(pasta, "kernel-metadata.json"), "w"), indent=1)
    print("gerado:", pasta, run_id)
