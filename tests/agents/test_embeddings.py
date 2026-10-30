"""Embeddings: read-only cost estimate; switching is refused (reindex_unsupported)."""

import json
import os

from friday_agent_core import AgentCore
from friday_server_agent import embeddings as e
from friday_server_agent import models as m

HDR = {"Authorization": "Bearer t"}
CATALOG = [{"name": "models/gemini-3.8-pro", "supportedGenerationMethods": ["generateContent"]},
           {"name": "models/gemini-embedding-2", "supportedGenerationMethods": ["embedContent"]},
           {"name": "models/gemini-embedding-3", "supportedGenerationMethods": ["embedContent"]}]


def build(tmp_path, price=0.15, factor=7.4):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ws = tmp_path / "workspace"
    (ws / "memory").mkdir(parents=True)
    (ws / "MEMORY.md").write_text("a" * 400)
    (ws / "memory" / "d.md").write_text("b" * 600)
    (ws / "MEMORY.md.bak.1").write_text("z" * 99999)  # backups are not indexed
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "x.md").write_text("c" * 1000)
    (extra / "x.txt").write_text("q" * 5000)  # pattern *.md only
    (tmp_path / "single.md").write_text("d" * 2000)
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"secret": {"k": "keep-me"}, "memory": {"search": {
        "provider": "gemini", "model": "gemini-embedding-2",
        "extraPaths": [{"path": "/share/extra", "pattern": "*.md"}, "/share/single.md", "/missing/none.md"]}}}))
    os.chmod(oc, 0o640)
    store = m.ModelStore(str(state), str(oc), lambda: CATALOG, lambda: "", lambda: None)
    store.reindexer = e.Reindexer(store, workspace=str(ws), path_map=[("/share", str(tmp_path))],
                                  price_usd_per_m=lambda model: price, factor=lambda: factor)
    core = AgentCore("t", {"id": "server", "kind": "server", "name": "S"}, lambda: [], lambda: {},
                     m.make_handlers(store))
    return core, store, store.reindexer, oc, state


async def call(aiohttp_client, core, name, **args):
    client = await aiohttp_client(core.app())
    ws = await client.ws_connect("/v1/ws", headers=HDR)
    await ws.receive_json()
    await ws.send_json({"type": "command", "id": 1, "name": name, "args": args})
    return await ws.receive_json()


def ok_runner(cmd, timeout):
    return 0, ""


def test_estimate_reindex_brl(tmp_path):
    _, _, re, *_ = build(tmp_path)
    est = re.estimate("gemini-embedding-3")
    # 400 + 600 (workspace) + 1000 (extraPath dir *.md) + 2000 (file); missing path ignored
    assert est["chars"] == 4000 and est["tokens"] == 1000
    assert est["usd"] == round(1000 * 0.15 / 1e6, 6)
    assert est["brl"] == round(1000 * 0.15 / 1e6 * 7.4, 4)
    assert est["price_known"] is True and est["factor_calibrated"] is True


def test_estimate_flags_presumed_price_and_factor(tmp_path):
    _, _, re, *_ = build(tmp_path, price=None, factor=None)
    est = re.estimate("gemini-embedding-3")
    assert est["usd"] > 0 and est["brl"] == round(est["usd"] * 7.4, 4)
    assert est["price_known"] is False and est["factor_calibrated"] is False


async def test_estimate_command(aiohttp_client, tmp_path):
    core, *_ = build(tmp_path)
    r = await call(aiohttp_client, core, "estimate_reindex", model="gemini-embedding-3")
    assert r["data"]["tokens"] == 1000
    assert set(r["data"]) == {"chars", "tokens", "usd", "brl", "price_known", "factor_calibrated"}


async def test_estimate_rejects_bad_model(aiohttp_client, tmp_path):
    core, *_ = build(tmp_path)
    r = await call(aiohttp_client, core, "estimate_reindex", model="../x")
    assert r["ok"] is False and r["error"] == "invalid_value"


async def test_changing_embeddings_is_refused_and_applies_nothing(aiohttp_client, tmp_path):
    core, store, re, oc, state = build(tmp_path)
    before = oc.read_text()
    r = await call(aiohttp_client, core, "set_models", embeddings="gemini-embedding-3", lucid="gemini-3.8-pro")
    assert r["ok"] is False and r["error"] == "reindex_unsupported"
    assert oc.read_text() == before and not (state / "models.json").exists()  # no partial change


async def test_same_embeddings_is_unchanged_and_other_roles_still_apply(aiohttp_client, tmp_path):
    core, store, re, oc, state = build(tmp_path)
    r = await call(aiohttp_client, core, "set_models", embeddings="gemini-embedding-2")
    assert r["data"] == {"embeddings": "unchanged"}
    r = await call(aiohttp_client, core, "set_models", embeddings="gemini-embedding-2", lucid="gemini-3.8-pro")
    assert r["data"]["lucid"] == "applied"
    assert json.loads((state / "models.json").read_text())["lucid"] == "gemini-3.8-pro"
