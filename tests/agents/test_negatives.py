"""Training baseline pack for a satellite's voice verifier (spec 2026-11-23-voice-training-baseline-negatives)."""
import io
import os
import sys
import tarfile
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
from friday_server_agent import negatives as ng  # noqa: E402

NOW = 2_000_000_000.0
A, B, C = "sat-aaaaaa", "sat-bbbbbb", "sat-cccccc"


def _wav(path: Path, seconds: float, age: float = 3600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * (ng.HEADER + int(seconds * ng.BYTES_PER_S)))
    os.utime(path, (NOW - age, NOW - age))
    return path


def _ambient(root, sat, days, seconds=300, age=3600):
    for d in days:
        _wav(root / sat / "gravacoes" / "ambiente" / f"ambiente_202611{d:02d}_080000.wav", seconds, age)


def _names(sel):
    return sorted(n for _, n in sel)


def test_never_sends_the_requesting_satellite_its_own_audio(tmp_path):
    _ambient(tmp_path, A, range(1, 6))
    _ambient(tmp_path, B, range(1, 6))
    sel = ng.select(A, 900, str(tmp_path), NOW)
    assert sel and all(n.startswith(f"{B}__") for n in _names(sel))


def test_splits_the_ambient_seconds_equally_among_the_sources(tmp_path):
    _ambient(tmp_path, A, range(1, 11))
    _ambient(tmp_path, B, range(1, 11))
    sel = ng.select(C, 900, str(tmp_path), NOW)
    per = {s: [n for n in _names(sel) if n.startswith(s)] for s in (A, B)}
    assert len(per[A]) == len(per[B]) == 2          # 450 s each at 300 s a clip


def test_a_satellite_without_ambient_does_not_dilute_the_share(tmp_path):
    _ambient(tmp_path, A, range(1, 11))
    _wav(tmp_path / B / "vozes" / "_negativos" / "x.wav", 3)
    sel = ng.select(C, 900, str(tmp_path), NOW)
    assert len([n for n in _names(sel) if "__ambiente_" in n]) == 3 and f"{B}__falsa_x.wav" in _names(sel)


def test_false_activations_come_from_vozes_negativos_only(tmp_path):
    _wav(tmp_path / A / "vozes" / "_negativos" / "20261014_a.wav", 3)
    _wav(tmp_path / A / "vozes" / "jose" / "atual" / "sessao.wav", 120)       # a person's voice: never
    _wav(tmp_path / A / "vozes" / "jose" / "historico" / "ativacoes" / "v.wav", 3)
    names = _names(ng.select(C, 900, str(tmp_path), NOW))
    assert names == [f"{A}__falsa_20261014_a.wav"]


def test_files_still_arriving_and_odd_names_are_left_out(tmp_path):
    _ambient(tmp_path, A, [1], age=60)                                         # touched 1 min ago: rsync in flight
    _wav(tmp_path / A / "gravacoes" / "ambiente" / "notas.wav", 300)
    _wav(tmp_path / A / "gravacoes" / "ambiente" / "ambiente_20261102_080000.wav.part", 300)
    assert ng.select(C, 900, str(tmp_path), NOW) == []


def test_symlinks_are_not_followed(tmp_path):
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"x" * 100)
    d = tmp_path / A / "gravacoes" / "ambiente"
    d.mkdir(parents=True)
    os.symlink(secret, d / "ambiente_20261101_080000.wav")
    os.utime(secret, (NOW - 3600, NOW - 3600))
    assert ng.select(C, 900, str(tmp_path), NOW) == []


def test_other_directories_in_incoming_are_ignored(tmp_path):
    _ambient(tmp_path, "not-a-satellite", [1])
    _ambient(tmp_path, A, [1])
    assert all(n.startswith(A) for n in _names(ng.select(C, 900, str(tmp_path), NOW)))


def test_seconds_are_capped(tmp_path):
    _ambient(tmp_path, A, range(1, 31))
    assert len(ng.select(C, 10 ** 6, str(tmp_path), NOW)) == 6                # 1800 s / 300 s


def test_build_returns_a_readable_tar_and_the_count(tmp_path):
    _ambient(tmp_path, A, range(1, 4), seconds=10)
    body, n = ng.build(C, 900, str(tmp_path), NOW)
    with tarfile.open(fileobj=io.BytesIO(body)) as t:
        assert n == 3 and sorted(t.getnames()) == sorted(f"{A}__ambiente_202611{d:02d}_080000.wav" for d in (1, 2, 3))


def test_build_leaves_out_what_does_not_fit(tmp_path, monkeypatch):
    _ambient(tmp_path, A, range(1, 4), seconds=10)
    monkeypatch.setattr(ng, "MAX_BYTES", 400_000)
    assert ng.build(C, 900, str(tmp_path), NOW)[1] == 1


async def _client(aiohttp_client, tmp_path, tokens):
    app = web.Application()
    ng.add_routes(app, lambda sat, tok: tokens.get(sat) == tok, str(tmp_path))
    return await aiohttp_client(app)


async def test_route_needs_the_satellite_token(tmp_path, aiohttp_client):
    _ambient(tmp_path, A, [1], seconds=10, age=ng.SETTLE_S + 5)
    c = await _client(aiohttp_client, tmp_path, {C: "tok"})
    q = f"/v1/training/negatives?sat_id={C}&seconds=900"
    assert (await c.get(q)).status == 401
    assert (await c.get(q, headers={"Authorization": "Bearer nope"})).status == 401
    assert (await c.get("/v1/training/negatives?sat_id=nope", headers={"Authorization": "Bearer tok"})).status == 401


async def test_route_answers_204_when_nothing_is_known_and_a_tar_otherwise(tmp_path, aiohttp_client):
    c = await _client(aiohttp_client, tmp_path, {C: "tok"})
    h = {"Authorization": "Bearer tok"}
    q = f"/v1/training/negatives?sat_id={C}&seconds=900"
    assert (await c.get(q, headers=h)).status == 204
    _ambient(tmp_path, A, [1], seconds=10, age=ng.SETTLE_S + 5)
    os.utime(tmp_path / A / "gravacoes" / "ambiente" / "ambiente_20261101_080000.wav")  # now: still settling
    assert (await c.get(q, headers=h)).status == 204
    p = tmp_path / A / "gravacoes" / "ambiente" / "ambiente_20261101_080000.wav"
    os.utime(p, (1, 1))
    r = await c.get(q, headers=h)
    assert r.status == 200 and r.headers["X-Clips"] == "1" and r.headers["Content-Type"] == "application/x-tar"
    assert (await c.get(f"/v1/training/negatives?sat_id={C}&seconds=abc", headers=h)).status == 400
