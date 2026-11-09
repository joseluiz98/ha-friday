"""Gera wakeword_v2.ipynb a partir de tools/satelite/treino/friday_kaggle_final.ipynb (spec/plano 2026-10-24, Task 5).
NUNCA editar o .ipynb gerado à mão: mude este script e rode de novo (a saída é determinística).
Uso: python3 build_notebook.py [saida.ipynb]    (padrão: wakeword_v2.ipynb ao lado deste arquivo)
Toda troca de texto é exata e falha (AssertionError) se o texto-alvo não existir: o original mudou, revise."""
import copy, json, os, re, sys

QUI = os.path.dirname(os.path.abspath(__file__))
ORIGEM = os.path.join(QUI, "..", "..", "treino", "friday_kaggle_final.ipynb")


def sub(texto, velho, novo, vezes=1):
    assert texto.count(velho) == vezes, f"texto-alvo ausente ou repetido ({texto.count(velho)}x): {velho[:70]!r}"
    return texto.replace(velho, novo)


def sub_re(texto, padrao, novo):
    r, n = re.subn(padrao, lambda m: novo, texto, flags=re.S)
    assert n == 1, f"padrão {padrao!r} casou {n}x"
    return r


def code(src):
    lin = src.split("\n")
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
            "source": [l + "\n" for l in lin[:-1]] + [lin[-1]]}


def md(src):
    lin = src.split("\n")
    return {"cell_type": "markdown", "metadata": {}, "source": [l + "\n" for l in lin[:-1]] + [lin[-1]]}


MD0 = """# Retreino de palavra de ativação (v2, parametrizado) — versão Kaggle

Derivado de `tools/satelite/treino/friday_kaggle_final.ipynb` por `wakeword-training/kaggle/build_notebook.py` (24/10/2026). **Não editar à mão.**

**O que mudou em relação ao v1 (o resto do treino é o mesmo):**
- Célula `P`: lê `params.json` do dataset privado (palavra, frases, tamanhos) e extrai os recortes reais.
- Célula 10: tudo vem de `PARAMS` (palavra, número de falas, rodadas de aumento, passos).
- Célula `11b`: falas sintéticas em **português** (vozes pt-BR do Piper) além das inglesas.
- Célula `11c`: **gravações reais** do dono como positivos (e negativos reais), repetidas para ter peso.
- Célula 15: grava `metrics.json` em `/kaggle/working`. Célula 16: salva `<palavra>.onnx` e as métricas.
- `smoke: true` no `params.json` roda uma versão minúscula para testar o encanamento.

**Como roda:** `wakeword-training/kaggle_run.sh <modelo> <ciclo>` (CLI do Kaggle, GPU T4, internet ligada)."""

P_CELL = """# P. params (v2): lê o params.json do dataset privado e extrai os recortes reais
import glob, json, os
_p = sorted(glob.glob('/kaggle/input/**/params.json', recursive=True))
assert _p, 'params.json não encontrado em /kaggle/input'
PARAMS = json.load(open(_p[0])); DATA_DIR = os.path.dirname(_p[0])
for z in glob.glob(f'{DATA_DIR}/*.zip'):            # dataset enviado com --dir-mode zip
    os.system(f'cd {DATA_DIR!r} && unzip -oq {os.path.basename(z)!r} -d /content/real_data')
REAL_DIR = '/content/real_data' if os.path.isdir('/content/real_data') else DATA_DIR
# 26/10: a 1ª rodada do ciclo 2 treinou 75 min com a versão ANTERIOR do dataset. Se os recortes não batem com a
# marca do envio (kaggle_run.sh grava "<ciclo>-<data>-<N>p<M>n"), para aqui, antes de gastar GPU.
import re
_m = re.search(r'-(\\d+)p(\\d+)n$', PARAMS.get('upload_id', ''))
_np = len(glob.glob(f'{REAL_DIR}/**/positive/*.wav', recursive=True))
_nn = len(glob.glob(f'{REAL_DIR}/**/negative/*.wav', recursive=True))
print(f'  recortes reais: {_np} positivos, {_nn} negativos; marca: {PARAMS.get("upload_id")}')
assert _m is None or (_np, _nn) == (int(_m[1]), int(_m[2])), f'dataset não bate com a marca: {_np}p{_nn}n'
if PARAMS.get('smoke'):
    PARAMS.update(n_samples=200, n_samples_val=100, augmentation_rounds=1, steps=500)
print('  PARAMS:', {k: v for k, v in PARAMS.items() if k != 'custom_negative_phrases'})"""

