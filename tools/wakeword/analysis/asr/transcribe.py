"""Transcreve WAVs com faster-whisper e grava as palavras com tempos (uma linha JSON por arquivo).

Uso no container: transcribe.py <pasta_wav> <saida.ndjson> [modelo]
Lê o WAV com wave/numpy: o decodificador PyAV do faster-whisper quebrou em 24/10 (metadata_errors).
Sem initial_prompt de propósito: um prompt com "Friday" faria o Whisper "ouvir" a palavra onde ela não foi dita."""
import glob, json, os, sys, wave

import numpy as np
from faster_whisper import WhisperModel


def read_wav(path):
    with wave.open(path) as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        a = np.interp(np.arange(0, len(a), sr / 16000), np.arange(len(a)), a).astype(np.float32)
    return a


CHUNK = 30 * 16000


def _words(model, a, off):
    segs, _ = model.transcribe(a, language="pt", word_timestamps=True, vad_filter=True, beam_size=5)
    return [{"w": w.word, "i": round(w.start + off, 2), "f": round(w.end + off, 2), "p": round(w.probability, 2)}
            for s in segs for w in (s.words or [])]


def main(in_dir, out_path, model_name="small"):
    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         cpu_threads=int(os.environ.get("THREADS", "3")), download_root="/models")
    with open(out_path, "w") as out:
        for p in sorted(glob.glob(os.path.join(in_dir, "*.wav"))):
            a = read_wav(p)
            try:
                words = _words(model, a, 0.0)
            except IndexError:  # bug do alinhamento do faster-whisper 1.2.1 (25/10): refaz em pedaços de 30 s
                words = []
                for ini in range(0, len(a), CHUNK):
                    try:
                        words += _words(model, a[ini:ini + CHUNK], ini / 16000)
                    except IndexError:
                        print("  pedaço pulado:", os.path.basename(p), ini // 16000, "s", flush=True)
            out.write(json.dumps({"arq": os.path.basename(p), "palavras": words}, ensure_ascii=False) + "\n")
            out.flush()
            print(os.path.basename(p), "".join(x["w"] for x in words)[:120], flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
