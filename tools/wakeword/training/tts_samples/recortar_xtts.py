"""Recorta de novo as falas brutas do XTTS (27/10/2026), sem gerar de novo.

Motivo: o 1º recorte terminava no fim da palavra segundo o Whisper + 0,20 s, um corte seco,
e o filtro só aceitava a transcrição exata "sexta-feira" (o dono ouviu "Esta-feira" rejeitada
com a pronúncia certa). Aqui o início e o fim são refinados pela energia da própria fala,
com folga e fade, e as variantes de transcrição viram uma classe separada para escuta.

Uso (na VM, imagem friday-asr): python3 recortar_xtts.py <pasta_da_geracao> <saida> [manifesto_anterior]
"""
import json, os, re, sys
import numpy as np, soundfile as sf
from scipy.signal import resample_poly, butter, sosfilt
from faster_whisper import WhisperModel

SR = 16000
ESTRITA = re.compile(r'^sexta[- ]?feira[.!?…]*$')
# 'sesta' fica de fora (o dono, 27/10: ensinaria "sexta feia/feya"); 'até esta feira' entra (R03)
VARIANTE = re.compile(r'^(até )?(sexta|esta|desta|nesta|testa|sexto)[- ]?feira(\.com(\.br)?)?[.,!?…]*( (ponto|pronto)[.!?…]*)?$')


from collections import namedtuple
Palavra = namedtuple('Palavra', 'word start end')
ANTES = {}


def envelope(a):
    q = 160  # 10 ms
    n = len(a) // q
    return np.sqrt((a[:n * q].reshape(n, q) ** 2).mean(1) + 1e-12)


AGUDOS = butter(4, 3000, 'highpass', fs=SR, output='sos')


def recortar(a, ini_s, fim_s):
    env = envelope(a)
    limiar = max(0.05 * env.max(), 3 * np.percentile(env, 5))
    # o "s" inicial é chiado: pouca energia total, mas acima de 3 kHz ele aparece (V01 perdeu o "s")
    ag = envelope(sosfilt(AGUDOS, a))
    lim_ag = max(0.05 * ag.max(), 3 * np.percentile(ag, 5))
    i = int(ini_s * 100)
    while i > 0 and (env[i - 1] > limiar or ag[i - 1] > lim_ag):
        i -= 1
    f = min(int(fim_s * 100), len(env) - 1)
    teto = f + 30  # estende no máximo 0,30 s além do fim do Whisper
    while f < min(teto, len(env) - 1) and env[f + 1] > limiar:
        f += 1
    i0 = max(0, i * 160 - int(0.10 * SR))
    i1 = min(len(a), (f + 1) * 160 + int(0.15 * SR))
    seg = a[i0:i1].copy()
    fi, fo = int(0.015 * SR), int(0.040 * SR)
    if i0 == 0:
        # o XTTS começa a fala no 1º sample: fade longo comeria o "s"; põe silêncio antes e fade curto
        seg = np.concatenate([np.zeros(int(0.10 * SR), seg.dtype), seg])
        fi = int(0.005 * SR)
        seg[int(0.10 * SR):int(0.10 * SR) + fi] *= 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, fi))
        fi = 0
    if fi:
        seg[:fi] *= 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, fi))
    seg[-fo:] *= 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, fo))
    return seg, i0 / SR, i1 / SR


def main(ger, out, anterior=None):
    global ANTES
    if anterior:
        # só itens com palavras salvas; os demais são retranscritos
        ANTES = {i['bruta']: (i['transcricao'], [Palavra(*p) for p in i['palavras']])
                 for i in json.load(open(anterior))['itens'] if i.get('palavras')}
    man = json.load(open(os.path.join(ger, 'manifesto.json')))
    asr = WhisperModel('small', device='cpu', compute_type='int8')
    for d in ('estrita', 'variante'):
        os.makedirs(os.path.join(out, d), exist_ok=True)
    itens = []
    for g in man['geradas']:
        if not g.get('bruta'):
            continue
        b, sr = sf.read(os.path.join(ger, 'brutas', g['bruta']), dtype='float32')
        a = resample_poly(b, 2, 3).astype(np.float32) if sr == 24000 else b
        if g['bruta'] in ANTES:
            txt, pal = ANTES[g['bruta']]
        else:
            segs, _ = asr.transcribe(a, language='pt', word_timestamps=True, beam_size=5)
            pal = [Palavra(w.word, w.start, w.end) for s in segs for w in (s.words or [])]
            txt = ''.join(w.word for w in pal).strip()
        norm = txt.lower()
        it = {'falante': g['falante'], 'genero': g.get('genero'), 'take': g['take'], 'bruta': g['bruta'], 'transcricao': txt}
        classe = 'estrita' if ESTRITA.match(norm) else 'variante' if VARIANTE.match(norm) else None
        idx = [k for k, w in enumerate(pal) if 'feira' in w.word.lower()]
        if classe is None or not idx:
            it['motivo'] = 'transcrição'
            itens.append(it)
            continue
        seg, i0, i1 = recortar(a, pal[0].start, pal[idx[-1]].end)
        if len(seg) / SR < 0.6:
            it['motivo'] = 'curta'
            itens.append(it)
            continue
        nome = f"xtts__{g['falante']}_{g['take']}.wav"
        sf.write(os.path.join(out, classe, nome), seg, SR, subtype='PCM_16')
        it.update(palavras=[[w.word, round(w.start, 2), round(w.end, 2)] for w in pal], classe=classe, arquivo=f'{classe}/{nome}', ini_s=round(i0, 3), fim_s=round(i1, 3), dur_s=round(len(seg) / SR, 3))
        itens.append(it)
    json.dump({'origem': man.get('run_id'), 'marca_dataset': man.get('marca_dataset'), 'itens': itens},
              open(os.path.join(out, 'manifesto.json'), 'w'), ensure_ascii=False, indent=1)
    from collections import Counter
    print(Counter(i.get('classe') or i['motivo'] for i in itens))


if __name__ == '__main__':
    main(*sys.argv[1:4])