C11B = """# 11b pt-BR: falas sintéticas em português (vozes pt-BR do Piper), v2 de 24/10/2026
import json, os, random, subprocess
import yaml
from huggingface_hub import hf_hub_download
with open('/content/my_model.yaml') as f:
    cfg = yaml.safe_load(f)
VOZES = ['pt/pt_BR/faber/medium/pt_BR-faber-medium', 'pt/pt_BR/cadu/medium/pt_BR-cadu-medium',
         'pt/pt_BR/jeff/medium/pt_BR-jeff-medium', 'pt/pt_BR/edresson/low/pt_BR-edresson-low']
vozes_ok = []
for v in VOZES:
    try:
        onnx = hf_hub_download('rhasspy/piper-voices', v + '.onnx')
        hf_hub_download('rhasspy/piper-voices', v + '.onnx.json')   # o Piper acha o json ao lado do .onnx
        vozes_ok.append(onnx)
    except Exception as e:
        print(f'  AVISO: voz {v} indisponível ({type(e).__name__}: {str(e)[:80]})')
assert len(vozes_ok) >= 2, f'só {len(vozes_ok)} voz(es) pt-BR: preciso de pelo menos 2'
N_PTBR_TRAIN = round(0.3 * PARAMS['n_samples'])
N_PTBR_TEST = round(0.3 * PARAMS['n_samples_val'])
spec = {'seed': 20261024, 'vozes': vozes_ok, 'textos': PARAMS['ptbr_texts'],
        'jobs': [{'dir': cfg['positive_clips_train_dir'], 'n': N_PTBR_TRAIN, 'prefix': 'ptbr_'},
                 {'dir': cfg['positive_clips_test_dir'], 'n': N_PTBR_TEST, 'prefix': 'ptbr_'}]}
# 06/11: as frases parecidas ("quarta-feira", "festa") só existiam com voz inglesa; aqui ganham voz nativa
N_PTBR_NEG = 0
_neg_txt = PARAMS.get('ptbr_neg_texts')
if _neg_txt:
    import re
    assert not any(re.search(r'(sexta|sesta)[- ]?feira', t, re.I) for t in _neg_txt), 'a própria palavra nos negativos'
    _f = PARAMS.get('ptbr_neg_frac', 0.3)
    _nt, _nv = round(_f * PARAMS['n_samples']), round(_f * PARAMS['n_samples_val'])
    spec['jobs'] += [{'dir': cfg['negative_clips_train_dir'], 'n': _nt, 'prefix': 'ptbrneg_', 'textos': _neg_txt},
                     {'dir': cfg['negative_clips_test_dir'], 'n': _nv, 'prefix': 'ptbrneg_', 'textos': _neg_txt}]
    N_PTBR_NEG = _nt + _nv
for j in spec['jobs']:
    os.makedirs(j['dir'], exist_ok=True)
json.dump(spec, open('/content/gen_ptbr_spec.json', 'w'))
open('/content/gen_ptbr.py', 'w').write('''import json, os, random, sys, wave
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from piper import PiperVoice, SynthesisConfig
spec = json.load(open(sys.argv[1]))
rnd = random.Random(spec['seed'])
vozes = [PiperVoice.load(p) for p in spec['vozes']]
contagem = [0] * len(vozes)
for job in spec['jobs']:
    for i in range(job['n']):
        k = i % len(vozes)
        cfg = SynthesisConfig(length_scale=rnd.uniform(0.8, 1.3), noise_scale=rnd.uniform(0.5, 0.9),
                              noise_w_scale=rnd.uniform(0.6, 1.0))
        destino = os.path.join(job['dir'], f"{job['prefix']}{k}_{i:05d}.wav")
        with wave.open(destino, 'wb') as wf:
            vozes[k].synthesize_wav(rnd.choice(job.get('textos', spec['textos'])), wf, syn_config=cfg)
        dados, sr = sf.read(destino)
        if sr != 16000:
            sf.write(destino, resample_poly(dados.astype('float32'), 16000, sr), 16000)
        contagem[k] += 1
print(json.dumps(contagem))
''')
r = subprocess.run(['/opt/py312/bin/python', '/content/gen_ptbr.py', '/content/gen_ptbr_spec.json'],
                   capture_output=True, text=True, cwd='/tmp')
print(r.stdout[-1500:], r.stderr[-2500:])
assert r.returncode == 0, 'geração pt-BR falhou'
N_PTBR = N_PTBR_TRAIN + N_PTBR_TEST
print(f'  pt-BR: {N_PTBR_TRAIN} treino + {N_PTBR_TEST} teste, {N_PTBR_NEG} negativos; clipes por voz: {r.stdout.strip().splitlines()[-1]}')"""

