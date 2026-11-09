"""Ensaio offline da PORTA DE VOZ da Friday (03/11/2026), na VM (wakeword-trainer:1, versões da Pi).
Pergunta: com música, o "Friday" real passa no modelo (0,86) mas o VAD fica ~0,5 < 0,6 e é barrado. Que regra de voz
recupera esses "Friday" sem abrir disparos com música/TV?

Duas etapas:
  frames  <saida.ndjson> <pasta_modelos> <wav>... | @lista   (pesada, uma vez)
      Por quadro de 80 ms, igual ao laço da Pi: `pont` = nota do modelo COM o verificador (openWakeWord troca a base
      >= 0,5 pela nota do verificador, como ao vivo), `vad` = VAD do openWakeWord (frame_size=640), e a razão do
      sussurro no 1º quadro >= 0,35 de cada episódio (sobre os ~2 s anteriores, como `whisper_ratio_db(trecho)`).
  sim     <frames.ndjson> <rotulos.json> <saida.json>         (leve)
      Simula o DetectorPalavra (limiar 0,35, rearma < 0,2, sussurro >= 5,5 dB conta como voz) com várias regras de voz.
      rotulos.json: {arquivo: {"pos": [[ini_s, fim_s], ...]} | {"neg": true} | {"clip": true}}
        pos  = janelas onde há um "Friday" real (acerto se dispara dentro);
               fora das janelas, um disparo conta como falso (sessão guiada: só música entre os ticks).
        neg  = arquivo inteiro sem palavra (ambiente filtrado): disparos = falsos.
        clip = barradas/sem_voz ao vivo, sem rótulo ainda: lista quais regras disparariam.
"""
import argparse, json, os, sys, wave

import numpy as np

PASSO, SILENCIO, LIMIAR, REARMA, SUSSURRO = 1280, 6, 0.35, 0.2, 5.5


def frames(saida, modelos, wavs, sem_verificador=False):
    from openwakeword.model import Model
    from openwakeword.vad import VAD
    sys.path.insert(0, "/programa")
    from whisper import whisper_ratio_db
    kw = {} if sem_verificador else {"custom_verifier_models": {"friday": os.path.join(modelos, "friday_verificador.pkl")},
                                     "custom_verifier_threshold": 0.5}
    m = Model(wakeword_models=[os.path.join(modelos, "friday.onnx")], inference_framework="onnx", **kw)
    ja = {json.loads(l)["name"] for l in open(saida) if l.strip()} if os.path.exists(saida) else set()
    with open(saida, "a") as out:
        for i, caminho in enumerate(wavs):
            nome = os.path.basename(caminho)
            if nome in ja:
                continue
            with wave.open(caminho) as w:
                assert w.getframerate() == 16000 and w.getnchannels() == 1, caminho
                a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            m.reset()
            vad = VAD()
            for _ in range(SILENCIO):
                z = np.zeros(PASSO, dtype=np.int16)
                m.predict(z); vad.predict(z, frame_size=640)
            pont, vz, razao, recentes, dentro = [], [], {}, [], False
            for k, j in enumerate(range(0, len(a) - PASSO + 1, PASSO)):
                x = a[j:j + PASSO]
                recentes = (recentes + [x.tobytes()])[-25:]
                p = float(m.predict(x)["friday"])
                vz.append(round(float(vad.predict(x, frame_size=640)), 3))
                pont.append(round(p, 3))
                if p < REARMA:
                    dentro = False
                elif p >= LIMIAR and not dentro:
                    dentro = True
                    r = whisper_ratio_db(b"".join(recentes))
                    if r is not None:
                        razao[k] = round(r, 1)
            out.write(json.dumps({"name": nome, "dur": round(len(a) / 16000, 2), "pont": pont, "vad": vz,
                                  "razao": razao}) + "\n")
            out.flush()
            print(f"{i + 1}/{len(wavs)} {nome}", flush=True)


# ---- regras de voz: (nome, função(pont, voz6, voz12, voz_atrasada) -> passa a voz?)
def regras():
    rs = []
    for lv in (0.6, 0.5, 0.45, 0.4, 0.35, 0.3, 0.2, 0.0):
        rs.append((f"voz>={lv}", lambda p, v6, v12, va, lv=lv: v6 >= lv))
    for s in (0.7, 0.8, 0.9):
        for lv2 in (0.45, 0.4, 0.3):
            rs.append((f"voz>=0.6 ou (nota>={s} e voz>={lv2})",
                       lambda p, v6, v12, va, s=s, lv2=lv2: v6 >= 0.6 or (p >= s and v6 >= lv2)))
    for n in (8, 10, 12, 16, 20):
        for lv in (0.6, 0.5, 0.4):
            rs.append((f"voz({n * 0.08:.2f}s)>={lv}", ("jan", n, 0, lv)))
    for at in (3, 6):
        for lv in (0.6, 0.5):
            rs.append((f"voz(6q, {at * 0.08:.2f}s antes)>={lv}", ("jan", 6, at, lv)))
    return rs


