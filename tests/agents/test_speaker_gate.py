"""Speaker gate backend (spec 2026-11-26-two-beam-listening D7). The extractor is always a fake."""
import base64
import json
import struct
import sys
import wave
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
from friday_server_agent import speaker_gate as sg  # noqa: E402

SAT = "sat-aaaaaa"


def _wav(path: Path, marker: int, seconds: float = 1.5):
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(seconds * 16000)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(struct.pack("<%dh" % n, *([marker] + [0] * (n - 1))))


def _fake(model):
    table = {1: [1.0, 0.0], 2: [0.0, 1.0], 9: [0.0, 5.0]}
    return lambda samples: table[round(samples[0] * 32768)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "MODEL", str(tmp_path / "m.onnx"))
    monkeypatch.setattr(sg, "ENROLL_DIR", str(tmp_path / "enroll"))
    return tmp_path


def _pcm(marker, seconds=1.5):
    n = int(seconds * 16000)
    return struct.pack("<%dh" % n, *([marker] + [0] * (n - 1)))


def test_score_unavailable_without_model(env):
    assert sg.SpeakerGate(_fake).score(SAT, _pcm(1)) == {"available": False}


def test_enroll_skips_jabra_folders_and_writes_mean(env):
    (env / "m.onnx").write_bytes(b"x")
    root = env / "voices"
    _wav(root / SAT / "vozes" / "person-a" / "a.wav", 1)
    _wav(root / SAT / "vozes" / "person-a" / "b.wav", 2)
    _wav(root / SAT / "vozes" / "jabra-speak-410" / "c.wav", 9)
    _wav(root / SAT / "vozes" / "person-b" / "jabra-speak-410" / "d.wav", 9)
    res = sg.SpeakerGate(_fake).enroll(SAT, str(root))
    assert res == {"person-a": 2}
    vec = json.loads((env / "enroll" / SAT / "person-a.json").read_text())
    assert vec["dim"] == 2 and vec["vector"] == pytest.approx([0.5, 0.5])
    assert not (env / "enroll" / SAT / "jabra-speak-410.json").exists()


def test_enroll_skips_program_folders(env):
    (env / "m.onnx").write_bytes(b"x")
    root = env / "voices"
    _wav(root / SAT / "vozes" / "person-a" / "a.wav", 1)
    _wav(root / SAT / "vozes" / "_negativos" / "n.wav", 9)
    _wav(root / SAT / "vozes" / "_cache" / "c.wav", 9)
    assert sg.SpeakerGate(_fake).enroll(SAT, str(root)) == {"person-a": 1}


def test_score_picks_best_person(env):
    (env / "m.onnx").write_bytes(b"x")
    d = env / "enroll" / SAT
    d.mkdir(parents=True)
    (d / "person-a.json").write_text(json.dumps({"dim": 2, "vector": [1.0, 0.0]}))
    (d / "person-b.json").write_text(json.dumps({"dim": 2, "vector": [0.0, 1.0]}))
    gate = sg.SpeakerGate(_fake)
    r = gate.score(SAT, _pcm(1))
    assert r["available"]
    assert r["best"] == "person-a" and r["score"] == pytest.approx(1.0) and r["scores"]["person-b"] == pytest.approx(0.0)
    assert gate.score("sat-bbbbbb", _pcm(1))["best"] is None


async def _client(env, remote_ok=True):
    (env / "m.onnx").write_bytes(b"x")
    app = web.Application()
    sg.add_routes(app, sg.SpeakerGate(_fake))
    return TestClient(TestServer(app))


def _body(sat=SAT, pcm=None):
    return {"sat_id": sat, "pcm16_b64": base64.b64encode(pcm if pcm is not None else _pcm(1)).decode()}


async def test_route_refuses_non_localhost(env, monkeypatch):
    async with await _client(env) as c:
        r = await c.post("/v1/speaker_gate/score", json=_body())
        assert r.status == 200  # the test server connects from 127.0.0.1
    app = web.Application()
    sg.add_routes(app, sg.SpeakerGate(_fake))
    async with TestClient(TestServer(app)) as c:
        from aiohttp.web_request import BaseRequest
        monkeypatch.setattr(BaseRequest, "remote", property(lambda self: "192.0.2.7"))
        r = await c.post("/v1/speaker_gate/score", json=_body())
        assert r.status == 403


async def test_route_rejects_bad_sat_id_and_length(env):
    async with await _client(env) as c:
        assert (await c.post("/v1/speaker_gate/score", json=_body(sat="../x"))).status == 400
        assert (await c.post("/v1/speaker_gate/score", json=_body(pcm=_pcm(1, 0.2)))).status == 400
        assert (await c.post("/v1/speaker_gate/score", json=_body(pcm=_pcm(1, 3.5)))).status == 400
        assert (await c.post("/v1/speaker_gate/score", json={"sat_id": SAT, "pcm16_b64": "!!"})).status == 400
