"""Provisioning orchestrator: steps, retries, failures, area naming, retry service, legacy satellite ignored."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday import provisioning as pv
from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

SAT = "sat-abc123"
INFO = {"program_password": "pw-from-pi", "sync_pubkey": "ssh-ed25519 AAAA sat-abc123-satsync", "mic": "jabra-speak-410",
        "versions": {"program": "x", "agent": "0.1.0"}, "ip": "192.0.2.60"}
BUNDLE = {"url": "http://vm/v1/bundles/tok", "sha256": "a" * 64, "expires_in": 600, "people": ["Ana"]}


class Conn:
    """A fake FridayConnection: records commands; answers come from `responses` (value, exception or callable)."""

    def __init__(self, kind, responses=None, connected=True):
        self.calls = []
        self.responses = responses or {}
        self.connected = connected
        self.hello = SimpleNamespace(device={"id": SAT if kind == "satellite" else "server", "kind": kind})

    async def send(self, name, timeout=10, **fields):
        self.calls.append((name, fields))
        r = self.responses.get(name, {})
        if isinstance(r, list):  # a sequence of answers, the last one repeats
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, Exception):
            raise r
        return r

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def reg(tmp_path, monkeypatch):
    path = tmp_path / "satellites.json"
    path.write_text(json.dumps({"sat-0a1b2c": {"comodo": "Escritório", "senha": "legacy", "ip": "192.0.2.15",
                                               "player": "media_player.escritorio"}}))
    monkeypatch.setattr(pv, "SATELLITES_JSON", str(path))
    return path


@pytest.fixture
def notes():
    with patch.object(pv.persistent_notification, "async_create") as create, \
            patch.object(pv.persistent_notification, "async_dismiss"), \
            patch.object(pv, "async_log_entry") as log:
        yield SimpleNamespace(create=create, log=log)


def make(hass, sat, server, provisioned=False):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[
        ConfigSubentryData(data={"host": "h", "port": 1, "token": "t", "provisioned": provisioned},
                           subentry_type="satellite", title=f"Satellite {SAT}", unique_id=SAT, subentry_id="s1"),
        ConfigSubentryData(data={"host": "h", "port": 2, "token": "t"}, subentry_type="server", title="Server",
                           unique_id=None, subentry_id="s0")])
    entry.add_to_hass(hass)
    entry.runtime_data = {"s0": server, "s1": sat}
    delays = []

    async def sleep(s):
        delays.append(s)

    orch = pv.Orchestrator(hass, SAT, "s1", sleep=sleep)
    return entry, orch, delays


def good_server():
    return Conn("server", {"register_sync_key": {}, "prepare_bundle": BUNDLE})


def good_sat():
    return Conn("satellite", {"provision_info": INFO, "apply_bundle": {}, "start_onboarding": {}, "say": {}})


def said(conn):
    return [c[1]["keys"] for c in conn.calls if c[0] == "say"]


async def test_steps_run_in_order_and_register_the_satellite(hass, reg, notes):
    sat, server = good_sat(), good_server()
    entry, orch, _ = make(hass, sat, server)
    hass.config_entries  # noqa: B018
    area = ar.async_get(hass).async_create("Sala")
    await orch.run()
    assert sat.names() == ["provision_info", "apply_bundle", "apply_bundle", "start_onboarding"]
    assert server.names() == ["register_sync_key", "prepare_bundle", "prepare_bundle"]
    assert server.calls[0][1] == {"sat_id": SAT, "pubkey": INFO["sync_pubkey"]}
    assert [c[1]["kind"] for c in server.calls[1:]] == ["update", "voices"]
    assert sat.calls[1][1] == {"kind": "update", "url": BUNDLE["url"], "sha256": BUNDLE["sha256"]}
    facts = sat.calls[3][1]["facts"]
    assert facts == {"areas": [{"id": area.id, "name": "Sala"}], "known_voices": ["Ana"], "failed_steps": []}
    data = json.loads(reg.read_text())
    assert data[SAT] == {"comodo": "", "ip": "192.0.2.60", "senha": "pw-from-pi"}
    assert data["sat-0a1b2c"]["senha"] == "legacy" and data["sat-0a1b2c"]["player"] == "media_player.escritorio"
    assert reg.stat().st_mode & 0o777 == 0o600
    assert list(reg.parent.glob("satellites.json.bak.*"))  # the first change of the day keeps a copy
    assert not notes.create.called
    assert set((await orch.steps_state()).values()) == {"done"}


async def test_finished_steps_are_not_repeated(hass, reg, notes):
    sat, server = good_sat(), good_server()
    _, orch, _ = make(hass, sat, server)
    await orch.run()
    n = len(sat.calls)
    await orch.run()
    assert len(sat.calls) == n


async def test_retry_then_success(hass, reg, notes):
    sat = good_sat()
    sat.responses["provision_info"] = [HomeAssistantError("timeout"), HomeAssistantError("timeout"), INFO]
    _, orch, delays = make(hass, sat, good_server())
    await orch.run()
    assert delays == [5, 30]  # waits before attempts 2 and 3
    assert sat.names().count("provision_info") == 3
    assert not notes.create.called
    assert (await orch.steps_state())["info"] == "done"


async def test_final_failure_notifies_and_is_told_to_the_satellite(hass, reg, notes):
    server = good_server()
    server.responses["register_sync_key"] = HomeAssistantError("io")
    sat = good_sat()
    _, orch, delays = make(hass, sat, server)
    await orch.run()
    assert server.names().count("register_sync_key") == pv.ATTEMPTS
    assert (await orch.steps_state())["sync_key"] == "failed"
    assert notes.create.call_args.kwargs["notification_id"] == f"friday_provisioning_{SAT}_sync_key"
    assert "sync_key" in notes.create.call_args.args[1]
    assert any("sync_key" in c.args[2] for c in notes.log.call_args_list)
    # the other steps still ran, and onboarding got the failed step to say out loud
    assert sat.calls[-1][0] == "start_onboarding" and sat.calls[-1][1]["facts"]["failed_steps"] == ["sync_key"]


async def test_no_voices_on_the_vm_is_not_a_failure(hass, reg, notes):
    server = good_server()
    server.responses["prepare_bundle"] = [BUNDLE, HomeAssistantError("source_missing")]
    sat = good_sat()
    _, orch, _ = make(hass, sat, server)
    await orch.run()
    assert (await orch.steps_state())["voices"] == "done"
    assert sat.calls[-1][1]["facts"]["known_voices"] == [] and not notes.create.called


async def test_server_offline_fails_the_steps_that_need_it(hass, reg, notes):
    sat = good_sat()
    server = good_server()
    server.connected = False
    _, orch, _ = make(hass, sat, server)
    await orch.run()
    state = await orch.steps_state()
    assert state["sync_key"] == state["update"] == state["voices"] == "failed"
    assert state["registry"] == "done" and state["onboarding"] == "done"
    assert sat.calls[-1][1]["facts"]["failed_steps"] == ["sync_key", "update", "voices"]


async def test_retry_redoes_only_the_failed_steps(hass, reg, notes):
    server = good_server()
    server.responses["register_sync_key"] = [HomeAssistantError("io")] * 3 + [{}]
    sat = good_sat()
    _, orch, _ = make(hass, sat, server)
    await orch.run()
    assert (await orch.steps_state())["sync_key"] == "failed"
    before_sat = len(sat.calls)
    await orch.run(retry=True)
    assert (await orch.steps_state())["sync_key"] == "done"
    assert server.names().count("register_sync_key") == 4
    assert len(sat.calls) == before_sat  # same process: the info is still known
    # a fresh process (HA restarted) has no info: it asks the satellite again, only for the failed step
    server.responses["register_sync_key"] = [HomeAssistantError("io")] * 3 + [{}]
    await orch._save_step("sync_key", "failed", "x")
    orch2 = pv.Orchestrator(hass, SAT, "s1", sleep=orch._sleep)
    await orch2.run(retry=True)
    assert [c[0] for c in sat.calls[before_sat:] if c[0] != "say"] == ["provision_info"]


async def test_factory_reset_removes_the_satellite_from_home_assistant(hass, reg, notes):
    hello = _hello("satellite", SAT)
    hello["capabilities"] = [{"key": "reset_home", "platform": "button", "name": "Forget home"}]
    hello["state"] = {}
    sat = FakeAgent(hello, answering([], {"press": {"forgot_home": True}}))
    server = FakeAgent(_hello("server", "server"))
    entry = await _setup(hass, [("satellite", sat, {"provisioned": True}), ("server", server, {})], [SAT, None])
    button = "button.friday_sat_abc123_reset_home"
    await wait_for(lambda: er.async_get(hass).async_get(button) is not None, timeout=5)
    other = json.loads(reg.read_text())["sat-0a1b2c"]
    pv.write_registry(str(reg), SAT, {"comodo": "Sala"})
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await wait_for(lambda: all(s.unique_id != SAT for s in entry.subentries.values()), timeout=5)
    await hass.async_block_till_done()
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, SAT), entry.entry_id) is None
    assert er.async_get(hass).async_get(button) is None
    data = json.loads(reg.read_text())
    assert SAT not in data and data["sat-0a1b2c"] == other
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()
    await server.stop()


async def test_settings_area_is_not_offered_as_a_room(hass, reg, notes):
    sat = good_sat()
    entry, orch, _ = make(hass, sat, good_server())
    ar.async_get(hass).async_create("Sala")
    ar.async_get(hass).async_create("Ajustes do Mark")
    await orch.run()
    facts = [c for c in sat.calls if c[0] == "start_onboarding"][0][1]["facts"]
    assert [a["name"] for a in facts["areas"]] == ["Sala"]


async def test_area_selected_sets_area_title_and_registry(hass, reg, notes):
    sat, server = good_sat(), good_server()
    entry, orch, _ = make(hass, sat, server)
    area = ar.async_get(hass).async_create("Sala de estar")
    await orch.run()
    with patch.object(hass.config_entries, "async_schedule_reload"):
        await orch.area_selected(area.id)
    sub = entry.subentries["s1"]
    assert sub.title == "Satellite Sala de estar"
    assert sub.data["area_id"] == area.id and sub.data["provisioned"] is False  # flips when the conversation closes
    data = json.loads(reg.read_text())
    assert data[SAT]["comodo"] == "Sala de estar" and data[SAT]["senha"] == "pw-from-pi"
    assert data["sat-0a1b2c"]["comodo"] == "Escritório"  # others untouched
    device = _device(hass, entry)
    assert device.area_id == area.id


async def test_ready_is_said_once_when_the_conversation_closes_and_marks_provisioned(hass, reg, notes):
    sat = good_sat()
    entry, orch, _ = make(hass, sat, good_server())
    area = ar.async_get(hass).async_create("Sala")
    await orch.run()
    await orch.conversation_closed()  # before the area: nothing happens
    assert said(sat) == [] and entry.subentries["s1"].data["provisioned"] is False
    with patch.object(hass.config_entries, "async_schedule_reload"):
        await orch.area_selected(area.id)
        assert said(sat) == []  # the conversation is still open
        await orch.conversation_closed()
        await orch.conversation_closed()
    assert said(sat) == [["ready"]]
    assert entry.subentries["s1"].data["provisioned"] is True


async def test_ready_waits_when_the_conversation_reopens_for_voice_enrollment(hass, reg, notes):
    sat = good_sat()
    entry, orch, _ = make(hass, sat, good_server())
    area = ar.async_get(hass).async_create("Sala")
    await orch.run()
    with patch.object(hass.config_entries, "async_schedule_reload"):
        await orch.area_selected(area.id)
        sat.state = {"program_state": "conversation"}  # the enrollment reopened it during the settle wait
        await orch.conversation_closed()
        assert said(sat) == [] and entry.subentries["s1"].data["provisioned"] is False
        sat.state = {"program_state": "idle"}  # the enrollment ended
        await orch.conversation_closed()
    assert said(sat) == [["ready"]]
    assert entry.subentries["s1"].data["provisioned"] is True


async def test_ready_is_not_said_when_a_step_failed_but_provisioned_flips(hass, reg, notes):
    server = good_server()
    server.responses["register_sync_key"] = HomeAssistantError("io")
    sat = good_sat()
    entry, orch, _ = make(hass, sat, server)
    area = ar.async_get(hass).async_create("Sala")
    await orch.run()
    with patch.object(hass.config_entries, "async_schedule_reload"):
        await orch.area_selected(area.id)
        await orch.conversation_closed()
    assert "ready" not in [k for keys in said(sat) for k in keys]
    assert entry.subentries["s1"].data["provisioned"] is True


async def test_step_failed_is_said_for_each_final_failure_and_offline_is_ignored(hass, reg, notes):
    server = good_server()
    server.responses["register_sync_key"] = HomeAssistantError("io")
    sat = good_sat()
    _, orch, _ = make(hass, sat, server)
    await orch.run()
    assert said(sat) == [["step_failed"]]
    sat2 = good_sat()
    sat2.connected = False
    _, orch2, _ = make(hass, sat2, good_server())
    await orch2.say(["step_failed"])  # no raise, nothing sent
    assert said(sat2) == []


async def test_unknown_area_changes_nothing(hass, reg, notes):
    entry, orch, _ = make(hass, good_sat(), good_server())
    await orch.area_selected("not_an_area")
    assert entry.subentries["s1"].title == f"Satellite {SAT}" and "area_id" not in entry.subentries["s1"].data


def _device(hass, entry):
    return next(d for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
                if (DOMAIN, SAT) in d.identifiers)


def test_write_registry_only_touches_the_given_entry(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"a": {"x": 1}}))
    pv.write_registry(str(p), "b", {"ip": "1"}, {"comodo": ""})
    pv.write_registry(str(p), "b", {"comodo": "Sala"})
    pv.write_registry(str(p), "b", {"ip": "2"}, {"comodo": ""})
    assert json.loads(p.read_text()) == {"a": {"x": 1}, "b": {"comodo": "Sala", "ip": "2"}}


# ---- wiring with real websockets
def _hello(kind, dev_id):
    h = hello_msg()
    h["device"] = {"id": dev_id, "kind": kind, "name": dev_id}
    return h


async def _setup(hass, agents, extra=None):
    subs = []
    for i, (kind, agent, data) in enumerate(agents):
        await agent.start()
        subs.append(ConfigSubentryData(data={"host": "127.0.0.1", "port": agent.port, "token": "t", **data},
                                       subentry_type=kind, title=kind, unique_id=extra[i] if extra else None,
                                       subentry_id=f"s{i}"))
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=subs)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    for i in range(len(agents)):
        await wait_for(lambda i=i: entry.runtime_data[f"s{i}"].hello is not None)
    await hass.async_block_till_done()
    return entry


def answering(sink, data=None):
    async def on_command(ws, cmd):
        sink.append(cmd["name"])
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True, "data": (data or {}).get(cmd["name"], {})})
    return on_command


async def test_legacy_satellite_never_enters_the_orchestrator(hass, reg, notes):
    got = []
    sat = FakeAgent(_hello("satellite", "sat-0a1b2c"), answering(got))
    entry = await _setup(hass, [("satellite", sat, {})], ["sat-0a1b2c"])  # no `provisioned` key: legacy
    await hass.async_block_till_done()
    assert got == [] and not hass.data.get(DOMAIN, {}).get("orchestrators")
    with pytest.raises(ServiceValidationError):
        dev = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "sat-0a1b2c")})
        await hass.services.async_call(DOMAIN, "retry_provisioning", {"device_id": dev.id}, blocking=True)
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_new_satellite_is_provisioned_on_first_connect_and_area_event_applies(hass, reg, notes):
    sat_got, srv_got = [], []
    area = ar.async_get(hass).async_create("Sala")
    sat = FakeAgent(_hello("satellite", SAT), answering(sat_got, {"provision_info": INFO}))
    server = FakeAgent(_hello("server", "server"), answering(srv_got, {"prepare_bundle": BUNDLE}))
    entry = await _setup(hass, [("satellite", sat, {"provisioned": False}), ("server", server, {})], [SAT, None])
    await wait_for(lambda: "start_onboarding" in sat_got, timeout=8)
    assert sat_got == ["provision_info", "apply_bundle", "apply_bundle", "start_onboarding"]
    assert srv_got == ["register_sync_key", "prepare_bundle", "prepare_bundle"]
    # the satellite reports the area picked in the presentation
    await sat.sockets[-1].send_json({"type": "event", "key": "onboarding", "event_type": "area_selected",
                                     "data": {"area_id": area.id}})
    await wait_for(lambda: entry.subentries["s0"].title == "Satellite Sala", timeout=5)
    assert entry.subentries["s0"].data.get("provisioned") is False  # the presentation is still going
    # the conversation closes: the satellite is told `ready` and the subentry is provisioned
    await wait_for(lambda: sat.sockets and sat.sockets[-1].closed is False, timeout=5)
    await sat.sockets[-1].send_json({"type": "state", "changes": {"program_state": "idle"}, "origin": "voice"})
    await wait_for(lambda: entry.subentries["s0"].data.get("provisioned") is True, timeout=5)
    assert "say" in sat_got
    assert json.loads(reg.read_text())[SAT]["comodo"] == "Sala"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()
    await server.stop()


async def test_retry_service_redoes_failed_steps(hass, reg, notes):
    sat_got, srv_got = [], []
    sat = FakeAgent(_hello("satellite", SAT), answering(sat_got, {"provision_info": INFO}))
    server = FakeAgent(_hello("server", "server"), answering(srv_got, {"prepare_bundle": BUNDLE}))
    entry = await _setup(hass, [("satellite", sat, {"provisioned": False}), ("server", server, {})], [SAT, None])
    await wait_for(lambda: "start_onboarding" in sat_got, timeout=8)
    orch = hass.data[DOMAIN]["orchestrators"][SAT]
    await orch._save_step("voices", "failed", "boom")
    dev = _device(hass, entry)
    n = srv_got.count("prepare_bundle")
    await hass.services.async_call(DOMAIN, "retry_provisioning", {"device_id": dev.id}, blocking=True)
    await wait_for(lambda: srv_got.count("prepare_bundle") == n + 1)
    await hass.async_block_till_done()
    assert (await orch.steps_state())["voices"] == "done"
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()
    await server.stop()
