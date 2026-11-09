"""Teste de cancelamento de eco do satélite (26/10/2026), parte offline. Roda na VM dentro de `friday-aec:1`.

Uso: aec.py <pasta>   (a pasta que o capture.sh gravou na Pi: music/mic.wav, music/marks.json, ref_48k_s16_stereo.raw)
Saída: <pasta>/out/<variante>.wav (16 kHz mono, para a repontuação na Pi) e <pasta>/out/metrics.json.

A referência é o que o Sendspin entregou ao ALSA (plugin `file`), ANTES da fila de 341 ms do dmix: o mesmo ponto em que
um cancelador ao vivo pegaria a referência. O atraso até o microfone é estimado aqui por correlação (GCC-PHAT)."""
import ctypes, json, os, sys, time, wave

import numpy as np
from scipy.signal import resample_poly

SR = 16000
TICK_WINDOW = (-0.2, 5.0)  # em volta de cada bipe: a fala do dono; fica fora da conta do eco (ERLE)
SPEEX_ECHO_SET_SAMPLING_RATE, SPEEX_PREPROCESS_SET_DENOISE, SPEEX_PREPROCESS_SET_ECHO_STATE = 24, 0, 24


def read_wav(path):
    w = wave.open(path)
    assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2, path
    return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def write_wav(path, x):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.asarray(x, dtype=np.int16).tobytes())


def ref_from_raw(raw: bytes):
    """S16_LE 48 kHz estéreo (formato do dmix) -> int16 16 kHz mono."""
    a = np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.int16).reshape(-1, 2).astype(np.float64).mean(axis=1)
    return np.clip(np.round(resample_poly(a, 1, 3)), -32768, 32767).astype(np.int16)


def find_lag(mic, ref, max_lag=None):
    """K tal que mic[n] ~ h * ref[n + K] (GCC-PHAT). K > 0: a referência começou antes do microfone."""
    m, r = mic.astype(np.float64), ref.astype(np.float64)
    n = 1 << int(np.ceil(np.log2(len(m) + len(r))))
    X = np.fft.rfft(r, n) * np.conj(np.fft.rfft(m, n))
    cc = np.fft.irfft(X / (np.abs(X) + 1e-9), n)
    lags = np.concatenate([np.arange(0, len(r)), np.arange(-len(m) + 1, 0)])
    vals = np.concatenate([cc[: len(r)], cc[n - len(m) + 1:]])
    if max_lag is not None:
        ok = np.abs(lags) <= max_lag
        lags, vals = lags[ok], vals[ok]
    return int(lags[np.argmax(vals)])


def align(ref, k, n, advance_s=0.01):
    """Referência no tempo do microfone, adiantada `advance_s` (o filtro precisa ver a música antes do eco)."""
    start = k + int(advance_s * SR)  # out[j] = ref[k + j + avanço]: a amostra que o microfone vai ouvir daqui a pouco
    out = np.zeros(n, dtype=np.int16)
    a, b = max(start, 0), min(start + n, len(ref))
    if b > a:
        out[a - start: b - start] = ref[a:b]
    return out


def echo_mask(n, ticks):
    """True onde só há música (eco); fora de [bipe-0,2 s, bipe+5 s]."""
    m = np.ones(n, dtype=bool)
    for t in ticks:
        m[max(int((t + TICK_WINDOW[0]) * SR), 0): max(int((t + TICK_WINDOW[1]) * SR), 0)] = False
    return m


def erle_db(mic, out, mask):
    p_in = np.mean(mic[mask].astype(np.float64) ** 2)
    p_out = np.mean(out[mask].astype(np.float64) ** 2)
    return round(10 * np.log10(p_in / max(p_out, 1e-9)), 1)


def speex(mic, ref, tail, frame=256, residual=False, lib_name="libspeexdsp.so.1"):
    lib = ctypes.CDLL(lib_name)
    lib.speex_echo_state_init.restype = ctypes.c_void_p
    lib.speex_echo_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.speex_echo_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    lib.speex_echo_cancellation.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 3
    lib.speex_echo_state_destroy.argtypes = [ctypes.c_void_p]
    lib.speex_preprocess_state_init.restype = ctypes.c_void_p
    lib.speex_preprocess_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.speex_preprocess_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    lib.speex_preprocess_run.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.speex_preprocess_state_destroy.argtypes = [ctypes.c_void_p]
    st = lib.speex_echo_state_init(frame, tail)
    rate = ctypes.c_int(SR)
    lib.speex_echo_ctl(st, SPEEX_ECHO_SET_SAMPLING_RATE, ctypes.byref(rate))
    pp = None
    if residual:  # supressor de eco residual do Speex; sem o redutor de ruído para não confundir a medida
        pp = lib.speex_preprocess_state_init(frame, SR)
        lib.speex_preprocess_ctl(pp, SPEEX_PREPROCESS_SET_ECHO_STATE, st)
        off = ctypes.c_int(0)
        lib.speex_preprocess_ctl(pp, SPEEX_PREPROCESS_SET_DENOISE, ctypes.byref(off))
    n = len(mic) // frame * frame
    near, far = np.ascontiguousarray(mic[:n]), np.ascontiguousarray(ref[:n])
    out = np.zeros(n, dtype=np.int16)
    step = frame * 2
    pn, pf, po = near.ctypes.data, far.ctypes.data, out.ctypes.data
    for i in range(0, n * 2, step):
        lib.speex_echo_cancellation(st, pn + i, pf + i, po + i)
        if pp:
            lib.speex_preprocess_run(pp, po + i)
    if pp:
        lib.speex_preprocess_state_destroy(pp)
    lib.speex_echo_state_destroy(st)
    return np.concatenate([out, np.zeros(len(mic) - n, dtype=np.int16)])


