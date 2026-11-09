"""Medida espectral do sussurro (spec 2026-10-20 D3).

Razao, em dB, entre a energia de 2-6 kHz e a de 100-600 Hz nos quadros com som:
sussurro nao tem vibracao das cordas vocais, logo quase nao tem graves.
PCM 16 kHz mono S16LE. Depende so de numpy.
"""
import math

import numpy as np

FRAME = 512
HOP = 256
RATE = 16000
LOW_BAND = (100, 600)
HIGH_BAND = (2000, 6000)
MIN_RMS = 80

_BIN_HZ = RATE / FRAME  # 31,25 Hz
_LO = (int(LOW_BAND[0] / _BIN_HZ), int(LOW_BAND[1] / _BIN_HZ))
_HI = (int(HIGH_BAND[0] / _BIN_HZ), int(HIGH_BAND[1] / _BIN_HZ))
_HANN = np.hanning(FRAME)


def _frames(pcm):
    x = np.frombuffer(pcm, dtype="<i2", count=len(pcm) // 2).astype(np.float64)
    if len(x) < FRAME:
        return np.empty((0, FRAME))
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    return x[idx]


def _rms(frames):
    return np.sqrt((frames ** 2).mean(axis=1))


def noise_floor_rms(pcm):
    """RMS do 10o percentil dos quadros; 0.0 com menos de 1 quadro."""
    fr = _frames(pcm)
    if len(fr) == 0:
        return 0.0
    r = np.sort(_rms(fr))
    return float(r[len(r) // 10])


def whisper_ratio_db(pcm, floor_rms=None, min_frames=4):
    """Razao 2-6 kHz / 100-600 Hz em dB, ou None se faltar som."""
    fr = _frames(pcm)
    if len(fr) == 0:
        return None
    r = _rms(fr)
    piso = floor_rms if floor_rms is not None else float(np.sort(r)[len(r) // 10])
    usados = fr[r > max(4 * piso, MIN_RMS)]
    if len(usados) < min_frames:
        return None
    p = np.abs(np.fft.rfft(usados * _HANN, axis=1)) ** 2
    e_lo = float(p[:, _LO[0]:_LO[1]].sum())
    e_hi = float(p[:, _HI[0]:_HI[1]].sum())
    return 10.0 * math.log10((e_hi + 1) / (e_lo + 1))
