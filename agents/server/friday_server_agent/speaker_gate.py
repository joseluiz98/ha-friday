"""Speaker-embedding gate, shadow backend (spec 2026-11-26-two-beam-listening, D7).

Enrolls each person from the satellite's own XVF recordings (never the Jabra ones) and scores a 1.5-3 s utterance by
cosine similarity against the enrolled mean embedding. Shadow only: nothing here decides anything yet. The model and
`sherpa_onnx`/`numpy` are loaded lazily, so a missing model just means `available: False`.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import logging
import math
import os
import struct
import sys
import time
import wave
from typing import Callable

from aiohttp import web

LOG = logging.getLogger("friday_server_agent.speaker_gate")
BASE = os.environ.get("FRIDAY_SPEAKER_GATE_DIR", "/srv/friday/state/speaker-gate")
MODEL = os.path.join(BASE, "eres2net_base_16k.onnx")
ENROLL_DIR = os.path.join(BASE, "enroll")
WIN_S = 1.5
HOP_S = 0.5
RATE = 16000
SKIP_FOLDERS = {"jabra-speak-410"}  # enrollment must be the satellite's XVF audio
MIN_S, MAX_S = 0.5, 3.0
_LOG_EVERY_S = 60.0

Extractor = Callable[[list], list]


def _default_factory(model: str) -> Extractor:
    import numpy as np  # noqa: PLC0415  (never at module top: CI has no sherpa-onnx)
    import sherpa_onnx  # noqa: PLC0415

    cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model, num_threads=1)
    ext = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def run(samples: list) -> list:
        st = ext.create_stream()
        st.accept_waveform(RATE, np.asarray(samples, dtype=np.float32))
        st.input_finished()
        return [float(x) for x in ext.compute(st)]

    return run


def _norm(v: list) -> list:
    n = math.sqrt(sum(x * x for x in v)) + 1e-9
    return [x / n for x in v]


def _mean(vs: list) -> list:
    return [sum(c) / len(vs) for c in zip(*vs)]


def _cos(a: list, b: list) -> float:
    return sum(x * y for x, y in zip(_norm(a), _norm(b)))


def _windows(samples: list) -> list:
    w, h = int(WIN_S * RATE), int(HOP_S * RATE)
    if len(samples) <= w:
        return [samples] if samples else []
    return [samples[i:i + w] for i in range(0, len(samples) - w + 1, h)]


def _pcm_to_floats(pcm: bytes) -> list:
    n = len(pcm) // 2
    return [s / 32768.0 for s in struct.unpack("<%dh" % n, pcm[:n * 2])]


def _read_wav(path: str) -> list:
    with wave.open(path, "rb") as w:
        if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("not 16 kHz mono int16")
        return _pcm_to_floats(w.readframes(w.getnframes()))


class SpeakerGate:
    def __init__(self, extractor_factory: Callable[[str], Extractor] | None = None):
        self._factory = extractor_factory or _default_factory
        self._extractor: Extractor | None = None

    @property
    def available(self) -> bool:
        return os.path.isfile(MODEL)

    def _ext(self) -> Extractor:
        if self._extractor is None:
            self._extractor = self._factory(MODEL)
        return self._extractor

    def _embed(self, samples: list) -> list:
        ext = self._ext()
        return _mean([_norm(list(ext(w))) for w in _windows(samples)])

    def enroll(self, sat_id: str, voices_root: str) -> dict[str, int]:
        root = os.path.join(voices_root, sat_id, "vozes")
        out: dict[str, int] = {}
        if not self.available or not os.path.isdir(root):
            return out
        ext = self._ext()
        for person in sorted(os.listdir(root)):
            pdir = os.path.join(root, person)
            if person in SKIP_FOLDERS or not os.path.isdir(pdir):
                continue
            embs: list = []
            for dirpath, dirs, names in os.walk(pdir):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_FOLDERS)
                for n in sorted(names):
                    if not n.lower().endswith(".wav"):
                        continue
                    try:
                        samples = _read_wav(os.path.join(dirpath, n))
                    except (OSError, ValueError, EOFError, wave.Error, struct.error) as err:
                        LOG.warning("speaker gate enroll: skipped a file (%s)", type(err).__name__)
                        continue
                    embs.extend(_norm(list(ext(w))) for w in _windows(samples))
            if not embs:
                continue
            vec = _mean(embs)
            dest = os.path.join(ENROLL_DIR, sat_id)
            os.makedirs(dest, exist_ok=True)
            tmp = os.path.join(dest, person + ".json.tmp")
            with open(tmp, "w") as f:
                json.dump({"dim": len(vec), "vector": vec}, f)
            os.replace(tmp, os.path.join(dest, person + ".json"))
            out[person] = len(embs)
        return out

    def _enrolled(self, sat_id: str) -> dict[str, list]:
        d = os.path.join(ENROLL_DIR, sat_id)
        res: dict[str, list] = {}
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return res
        for n in names:
            if n.endswith(".json"):
                try:
                    with open(os.path.join(d, n)) as f:
                        res[n[:-5]] = json.load(f)["vector"]
                except (OSError, ValueError, KeyError):
                    continue
        return res

    def score(self, sat_id: str, pcm16: bytes) -> dict:
        if not self.available:
            return {"available": False}
        enrolled = self._enrolled(sat_id)
        scores: dict[str, float] = {}
        if enrolled:
            emb = self._embed(_pcm_to_floats(pcm16))
            scores = {p: _cos(emb, v) for p, v in enrolled.items()}
        best = max(scores, key=scores.get) if scores else None
        return {"available": True, "scores": scores, "best": best, "score": scores[best] if best else None}


_last_log = [0.0]


def _log_error(err: Exception) -> None:
    now = time.monotonic()
    if now - _last_log[0] >= _LOG_EVERY_S:
        _last_log[0] = now
        LOG.warning("speaker gate: %s", type(err).__name__)


def add_routes(app: web.Application, gate: SpeakerGate | None = None) -> None:
    from .negatives import SAT_ID  # same pattern as the OTA routes

    gate = gate or SpeakerGate()

    async def score(request: web.Request) -> web.Response:
        if request.remote != "127.0.0.1":
            return web.Response(status=403)
        try:
            body = await request.json()
            sat = body["sat_id"]
            pcm = base64.b64decode(body["pcm16_b64"], validate=True)
        except (ValueError, KeyError, TypeError, binascii.Error):
            return web.json_response({"ok": False, "error": "bad body"}, status=400)
        if not isinstance(sat, str) or not SAT_ID.match(sat):
            return web.json_response({"ok": False, "error": "sat_id"}, status=400)
        if not MIN_S * RATE * 2 <= len(pcm) <= MAX_S * RATE * 2:
            return web.json_response({"ok": False, "error": "length"}, status=400)
        try:
            return web.json_response(await asyncio.to_thread(gate.score, sat, pcm))
        except Exception as err:  # noqa: BLE001
            _log_error(err)
            return web.json_response({"ok": False, "error": "internal"}, status=500)

    app.router.add_post("/v1/speaker_gate/score", score)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="speaker_gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    en = sub.add_parser("enroll")
    en.add_argument("--sat", required=True)
    en.add_argument("--voices-root", default=os.environ.get("FRIDAY_WAKEWORD_INCOMING", "/srv/wakeword-data/incoming"))
    args = ap.parse_args(argv)
    gate = SpeakerGate()
    if not gate.available:
        print("speaker gate: model missing, run tools/speaker_gate/fetch_model.sh", file=sys.stderr)
        return 1
    res = gate.enroll(args.sat, args.voices_root)
    print("enrolled %s: %s" % (args.sat, ", ".join("%s=%d windows" % kv for kv in res.items()) or "nobody"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
