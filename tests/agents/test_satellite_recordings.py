"""Satellite agent: read-only recordings routes."""

import sys
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import recordings  # noqa: E402

H = {"Authorization": "Bearer tok"}
NAMES = ["20261103_100215.wav", "sem_voz_20261103_094322.wav", "sexta_feira_20261102_221000.wav"]


@pytest.fixture
async def client(aiohttp_client, tmp_path):
    folder = tmp_path / "ativacoes"
    folder.mkdir()
    for n in NAMES:
        (folder / n).write_bytes(b"RIFF" + n.encode())
    (folder / "notes.txt").write_text("x")
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"secret")
    (folder / "escape_20261103_000000.wav").symlink_to(outside)
    app = web.Application()
    recordings.add_routes(app, "tok", str(folder))
    c = await aiohttp_client(app)
    c.folder = folder
    return c


async def test_requires_token(client):
    for url in ("/v1/recordings/days", "/v1/recordings?date=20261103", "/v1/recordings/" + NAMES[0]):
        assert (await client.get(url)).status == 401
        assert (await client.get(url, headers={"Authorization": "Bearer no"})).status == 401


async def test_days_newest_first(client):
    r = await client.get("/v1/recordings/days", headers=H)
    assert (await r.json())["days"] == ["20261103", "20261102"]


async def test_list_by_date(client):
    r = await client.get("/v1/recordings?date=20261103", headers=H)
    items = (await r.json())["recordings"]
    names = [i["name"] for i in items if not i["name"].startswith("escape")]
    assert names == ["20261103_100215.wav", "sem_voz_20261103_094322.wav"]
    assert items[0]["size"] == (client.folder / items[0]["name"]).stat().st_size
    assert (await client.get("/v1/recordings?date=2026", headers=H)).status == 400
    assert (await client.get("/v1/recordings", headers=H)).status == 400


async def test_serves_wav(client):
    r = await client.get("/v1/recordings/" + NAMES[0], headers=H)
    assert r.status == 200 and r.headers["Content-Type"] == "audio/wav"
    assert await r.read() == (client.folder / NAMES[0]).read_bytes()


async def test_rejects_bad_names(client):
    for n in ("notes.txt", "..%2F20261103_100215.wav", "%2e%2e%2fsecret.wav", "escape_20261103_000000.wav",
              "nope_20261103_000001.wav"):
        assert (await client.get("/v1/recordings/" + n, headers=H)).status == 404, n