def webrtc(mic, ref, delay_ms=0):
    """WebRTC AEC3 (livekit). Sem redutor de ruído e sem ganho automático: o detector de hoje recebe o áudio cru."""
    from livekit import rtc
    apm = rtc.AudioProcessingModule(echo_cancellation=True, high_pass_filter=True)
    fs = SR // 100
    n = len(mic) // fs * fs
    out = np.zeros(len(mic), dtype=np.int16)
    for i in range(0, n, fs):
        f_ref = rtc.AudioFrame(bytearray(ref[i:i + fs].tobytes()), SR, 1, fs)
        f_mic = rtc.AudioFrame(bytearray(mic[i:i + fs].tobytes()), SR, 1, fs)
        apm.set_stream_delay_ms(delay_ms)
        apm.process_reverse_stream(f_ref)
        apm.process_stream(f_mic)
        out[i:i + fs] = np.frombuffer(f_mic.data.tobytes(), dtype=np.int16)
    return out


def drift(mic, ref, k, win_s=15):
    """O lag medido no começo, meio e fim: microfone e alto-falante do mesmo Jabra deveriam ter o mesmo relógio."""
    res = []
    for c in (0.2, 0.5, 0.8):
        a = int(c * len(mic)) - win_s * SR // 2
        seg = mic[max(a, 0): a + win_s * SR]
        r0 = max(a + k - SR, 0)
        res.append(find_lag(seg, ref[r0: r0 + (win_s + 2) * SR]) + r0 - max(a, 0))
    return res


def variants(mic, ref, k):
    """(nome, função). `off300`: a referência sem a compensação da fila do dmix (chega 300 ms adiantada)."""
    al = align(ref, k, len(mic))
    early = align(ref, k, len(mic), advance_s=0.30)
    return [
        ("raw", lambda: mic.copy()),
        ("speex_256ms", lambda: speex(mic, al, 4096)),
        ("speex_256ms_residual", lambda: speex(mic, al, 4096, residual=True)),
        ("speex_512ms_off300", lambda: speex(mic, early, 8192)),
        ("webrtc", lambda: webrtc(mic, al)),
        ("webrtc_off300", lambda: webrtc(mic, early)),
    ]


def main(d):
    mic = read_wav(os.path.join(d, "music", "mic.wav"))
    ticks = json.load(open(os.path.join(d, "music", "marks.json")))["ticks"]
    ref = ref_from_raw(open(os.path.join(d, "ref_48k_s16_stereo.raw"), "rb").read())
    if np.abs(ref).max() < 100:
        sys.exit("referência muda: o Sendspin não tocou pelo plugin file")
    k = find_lag(mic, ref)
    out_dir = os.path.join(d, "out")
    os.makedirs(out_dir, exist_ok=True)
    mask = echo_mask(len(mic), ticks)
    lags = drift(mic, ref, k)
    # 26/10: com o Jabra a música some do microfone (fica ~5 dB acima do silêncio) e não há eco linear para achar:
    # os lags por trecho discordam. Sem alinhamento os números das variantes não querem dizer nada.
    confiavel = max(lags) - min(lags) <= int(0.05 * SR) and abs(lags[1] - k) <= int(0.05 * SR)
    m = {"lag_samples": k, "lag_s": round(k / SR, 3), "drift_lags": lags, "alignment_ok": confiavel,
         "mic_s": round(len(mic) / SR, 1), "ticks": ticks, "variants": {}}
    for name, fn in variants(mic, ref, k):
        t0 = time.process_time()
        y = fn()
        cpu = time.process_time() - t0
        write_wav(os.path.join(out_dir, name + ".wav"), y)
        m["variants"][name] = {"erle_db": erle_db(mic, y, mask), "cpu_s": round(cpu, 2),
                               "realtime_factor_vm": round(cpu / (len(mic) / SR), 3)}
        print(json.dumps({name: m["variants"][name]}), flush=True)
    base = os.path.join(d, "baseline", "mic.wav")
    if os.path.exists(base):
        write_wav(os.path.join(out_dir, "baseline.wav"), read_wav(base))
    json.dump(m, open(os.path.join(out_dir, "metrics.json"), "w"), indent=1)
    print(json.dumps({"lag_s": m["lag_s"], "drift_lags": m["drift_lags"]}))


if __name__ == "__main__":
    main(sys.argv[1])