C11D = """# 11d negativos pt-BR: fala real em português como negativa (MLS, CC-BY 4.0), v2 de 27/10/2026
# A Sexta-feira disparava ~30x/dia em sombra com conversa e TV em português: os negativos eram quase só inglês.
# Recortes de `dur_s` s de leitores do Multilingual LibriSpeech (audiolivros, ~40 falantes), uma janela por frase,
# sem nenhuma frase que contenha "sext" (não pode virar negativo a própria palavra). 10% vão para a validação.
import io, json, os, random, re, tarfile, urllib.request
import numpy as np
import soundfile as sf
N_PT_NEG = 0
_cn = PARAMS.get('ptbr_negatives')
if _cn:
    _base = 'https://huggingface.co/datasets/facebook/multilingual_librispeech'
    _api = 'https://huggingface.co/api/datasets/facebook/multilingual_librispeech/tree/main'
    _arqs = json.load(urllib.request.urlopen(f'{_api}/data/mls_portuguese/train/audio', timeout=60))
    _por = {}  # falante -> pacotes (do menor para o maior); muitos falantes têm só um pacote pequeno
    for _x in _arqs:
        _n = _x['path'].rsplit('/', 1)[1]
        _por.setdefault(_n.split('_')[0], []).append((_x.get('size', 0), _n))
    _trans = urllib.request.urlopen(f'{_base}/resolve/main/data/mls_portuguese/train/transcripts.txt',
                                    timeout=120).read().decode('utf-8')
    _proib = {l.split('\t')[0] for l in _trans.splitlines() if re.search(r'sext', l, re.I)}
    _rnd = random.Random(20261027)
    _dur = int(_cn.get('dur_s', 2.0) * 16000)
    _dirs = (cfg['negative_clips_train_dir'], cfg['negative_clips_test_dir'])
    for _d in _dirs:
        os.makedirs(_d, exist_ok=True)
    _por_falante = {}
    # falantes com menos áudio primeiro: a cota de cada um é o que falta dividido pelos que faltam, e o que um falante
    # pequeno não consegue dar passa para os grandes
    _ordem = sorted(_por, key=lambda s: sum(t for t, _ in _por[s]))
    for _j, _s in enumerate(_ordem):
        _quota = -(-(_cn['n_clips'] - N_PT_NEG) // (len(_ordem) - _j))
        _k = 0
        for _, _n in sorted(_por[_s]):
            if _k >= _quota:
                break
            with urllib.request.urlopen(f'{_base}/resolve/main/data/mls_portuguese/train/audio/{_n}', timeout=600) as _r:
                with tarfile.open(fileobj=_r, mode='r|gz') as _t:
                    for _m in _t:
                        if _k >= _quota:
                            break
                        _id = os.path.basename(_m.name).rsplit('.', 1)[0]
                        if not _m.isfile() or not _m.name.endswith('.flac') or _id in _proib:
                            continue
                        _a, _sr = sf.read(io.BytesIO(_t.extractfile(_m).read()), dtype='int16')
                        if _sr != 16000 or len(_a) < _dur:
                            continue
                        _i = _rnd.randrange(0, len(_a) - _dur + 1)
                        _w = _a[_i:_i + _dur]
                        if np.sqrt(np.mean(_w.astype(np.float32) ** 2)) < 100:  # trecho mudo não ensina nada
                            continue
                        _dst = _dirs[1] if _rnd.random() < 0.1 else _dirs[0]
                        sf.write(os.path.join(_dst, f'ptneg_{_id}.wav'), _w, 16000)
                        _k += 1
        _por_falante[_s] = _k
        N_PT_NEG += _k
    print(f'  negativos pt-BR: {N_PT_NEG} recortes de {len(_por_falante)} falantes '
          f'(meta {_cn["n_clips"]}; {len(_proib)} frases com "sext" excluídas)')
    assert N_PT_NEG >= 0.5 * _cn['n_clips'], 'poucos negativos pt-BR: algo falhou no download do MLS'"""

