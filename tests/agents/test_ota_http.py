"""OTA HTTP API for satellites and WS commands for HA (plan 2026-11-10 Task 8)."""
import sys
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
from friday_server_agent.ota import http as oh  # noqa: E402

from tests.agents.test_ota_orchestrator import SAT, World, rel  # noqa: E402


async def _client(aiohttp_client, w, status_token="st"):
    app = web.Application()
    oh.add_routes(app, w.orch, lambda: status_token)
    return await aiohttp_client(app)


def _pkg(tmp_path, tag="v0.3.0"):
    p = tmp_path / "ota" / "packages" / tag
    p.mkdir(parents=True, exist_ok=True)
    (p / "satellite.tar.gz").write_bytes(b"PKG")
    return p


async def test_offer_needs_the_satellite_token(tmp_path, aiohttp_client):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    tok = w.tokens.issue(SAT)
    c = await _client(aiohttp_client, w)
    q = f"/v1/ota/offer?sat_id={SAT}&channel=stable&installed=v0.2.0&protocol=1&lock_sha256=" + "L" * 64
    assert (await c.get(q)).status == 401
    assert (await c.get(q, headers={"Authorization": "Bearer nope"})).status == 401
    r = await c.get(q, headers={"Authorization": f"Bearer {tok}"})
    assert r.status == 200 and (await r.json())["tag"] == "v0.3.0"


async def test_token_of_another_satellite_is_refused(tmp_path, aiohttp_client):
    w = World(tmp_path, [rel("v0.3.0")])
    other = w.tokens.issue("sat-0000b2")
    c = await _client(aiohttp_client, w)
    r = await c.get(f"/v1/ota/offer?sat_id={SAT}&channel=stable", headers={"Authorization": f"Bearer {other}"})
    assert r.status == 401


async def test_package_download(tmp_path, aiohttp_client):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    _pkg(tmp_path)
    tok = w.tokens.issue(SAT)
    c = await _client(aiohttp_client, w)
    h = {"Authorization": f"Bearer {tok}"}
    o = w.offer("stable")
    r = await c.get(o["url"], headers=h)  # the offer's url as given
    assert r.status == 200 and await r.read() == b"PKG"
    assert (await c.get(f"/v1/ota/package/v0.3.0/state.json?sat_id={SAT}", headers=h)).status == 404
    assert (await c.get(f"/v1/ota/package/..%2F..%2Fx/satellite.tar.gz?sat_id={SAT}", headers=h)).status == 404


async def test_report_and_bad_report(tmp_path, aiohttp_client):
    w = World(tmp_path, [rel("v0.3.0")])
    tok = w.tokens.issue(SAT)
    c = await _client(aiohttp_client, w)
    h = {"Authorization": f"Bearer {tok}"}
    ok = await c.post("/v1/ota/report", headers=h, json={"sat_id": SAT, "tag": "v0.3.0", "result": "installed"})
    assert ok.status == 200 and w.store.device(SAT)["installed"] == "v0.3.0"
    bad = await c.post("/v1/ota/report", headers=h, json={"sat_id": SAT, "tag": "v0.3.0", "result": "maybe"})
    assert bad.status == 400 and (await bad.json())["error"] == "result"


async def test_request_install_uses_the_status_token(tmp_path, aiohttp_client):
    w = World(tmp_path, [rel("v0.3.0")])
    c = await _client(aiohttp_client, w, status_token="st")
    assert (await c.post("/v1/ota/request_install", json={"sat_id": SAT})).status == 401
    r = await c.post("/v1/ota/request_install", json={"sat_id": SAT}, headers={"Authorization": "Bearer st"})
    assert r.status == 200 and w.store.device(SAT)["install_requested"] is True


async def test_ws_handlers(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    kicked = []
    h = oh.make_handlers(w.orch, kick=lambda full: kicked.append(full))
    assert (await h["ota_set_auto"]({"enabled": False}))["ok"] and w.store.auto_install is False
    issued = await h["ota_issue_token"]({"sat_id": SAT})
    assert issued["ok"] and w.tokens.check(SAT, issued["data"]["token"]) and issued["data"]["url"].endswith(":8801")
    assert (await h["ota_issue_token"]({"sat_id": "bad"}))["ok"] is False
    assert (await h["ota_install"]({"sat_id": SAT}))["ok"] and w.store.device(SAT)["install_requested"]
    assert (await h["ota_check_now"]({}))["ok"] and kicked == [True]
    st = await h["ota_status"]({})
    assert st["ok"] and st["data"]["auto_install"] is False


def test_agent_main_imports_with_ota_wired():
    import importlib
    m = importlib.import_module("friday_server_agent.__main__")
    assert hasattr(m, "main")



async def test_kick_during_a_poll_is_not_lost():
    import asyncio
    from friday_server_agent.ota import loop as ol
    calls, k = [], ol.Kick()

    def poll(full):
        calls.append(full)
        if len(calls) == 1:
            k.kick(True)  # pressed while the first poll runs
    task = asyncio.ensure_future(ol.run(poll, k, period=3600))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if len(calls) >= 2:
            break
    task.cancel()
    assert calls[:2] == [False, True]


def test_blank_chat_gives_null_notifier():
    from friday_server_agent.ota import notify as nt
    assert isinstance(nt.notifier_for("  "), nt.NullNotifier) and isinstance(nt.notifier_for("123"), nt.Notifier)


async def test_offer_with_install_flag_asks_for_the_install(tmp_path, aiohttp_client):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    tok = w.tokens.issue(SAT)
    w.store.device(SAT)["attempts"] = {"version": "v0.3.0", "failures": 4, "exhausted": True}
    c = await _client(aiohttp_client, w)
    h = {"Authorization": f"Bearer {tok}"}
    r = await c.get(f"/v1/ota/offer?sat_id={SAT}&channel=stable&installed=v0.2.0&install=1", headers=h)
    body = await r.json()
    assert body["tag"] == "v0.3.0" and body["install_requested"] is True
    assert w.store.device(SAT)["attempts"] is None


async def test_install_flag_with_nothing_to_install_does_not_latch(tmp_path, aiohttp_client):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    tok = w.tokens.issue(SAT)
    c = await _client(aiohttp_client, w)
    h = {"Authorization": f"Bearer {tok}"}
    r = await c.get(f"/v1/ota/offer?sat_id={SAT}&channel=stable&installed=v0.3.0&install=1", headers=h)
    assert (await r.json())["tag"] is None
    assert w.store.device(SAT)["install_requested"] is False


async def test_server_update_entity_and_its_install_key(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    e = w.orch.server_entity()
    assert e["latest"] == "v0.3.0" and e["in_progress"] is False and e["title"] == "F.R.I.D.A.Y. server"
    h = oh.make_handlers(w.orch, kick=lambda full: None)
    assert (await h["ota_install"]({"key": "ota_server"}))["ok"]
    assert w.store.data["server_ota"]["install_requested"] is True


async def test_loop_pushes_state_after_each_poll():
    import asyncio
    from friday_server_agent.ota import loop as ol
    pushed = []
    k = ol.Kick()

    async def after():
        pushed.append(1)
        raise asyncio.CancelledError
    try:
        await ol.run(lambda full: None, k, 0.01, after=after)
    except asyncio.CancelledError:
        pass
    assert pushed == [1]


def test_agent_publishes_image_and_integration_target_descriptors():
    from friday_server_agent import collectors as c
    keys = {d["key"]: d["platform"] for d in c.capabilities(False)}
    assert keys["ota_server"] == "update" and keys["image_build"] == "button" and keys["image_latest"] == "sensor"
