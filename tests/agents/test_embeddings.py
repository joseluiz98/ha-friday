"""Embeddings switch: cost estimate, background forced reindex, model changed only on success."""

import asyncio
import json
import os
import stat

from friday_agent_core import AgentCore
from friday_server_agent import embeddings as e
from friday_server_agent import models as m

HDR = {"Authorization": "Bearer t"}
CATALOG = [{"name": "models/gemini-embedding-2", "supportedGenerationMethods": ["embedContent"]},
           {"name": "models/gemini-embedding-3", "supportedGenerationMethods": ["embedContent"]}]


def build(tmp_path, runner, price=0.15, factor=7.4):
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
    single = tmp_path / "single.md"
    single.write_text("d" * 2000)
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"secret": {"k": "keep-me"}, "memory": {"search": {
        "provider": "gemini", "model": "gemini-embedding-2",
        "extraPaths": [{"path": "/share/extra", "pattern": "*.md"}, "/share/single.md", "/missing/none.md"]}}}))
    os.chmod(oc, 0o640)
    store = m.ModelStore(str(state), str(oc), lambda: CATALOG, lambda: "", lambda: None)
    pushed = {"state": [], "log": []}

    async def push_state(ch):
        pushed["state"].append(ch)

    async def push_log(msg, data):
        pushed["log"].append((msg, data))

    re = e.Reindexer(store, runner, workspace=str(ws), path_map=[("/share", str(tmp_path))],
                     price_usd_per_m=lambda model: price, factor=lambda: factor,
                     push_state=push_state, push_log=push_log)
    store.reindexer = re
    core = AgentCore("t", {"id": "server", "kind": "server", "name": "S"}, lambda: [], lambda: {},
                     m.make_handlers(store))
    return core, store, re, oc, pushed


async def call(aiohttp_client, core, name, **args):
    client = await aiohttp_client(core.app())
    ws = await client.ws_connect("/v1/ws", headers=HDR)
    await ws.receive_json()
    await ws.send_json({"type": "command", "id": 1, "name": name, "args": args})
    return await ws.receive_json()


def ok_runner(cmd, timeout):
    return 0, ""


def model_of(oc):
    return json.loads(oc.read_text())["memory"]["search"]["model"]


def test_estimate_reindex_brl(tmp_path):
    _, _, re, *_ = build(tmp_path, ok_runner)
    est = re.estimate("gemini-embedding-3")
    # 400 + 600 (workspace) + 1000 (extraPath dir *.md) + 2000 (file); missing path ignored
    assert est["chars"] == 4000 and est["tokens"] == 1000
    assert est["usd"] == round(1000 * 0.15 / 1e6, 6)
    assert est["brl"] == round(1000 * 0.15 / 1e6 * 7.4, 4)


def test_estimate_falls_back_to_default_price_and_factor(tmp_path):
    _, _, re, *_ = build(tmp_path, ok_runner, price=None, factor=None)
    est = re.estimate("gemini-embedding-3")
    assert est["usd"] > 0 and est["brl"] == round(est["usd"] * 7.4, 4)


async def test_estimate_command(aiohttp_client, tmp_path):
    core, *_ = build(tmp_path, ok_runner)
    r = await call(aiohttp_client, core, "estimate_reindex", model="gemini-embedding-3")
    assert r["data"]["tokens"] == 1000 and set(r["data"]) == {"chars", "tokens", "usd", "brl"}


async def test_estimate_rejects_bad_model(aiohttp_client, tmp_path):
    core, *_ = build(tmp_path, ok_runner)
    r = await call(aiohttp_client, core, "estimate_reindex", model="../x")
    assert r["ok"] is False and r["error"] == "invalid_value"


async def test_model_changes_only_after_successful_reindex(aiohttp_client, tmp_path):
    seen = []
    gate = asyncio.Event()

    core_holder = {}

    def runner(cmd, timeout):
        seen.append(cmd)
        # while running, the live config still has the OLD model
        seen.append(model_of(core_holder["oc"]))
        return 0, ""

    core, store, re, oc, pushed = build(tmp_path, runner)
    core_holder["oc"] = oc
    r = await call(aiohttp_client, core, "set_models", embeddings="gemini-embedding-3")
    assert r["data"]["embeddings"] == "started"
    await re.wait()
    assert "--force" in seen[0] and "index" in seen[0] and "memory" in seen[0]
    assert seen[1] == "gemini-embedding-2"
    assert model_of(oc) == "gemini-embedding-3"
    assert json.loads(oc.read_text())["secret"] == {"k": "keep-me"}
    assert stat.S_IMODE(oc.stat().st_mode) == 0o640
    assert [c["embeddings_reindex"] for c in pushed["state"]] == ["running", "idle"]
    assert not any(p.name.startswith("openclaw.json.reindex") for p in tmp_path.iterdir())


async def test_model_kept_when_reindex_fails(aiohttp_client, tmp_path):
    core, store, re, oc, pushed = build(tmp_path, lambda cmd, timeout: (1, "boom SECRETISH"))
    r = await call(aiohttp_client, core, "set_models", embeddings="gemini-embedding-3")
    assert r["data"]["embeddings"] == "started"
    await re.wait()
    assert model_of(oc) == "gemini-embedding-2"
    assert [c["embeddings_reindex"] for c in pushed["state"]] == ["running", "failed"]
    (msg, data), = pushed["log"]
    assert "reindex_failed" in data["code"] and "SECRETISH" not in json.dumps(pushed["log"])
    assert not any(p.name.startswith("openclaw.json.reindex") for p in tmp_path.iterdir())


async def test_runner_exception_and_timeout_count_as_failure(aiohttp_client, tmp_path):
    import subprocess

    def boom(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    core, store, re, oc, pushed = build(tmp_path, boom)
    await call(aiohttp_client, core, "set_models", embeddings="gemini-embedding-3")
    await re.wait()
    assert model_of(oc) == "gemini-embedding-2"
    assert pushed["log"][0][1]["code"] == "reindex_timeout"


async def test_one_reindex_at_a_time(aiohttp_client, tmp_path):
    import threading
    release = threading.Event()

    def slow(cmd, timeout):
        release.wait(5)
        return 0, ""

    core, store, re, oc, pushed = build(tmp_path, slow)
    client = await aiohttp_client(core.app())
    ws = await client.ws_connect("/v1/ws", headers=HDR)
    await ws.receive_json()
    await ws.send_json({"type": "command", "id": 1, "name": "set_models", "args": {"embeddings": "gemini-embedding-3"}})
    assert (await ws.receive_json())["data"]["embeddings"] == "started"  # replied before it finished
    await ws.send_json({"type": "command", "id": 2, "name": "set_models", "args": {"embeddings": "gemini-embedding-3"}})
    r2 = await ws.receive_json()
    assert r2["ok"] is False and r2["error"] == "reindex_running"
    release.set()
    await re.wait()
    assert model_of(oc) == "gemini-embedding-3"


async def test_same_model_is_a_noop_and_unlisted_rejected(aiohttp_client, tmp_path):
    calls = []
    core, store, re, oc, pushed = build(tmp_path, lambda c, t: calls.append(c) or (0, ""))
    r = await call(aiohttp_client, core, "set_models", embeddings="gemini-embedding-2")
    assert r["data"]["embeddings"] == "unchanged" and calls == []
    r = await call(aiohttp_client, core, "set_models", embeddings="gemini-embedding-9")
    assert r["ok"] is False and r["error"] == "invalid_value"