COPIAS = """def copias(nome, padrao, tags):
    # 26/10: peso por etiqueta no nome do recorte (ex.: {"_music_": 30}); sem etiqueta, o padrão
    for tag, n in tags.items():
        if tag in nome:
            return n
    return padrao"""

C11C = """# 11c reais: gravações reais do dono (recortes curtos) como positivos e negativos, com peso por repetição (v2)
import glob, os, shutil
import yaml
with open('/content/my_model.yaml') as f:
    cfg = yaml.safe_load(f)
_pos = sorted(glob.glob(f'{REAL_DIR}/**/positive/*.wav', recursive=True))
_neg = sorted(glob.glob(f'{REAL_DIR}/**/negative/*.wav', recursive=True))
""" + COPIAS + """
_tags = PARAMS.get('real_copies_by_tag', {})
_n_copias = {'positive': 0, 'negative': 0}
for _lado, _lista, _padrao, _dest in (('positive', _pos, PARAMS['real_pos_copies'], cfg['positive_clips_train_dir']),
                                      ('negative', _neg, PARAMS['real_neg_copies'], cfg['negative_clips_train_dir'])):
    for p in _lista:
        for c in range(copias(os.path.basename(p), _padrao, _tags.get(_lado, {}))):
            shutil.copy(p, os.path.join(_dest, f'real_{c}_{os.path.basename(p)}'))
            _n_copias[_lado] += 1
N_REAL_POS, N_REAL_NEG = len(_pos), len(_neg)
print(f'  reais únicos: {N_REAL_POS} positivos (x{PARAMS["real_pos_copies"]}), {N_REAL_NEG} negativos (x{PARAMS["real_neg_copies"]})')
print(f'  pesos por etiqueta: {_tags}; cópias: {_n_copias}')
assert N_REAL_POS > 0, 'nenhum positivo real encontrado em REAL_DIR'

# Sanidade (smoke 1, 24/10: o augment dos clipes de teste quebrou com "No audio frames were decoded"):
# remove dos 4 diretórios de clipes qualquer wav ilegível, vazio, curto demais (< 0,25 s) ou mudo.
import soundfile as sf
import numpy as np
_ruins = []
for _d in (cfg['positive_clips_train_dir'], cfg['positive_clips_test_dir'],
           cfg['negative_clips_train_dir'], cfg['negative_clips_test_dir']):
    for _f in sorted(glob.glob(f'{_d}/*.wav')):
        try:
            _x, _sr = sf.read(_f)
            _ok = len(_x) / _sr >= 0.25 and float(np.max(np.abs(_x))) > 1e-3
        except Exception:
            _ok = False
        if not _ok:
            _ruins.append(_f); os.remove(_f)
print(f'  clipes ruins removidos: {len(_ruins)}', _ruins[:10])"""

METRICS = """
# v2: métricas para o portão (lidas por gate.py) — /kaggle/working/metrics.json
import json
os.makedirs('/kaggle/working', exist_ok=True)
_pr = {k: v for k, v in PARAMS.items() if k != 'custom_negative_phrases'}
metrics = {
    'qualified': n_qualified_real, 'fallback_single_best': bool(best_models) and n_qualified_real == 0,
    'n_checkpoints': len(best_models),
    'val_recall': float(qr) if qr is not None else float(history['val_recall'][-1]),
    'val_fp_per_hour': float(qf) if qf is not None else float(history['val_fp_per_hour'][-1]),
    'val_accuracy': float(qa) if qa is not None else float(history['val_accuracy'][-1]),
    'pos_test_recall_at_0_5': float((p_scores >= 0.5).mean()),
    'n_real_pos': globals().get('N_REAL_POS'), 'n_real_neg': globals().get('N_REAL_NEG'),
    'n_ptbr': globals().get('N_PTBR'), 'n_ptbr_neg': globals().get('N_PTBR_NEG'), 'n_pt_neg': globals().get('N_PT_NEG'), 'params': _pr,
}
with open('/kaggle/working/metrics.json', 'w') as _f:
    json.dump(metrics, _f, indent=1)
print('  metrics.json:', metrics)
"""

