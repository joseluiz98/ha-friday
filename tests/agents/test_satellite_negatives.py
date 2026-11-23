"""Satellite side of the voice-training baseline (spec 2026-11-23-voice-training-baseline-negatives)."""
import io
import os
import sys
import tarfile
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "common"))
from friday_satellite_agent import local, negatives as ng  # noqa: E402

GOOD = "sat-aaaaaa__ambiente_20261101_080000.wav"


def _tar(members) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.size = len(data) if kind == tarfile.REGTYPE else 0
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "/etc/passwd"
            t.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return buf.getvalue()


def _dest(tmp_path):
    (tmp_path / "vozes").mkdir()
    return str(tmp_path / "vozes" / "_baseline")


def test_unpack_extracts_the_clips_and_replaces_the_old_folder(tmp_path):
    dest = _dest(tmp_path)
    os.makedirs(dest)
    Path(dest, "old.wav").write_bytes(b"old")
    r = ng.unpack(_tar([(GOOD, b"x" * 10, tarfile.REGTYPE)]), dest)
    assert r == {"ok": True, "clips": 1, "bytes": 10}
    assert os.listdir(dest) == [GOOD] and oct(os.stat(dest).st_mode & 0o777) == "0o700"


def test_unpack_creates_the_voices_folder_of_a_new_satellite(tmp_path):
    dest = str(tmp_path / "vozes" / "_baseline")
    assert ng.unpack(_tar([(GOOD, b"x", tarfile.REGTYPE)]), dest)["clips"] == 1
    assert os.listdir(dest) == [GOOD]


@pytest.mark.parametrize("name", ["../x.wav", "/etc/x.wav", "sat-aaaaaa__../x.wav", "notes.txt", "sat-aaaaaa__a.sh",
                                  "sat-zzzzzz__a.wav", "a/sat-aaaaaa__a.wav"])
def test_unpack_refuses_odd_names_and_leaves_nothing(tmp_path, name):
    dest = _dest(tmp_path)
    with pytest.raises(ng.Refused):
        ng.unpack(_tar([(GOOD, b"x", tarfile.REGTYPE), (name, b"x", tarfile.REGTYPE)]), dest)
    assert os.listdir(tmp_path / "vozes") == []


def test_unpack_refuses_links_and_oversize(tmp_path, monkeypatch):
    dest = _dest(tmp_path)
    with pytest.raises(ng.Refused):
        ng.unpack(_tar([(GOOD, b"", tarfile.SYMTYPE)]), dest)
    monkeypatch.setattr(ng, "MAX_FILE", 5)
    with pytest.raises(ng.Refused):
        ng.unpack(_tar([(GOOD, b"123456", tarfile.REGTYPE)]), dest)
    monkeypatch.setattr(ng, "MAX_FILE", 100)
    monkeypatch.setattr(ng, "MAX_TOTAL", 10)
    with pytest.raises(ng.Refused):
        ng.unpack(_tar([(GOOD, b"x" * 8, tarfile.REGTYPE), (GOOD.replace("0101", "0102"), b"x" * 8, tarfile.REGTYPE)]), dest)
    assert os.listdir(tmp_path / "vozes") == []


class FakeUpdater:
    sat_id = "sat-cccccc"

    def __init__(self, url, token="tok", fp="a" * 64):
        self._s, self._t = {"url": url, "fingerprint": fp}, token
        self.http = type("H", (), {"_ssl": staticmethod(lambda fp: False)})()   # plain HTTP in the test

    def settings(self):
        return self._s

    def token(self):
        return self._t


async def _server(aiohttp_server, status, body=b""):
    app = web.Application()

    async def h(request):
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.query["sat_id"] == "sat-cccccc" and request.query["seconds"] == "900"
        return web.Response(status=status, body=body)

    app.router.add_get("/v1/training/negatives", h)
    return await aiohttp_server(app)


async def test_fetch_downloads_and_unpacks(tmp_path, aiohttp_server):
    srv = await _server(aiohttp_server, 200, _tar([(GOOD, b"x" * 10, tarfile.REGTYPE)]))
    dest = _dest(tmp_path)
    r = await ng.fetch(FakeUpdater(f"http://127.0.0.1:{srv.port}"), 900, dest)
    assert r == {"ok": True, "clips": 1, "bytes": 10} and os.listdir(dest) == [GOOD]


async def test_fetch_with_nothing_known_clears_the_folder(tmp_path, aiohttp_server):
    srv = await _server(aiohttp_server, 204)
    dest = _dest(tmp_path)
    os.makedirs(dest)
    r = await ng.fetch(FakeUpdater(f"http://127.0.0.1:{srv.port}"), 900, dest)
    assert r == {"ok": True, "clips": 0, "bytes": 0} and not os.path.exists(dest)


async def test_fetch_never_raises(tmp_path, aiohttp_server):
    dest = _dest(tmp_path)
    assert (await ng.fetch(FakeUpdater(""), 900, dest))["error"] == "no_credentials"
    assert (await ng.fetch(FakeUpdater("http://x", token=""), 900, dest))["error"] == "no_credentials"
    srv = await _server(aiohttp_server, 500)
    assert (await ng.fetch(FakeUpdater(f"http://127.0.0.1:{srv.port}"), 900, dest))["error"] == "http 500"
    bad = await _server(aiohttp_server, 200, _tar([("evil.sh", b"x", tarfile.REGTYPE)]))
    assert (await ng.fetch(FakeUpdater(f"http://127.0.0.1:{bad.port}"), 900, dest))["error"] == "refused"
    assert (await ng.fetch(FakeUpdater("http://127.0.0.1:1"), 900, dest))["ok"] is False


async def test_local_route_needs_the_password_and_a_body(aiohttp_client):
    calls = []

    async def on_negatives(seconds):
        calls.append(seconds)
        return {"ok": True, "clips": 2}

    app = local.make_app("pw", lambda *a: None, None, on_negatives)
    c = await aiohttp_client(app)
    url = local.PATH_NEGATIVES
    assert (await c.post(url, json={"seconds": 900})).status == 401
    assert (await c.post(url, json={"seconds": 900}, headers={"Authorization": "Bearer no"})).status == 401
    h = {"Authorization": "Bearer pw"}
    assert (await c.post(url, json={}, headers=h)).status == 400
    r = await c.post(url, json={"seconds": 900}, headers=h)
    assert r.status == 200 and await r.json() == {"ok": True, "clips": 2} and calls == [900.0]


async def test_local_route_is_absent_without_a_handler(aiohttp_client):
    c = await aiohttp_client(local.make_app("pw", lambda *a: None))
    assert (await c.post(local.PATH_NEGATIVES, json={"seconds": 1}, headers={"Authorization": "Bearer pw"})).status == 404
