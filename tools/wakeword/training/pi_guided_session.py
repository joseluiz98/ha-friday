"""Sessão guiada de uma palavra com ruído de fundo (música no satélite ou TV) (26/10/2026). Roda NA PI com o
programa do satélite parado.

Por quê: com música o Jabra picota partes fracas da palavra (o "d" de Friday; teste aec-test de 26/10) e os
"Friday" picotados levam nota ~0. O treino só mistura música por cima de vozes limpas, então ele precisa de
positivos gravados pelo Jabra enquanto toca.

Mesmo compasso do teste_tentativas.py (o harvest recorta por dataset_rules.recortes_tentativas): 1º bipe em 1,5 s,
um a cada 6 s, 20 tentativas, e na metade um tom grave + 4,9 s para mudar de lugar (~3 m). Só biblioteca padrão.
Também serve à Sexta-feira com TV (pendência do ciclo 2): o teste_tentativas.py abre /dev/hidraw0, que desde 25/10
não é mais o Jabra; este não usa HID nem botão.
Saída: ~/gravacoes/tentativas_<palavra>_<etiqueta>_<data>.wav (o harvest pega `tentativas_<palavra>_*.wav`) e um .json.
Antes de gravar, dois tons subindo = "nova sessão, volte para perto do Jabra".

Uso: python3 pi_guided_session.py [--word friday|sexta_feira] [--tag music|tv] [--n 20] [--info '{"title": ...}']"""
import argparse, datetime, json, math, os, struct, subprocess, time, wave

GRAVACOES = "/home/jose/gravacoes"
INICIO, INTERVALO, PAUSA_METADE = 1.5, 6.0, 4.9


def tone(path, freq, ms, amp=0.25, sr=16000):
    n = int(sr * ms / 1000)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(amp * 32767 * min(1.0, i / 160, (n - i) / 160)
                                                     * math.sin(2 * math.pi * freq * i / sr))) for i in range(n)))


def schedule(n):
    """Instante de cada bipe, em s desde o início da gravação (igual ao recortes_tentativas)."""
    return [INICIO + k * INTERVALO + (PAUSA_METADE if k >= n // 2 else 0) for k in range(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--word", default="friday", choices=("friday", "sexta_feira"))
    ap.add_argument("--tag", default="music")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--info", default="{}")
    a = ap.parse_args()
    os.makedirs(GRAVACOES, exist_ok=True)
    curto, grave = "/tmp/msess_curto.wav", "/tmp/msess_grave.wav"
    tone(curto, 1200, 120); tone(grave, 440, 900)
    for f in (660, 990):  # aviso de nova sessão (fora da gravação)
        tone("/tmp/msess_intro.wav", f, 250)
        subprocess.run(["aplay", "-q", "-D", "voice", "/tmp/msess_intro.wav"])
    time.sleep(2.5)
    marcas = schedule(a.n)
    total = math.ceil(marcas[-1] + INTERVALO + 1)
    nome = f"{GRAVACOES}/tentativas_{a.word}_{a.tag}_{datetime.datetime.now():%Y%m%d_%H%M%S}.wav"
    rec = subprocess.Popen(["arecord", "-q", "-D", "jabra_entrada", "-f", "S16_LE", "-r", "16000", "-c", "1",
                            "-d", str(total), nome])
    t0 = time.monotonic()
    for k, t in enumerate(marcas):
        if k == a.n // 2:
            time.sleep(max(0.0, t - PAUSA_METADE - (time.monotonic() - t0)))
            subprocess.Popen(["aplay", "-q", "-D", "voice", grave])
            print("metade: mude de lugar (~3 m do Jabra)", flush=True)
        time.sleep(max(0.0, t - (time.monotonic() - t0)))
        subprocess.Popen(["aplay", "-q", "-D", "voice", curto])
        print(f"tentativa {k + 1}/{a.n}: diga '{'Friday' if a.word == 'friday' else 'Sexta-feira'}'", flush=True)
    rc = rec.wait()
    info = json.loads(a.info)
    info.update({"word": a.word, "tag": a.tag, "ticks": marcas, "total_s": total, "arquivo": os.path.basename(nome)})
    json.dump(info, open(nome[:-4] + ".json", "w"))
    if rc != 0:
        raise SystemExit(f"arecord saiu com {rc}")
    print(nome, flush=True)


if __name__ == "__main__":
    main()