CKPTS = """
# 07/11: exporta TODOS os checkpoints validados em /kaggle/working/ckpts/. Com qualified = 0 em todos os ciclos, o modelo
# acima é o de menor FP/h no ACAV (áudio inglês genérico), um mergulho de ruído da trajetória; a escolha passa para a
# VM, em áudio real da casa e com a pilha inteira (verificador, paciência, porta de voz).
_dir_ck = '/kaggle/working/ckpts'
os.makedirs(_dir_ck, exist_ok=True)
_idx_ck = []
_m_ck = WakewordModel().to('cpu').eval()
for _i, (_lab, _sd, _sc) in enumerate(ALL_CKPTS):
    _m_ck.load_state_dict(_sd)
    _p = f'{_dir_ck}/ckpt_{_i:03d}.onnx'
    _e = WakewordExportable(_m_ck).eval()
    _x = torch.randn(1, 16, 96)
    _kw = dict(input_names=['onnx::Flatten_0'], output_names=['output'],
               dynamic_axes={'onnx::Flatten_0': {0: 'batch'}, 'output': {0: 'batch'}})
    try:
        torch.onnx.export(_e, _x, _p, opset_version=14, dynamo=False, **_kw)
    except Exception:
        torch.onnx.export(_e, _x, _p, opset_version=18, dynamo=True, external_data=False, **_kw)
    _idx_ck.append({'i': _i, 'arquivo': os.path.basename(_p), 'etapa': _lab, **_sc})
with open(f'{_dir_ck}/ckpts.json', 'w') as _f:
    json.dump(_idx_ck, _f, indent=1)
assert len(_idx_ck) == len(ALL_CKPTS) > 0, 'nenhum checkpoint exportado'
print(f'  checkpoints exportados: {len(_idx_ck)} em {_dir_ck}')
"""

PATCH_G = """# Patch G (Sexta-feira, 05/10 e de novo 25/10): "sesta", "fayra" e "fera" não estão no dicionário inglês (CMU).
# Sem a pronúncia, o gerador de negativas baixa o DeepPhonemizer de um S3 que responde 403 e o pacote `dp`
# nem está instalado (a rodada de 25/10 quebrou assim na célula 11). Damos a pronúncia na mão; a Friday não é afetada.
DATA_PY = '/content/openwakeword/openwakeword/data.py'
ANTIGO = 'input_text_phones = [pronouncing.phones_for_word(i) for i in input_text.split()]'
NOVO = ('input_text_phones = [pronouncing.phones_for_word(i) or '
        '{"sesta": ["S EH1 S T AH0"], "fayra": ["F EY1 R AH0"], "fera": ["F EH1 R AH0"]}.get(i.lower(), []) '
        'for i in input_text.split()]')
with open(DATA_PY) as f:
    _src = f.read()
if NOVO not in _src:
    assert ANTIGO in _src, 'data.py do openWakeWord mudou: a linha do pronouncing não foi encontrada'
    with open(DATA_PY, 'w') as f:
        f.write(_src.replace(ANTIGO, NOVO))
with open(DATA_PY) as f:
    assert NOVO in f.read()
print('  Patch G (pronúncia de sesta/fayra/fera no gerador de negativas)')
print()
print('All 7 patches applied (idempotent).')"""