def janela(v, k, n, atraso=0):
    fim = k + 1 - atraso
    return float(v[max(0, fim - n):max(1, fim)].max()) if fim > 0 else 0.0


def simular(pont, vad, razao, regra):
    """Instantes (s, fim do quadro) em que o detector dispara."""
    disparos, armado = [], True
    v = np.asarray(vad)
    sussurro_ep = False
    for k, p in enumerate(pont):
        if str(k) in razao or k in razao:
            r = razao.get(str(k), razao.get(k))
            sussurro_ep = r is not None and r >= SUSSURRO
        v6 = float(v[max(0, k - 5):k + 1].max())
        v12 = float(v[max(0, k - 11):k + 1].max())
        va = float(v[max(0, k - 6):max(1, k - 3)].max())
        ok = (janela(v, k, regra[1], regra[2]) >= regra[3]) if isinstance(regra, tuple) else regra(p, v6, v12, va)
        passa = p >= LIMIAR and (ok or sussurro_ep)
        if armado and passa:
            armado = False
            disparos.append(round((k + 1) * PASSO / 16000, 2))
        if p < REARMA:
            armado, sussurro_ep = True, False
    return disparos


def sim(frames_arq, rotulos_arq, saida):
    rot = json.load(open(rotulos_arq))
    dados = [json.loads(l) for l in open(frames_arq) if l.strip()]
    res = {}
    for nome_r, regra in regras():
        r = {"pos_total": 0, "pos_ok": 0, "falsos_sessao": 0, "falsos_neg": 0, "horas_neg": 0.0,
             "horas_sessao_fora": 0.0, "clips_disparam": [], "falsos_neg_onde": []}
        for d in dados:
            rt = rot.get(d["name"])
            if rt is None:
                continue
            ds = simular(d["pont"], d["vad"], d["razao"], regra)
            if "pos" in rt:
                jan = rt["pos"]
                r["pos_total"] += len(jan)
                r["pos_ok"] += sum(any(a <= t <= b for t in ds) for a, b in jan)
                ign = rt.get("ign", [])
                r["falsos_sessao"] += sum(not any(a <= t <= b for a, b in jan + ign) for t in ds)
                r.setdefault("janelas_ok", []).extend([d["name"], a] for a, b in jan if any(a <= t <= b for t in ds))
                r["horas_sessao_fora"] += (d["dur"] - sum(b - a for a, b in jan)) / 3600
            elif rt.get("neg"):
                r["falsos_neg"] += len(ds)
                r["horas_neg"] += d["dur"] / 3600
                r["falsos_neg_onde"] += [[d["name"], t] for t in ds]
            elif rt.get("clip") and ds:
                r["clips_disparam"].append(d["name"])
        res[nome_r] = r
    json.dump(res, open(saida, "w"), indent=1)
    print(f"{'regra':42} {'acertos c/ música':>18} {'falsos sessão':>14} {'falsos amb/h':>13} {'clips':>6}")
    for n, r in res.items():
        fh = r["falsos_neg"] / r["horas_neg"] if r["horas_neg"] else float("nan")
        print(f"{n:42} {r['pos_ok']:>8}/{r['pos_total']:<9} {r['falsos_sessao']:>14} "
              f"{r['falsos_neg']:>5} ({fh:5.2f}) {len(r['clips_disparam']):>6}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("frames"); f.add_argument("saida"); f.add_argument("modelos"); f.add_argument("wavs", nargs="+")
    f.add_argument("--sem-verificador", action="store_true")
    s = sub.add_parser("sim"); s.add_argument("frames"); s.add_argument("rotulos"); s.add_argument("saida")
    a = ap.parse_args()
    if a.cmd == "frames":
        w = a.wavs
        if len(w) == 1 and w[0].startswith("@"):
            w = [l.strip() for l in open(w[0][1:]) if l.strip()]
        frames(a.saida, a.modelos, w, a.sem_verificador)
    else:
        sim(a.frames, a.rotulos, a.saida)
