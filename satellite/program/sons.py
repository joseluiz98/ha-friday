"""Gera os sons do satélite (48 kHz estéreo S16, saída sempre a 48 kHz). 3 variantes de cada, para o dono
escolher ouvindo: a = Echo-like (duas notas), b = mais grave e macio, c = curto e agudo."""
import math, os, struct, wave

R = 48000
VARIANTES = {
    "inicio": {"a": [(659, 0.09), (880, 0.12)], "b": [(440, 0.10), (587, 0.14)], "c": [(1047, 0.06), (1319, 0.08)]},
    "fim": {"a": [(880, 0.09), (659, 0.12)], "b": [(587, 0.10), (440, 0.14)], "c": [(1319, 0.06), (1047, 0.08)]},
    "erro": {"a": [(330, 0.14), (0, 0.08), (330, 0.14)], "b": [(262, 0.18), (0, 0.085), (262, 0.18)],
             "c": [(392, 0.10), (0, 0.06), (311, 0.16)]},
    # spec 2026-10-12 D12: bip de volume (curto, neutro), alarme de timer (uma volta ~1,3 s, tocada em repetição)
    # e aviso de lembrete (três notas subindo). Só a variante "a"; outras entram se o dono pedir.
    "volume": {"a": [(988, 0.07)]},
    "alarme": {"a": [(1175, 0.12), (0, 0.08), (1175, 0.12), (0, 0.08), (1175, 0.12), (0, 0.08), (1175, 0.12),
                     (0, 0.55)]},
    "lembrete": {"a": [(523, 0.12), (659, 0.12), (784, 0.24)]},
}


def _notas(notas, amp=9000):
    amostras = []
    for f, dur in notas:
        n = int(R * dur)
        for i in range(n):
            env = min(1.0, i / (0.01 * R), (n - i) / (0.02 * R))  # ataque 10 ms, soltura 20 ms
            v = int(amp * env * math.sin(2 * math.pi * f * i / R)) if f else 0
            amostras.append(v)
    return b"".join(struct.pack("<hh", v, v) for v in amostras)


def _grava(caminho, pcm):
    with wave.open(caminho, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(R); w.writeframes(pcm)


def gerar(pasta: str) -> dict:
    os.makedirs(pasta, exist_ok=True)
    feitos = {}
    for nome, vs in VARIANTES.items():
        for letra, notas in vs.items():
            p = os.path.join(pasta, f"{nome}_{letra}.wav"); _grava(p, _notas(notas)); feitos[f"{nome}_{letra}"] = p
    p = os.path.join(pasta, "bipe.wav"); _grava(p, _notas([(1200, 0.12)])); feitos["bipe"] = p
    return feitos