def build():
    nb = copy.deepcopy(json.load(open(ORIGEM)))
    c = nb["cells"]
    g = lambda i: "".join(c[i]["source"])
    assert g(1).startswith("# 0.") and g(11).startswith("# 10.") and g(12).startswith("# 11.")
    assert g(16).startswith("# 15.") and g(17).startswith("# 16.")

    # cell 10 (índice 11): tudo vem de PARAMS
    s = g(11)
    s = sub(s, "TARGET_PHRASE = ['friday']", "TARGET_PHRASE = PARAMS['target_phrase']")
    s = sub(s, "MODEL_NAME = 'friday'", "MODEL_NAME = PARAMS['model_name']")
    s = sub(s, "    # Friday (04/10/2026): palavras parecidas que NÃO devem acordar, além das geradas sozinhas.\n",
            "    # (v2) palavras parecidas que NÃO devem acordar, além das geradas sozinhas: vêm do params.json.\n")
    s = sub_re(s, r"'custom_negative_phrases': \[.*?\],\n", "'custom_negative_phrases': PARAMS['custom_negative_phrases'],\n")
    s = sub(s, "'n_samples': 2000,", "'n_samples': PARAMS['n_samples'],")
    s = sub(s, "'n_samples_val': 1000,", "'n_samples_val': PARAMS['n_samples_val'],")
    s = sub(s, "'augmentation_rounds': 1,", "'augmentation_rounds': PARAMS['augmentation_rounds'],")
    s = sub(s, "'steps': 20000,", "'steps': PARAMS['steps'],")
    # 02/11: peso máximo dos negativos no params (a comunidade sobe 1500 -> 3000 contra disparos falsos)
    s = sub(s, "'max_negative_weight': 1500,", "'max_negative_weight': PARAMS.get('max_negative_weight', 1500),")
    c11 = code(s)

    # cell 7 (índice 8): fração do ACAV no params (era 10% fixo; o ACAV inteiro tem ~2000 h e é o grosso dos negativos)
    c8 = code(sub(g(8), "n_train, n_val = N // 10, N // 100",
                  "n_train = max(1, round(N * PARAMS.get('acav_train_frac', 0.1)))  # 02/11: no params\n    n_val = N // 100"))

    # cell 15 (índice 16): métricas
    s = g(16)
    s = sub(s, "(Kaggle: sem google.colab.files.download)", "(Kaggle: sem download pelo navegador)")
    s = sub(s, "import torch\nif not best_models:", "import torch\nqualified = []\nn_qualified_real = 0\nqa = qr = qf = None\nif not best_models:")
    s = sub(s, "    print(f'  qualified: {len(qualified)} of {len(best_models)} checkpoints')\n",
            "    print(f'  qualified: {len(qualified)} of {len(best_models)} checkpoints')\n    n_qualified_real = len(qualified)\n")
    s = sub(s, "print(f'  ACAV val FP/hour at 0.5: {history[\"val_fp_per_hour\"][-1]:.2f}')\n",
            "print(f'  ACAV val FP/hour at 0.5: {history[\"val_fp_per_hour\"][-1]:.2f}')\n" + METRICS + CKPTS)
    c15 = code(s)

    # cell 14 (índice 15): guarda todo checkpoint validado (07/11), além dos "best_models" do original
    s = g(15)
    assert s.startswith("# 14.")
    s = sub(s, "best_models = []\n", "best_models = []\nALL_CKPTS = []  # 07/11: todos os validados, exportados na célula 15\n")
    s = sub(s, "    history['val_n_fp'].append(n_fp)\n",
            "    history['val_n_fp'].append(n_fp)\n"
            "    ALL_CKPTS.append((step_label, {k: v.detach().cpu().clone() for k, v in model.state_dict().items()},\n"
            "                      {'val_recall': recall, 'val_accuracy': accuracy, 'val_fp_per_hour': fp_per_hour}))\n")
    c14 = code(s)

    # cell 16 (índice 17): nome da palavra
    s = g(17)
    s = sub(s, "print('PRONTO. O friday.onnx está na aba Output desta versão do notebook.')",
            "print(f'PRONTO. O {MODEL_NAME}.onnx e o metrics.json estão na aba Output desta versão do notebook.')")
    s = sub(s, "for arq in [out_path, out_path + '.data']:", "for arq in [out_path, out_path + '.data']:  # metrics.json já está em /kaggle/working")
    c16 = code(s)

    # cell 3 (índice 4): patches + G
    c4 = code(sub(g(4), "print()\nprint('All 6 patches applied (idempotent).')", PATCH_G))

    # cell 12 (índice 13): a reamostragem decidia pela amostra dos 20 primeiros arquivos da pasta. Com os negativos do
    # MLS e os reais (16 kHz) em maioria, a amostra saiu toda 16 kHz e a pasta foi pulada com os Piper de 22,05 kHz
    # dentro (smoke da Sexta, 27/10: "Clip does not have the correct sample rate!"). Agora confere TODOS os arquivos.
    c12 = code(sub(g(13), "    for f in files[:20]:\n", "    for f in files:  # 27/10: todos, não uma amostra\n"))

    novas = [md(MD0)] + c[1:2] + [code(P_CELL)] + c[2:4] + [c4] + c[5:8] + [c8] + c[9:11] + [c11] + [c[12], code(C11B), code(C11C), code(C11D)] + [c12] + [c[14], c14] + [c15, c16]
    nb["cells"] = novas
    return nb


def main(argv):
    saida = argv[1] if len(argv) > 1 else os.path.join(QUI, "wakeword_v2.ipynb")
    with open(saida, "w") as f:
        json.dump(build(), f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    print("gerado:", saida)


if __name__ == "__main__":
    main(sys.argv)
