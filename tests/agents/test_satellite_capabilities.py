"""Satellite agent: control mapping, program-down state, local event route."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import capabilities as cap  # noqa: E402
from friday_satellite_agent import local  # noqa: E402

CFG = {"volume": 6, "nao_perturbe": False, "volume_alertas": 6, "alarm_ramp_s": 0, "volume_sons": -12,
       "whisper_min_ratio_db": 5.5, "espera_resposta_s": 8, "music_duck_percent": 15, "music_restore_s": 1.0,
       "limiar_palavra": 0.5, "modo_friday": "ativa",
       "palavras_extras": [{"nome": "sexta_feira", "modo": "sombra", "limiar": 0.35}]}
WW = [{"nome": "friday", "modo": "ativa", "limiar": 0.5, "principal": True, "treinada": True},
      {"nome": "sexta_feira", "modo": "sombra", "limiar": 0.35, "principal": False, "treinada": True}]


def test_volume_percent_position_roundtrip():
    assert cap.position_from_percent(0) == 1
    assert cap.position_from_percent(100) == 11
    assert cap.position_from_percent(50) == 6
    assert cap.percent_from_position(11) == 100
    assert cap.percent_from_position(6) == 55
    assert cap.percent_from_position(0) == 0


def test_controls_cover_existing_settings():
    keys = {c["key"]: c for c in cap.controls_from_config(CFG, WW)}
    for k in ("volume", "do_not_disturb", "alarm_volume", "alarm_ramp", "sounds_volume", "whisper_mode",
              "follow_up", "ducking", "ducking_level", "ducking_restore", "wakeword_friday_mode",
              "wakeword_sexta_feira_sensitivity", "program_state", "activations_today", "rejected_today",
              "last_activation", "sw_version", "activation"):
        assert k in keys, k
    assert keys["volume"].get("category") is None and keys["do_not_disturb"].get("category") is None
    assert keys["follow_up"]["category"] == "config"
    assert keys["activation"]["event_types"] == ["conversation", "rejected", "shadow"]
    assert keys["wakeword_friday_mode"]["options"] == ["active", "shadow", "off"]


def test_wakeword_mode_mapping():
    m = cap.Mapper()
    st = m.state(CFG, WW, None)
    assert st["wakeword_friday_mode"] == "active"
    assert st["wakeword_sexta_feira_mode"] == "shadow"
    assert st["wakeword_sexta_feira_sensitivity"] == 0.35
    assert m.to_program(CFG, "wakeword_sexta_feira_mode", "off") == {"wakeword": ("sexta_feira", "desligada")}
    assert m.to_program(CFG, "wakeword_friday_mode", "active") == {"wakeword": ("friday", "ativa")}
    assert m.to_program(CFG, "wakeword_friday_sensitivity", 0.4) == {"config": {"limiar_palavra": 0.4}}
    r = m.to_program(CFG, "wakeword_sexta_feira_sensitivity", 0.2)["config"]["palavras_extras"]
    assert r == [{"nome": "sexta_feira", "modo": "sombra", "limiar": 0.2}]


def test_whisper_off_is_null():
    m = cap.Mapper()
    assert m.state(dict(CFG, whisper_min_ratio_db=None), WW, None)["whisper_mode"] is False
    assert m.state(CFG, WW, None)["whisper_mode"] is True
    assert m.to_program(CFG, "whisper_mode", False) == {"config": {"whisper_min_ratio_db": None}}
    assert m.to_program(CFG, "whisper_mode", True) == {"config": {"whisper_min_ratio_db": 5.5}}


def test_ducking_switch_restores_last_level():
    m = cap.Mapper()
    m.state(dict(CFG, music_duck_percent=20), WW, None)
    assert m.to_program(CFG, "ducking", False) == {"config": {"music_duck_percent": 0}}
    m.state(dict(CFG, music_duck_percent=0), WW, None)
    assert m.state(dict(CFG, music_duck_percent=0), WW, None)["ducking"] is False
    assert m.to_program(CFG, "ducking", True) == {"config": {"music_duck_percent": 20}}
    assert cap.Mapper().to_program(CFG, "ducking", True) == {"config": {"music_duck_percent": 15}}


def test_volume_set_and_unknown():
    m = cap.Mapper()
    assert m.to_program(CFG, "volume", 100) == {"config": {"volume": 11}}
    assert m.to_program(CFG, "do_not_disturb", True) == {"config": {"nao_perturbe": True}}
    assert m.to_program(CFG, "bogus", 1) is None


def test_program_down_sets_state_off():
    m = cap.Mapper()
    assert m.state(None, [], None)["program_state"] == "off"
    st = m.state(CFG, WW, {"estado": "parada"})
    assert st["program_state"] == "idle"


def test_activation_events_idempotent_conversation_state():
    t = cap.Tracker()
    ev = t.on_event("ativacao", {"palavra": "friday", "nota": 0.8, "resultado": "conversa", "duracao_s": None})
    assert ev["event"] == ("activation", "conversation", {"word": "friday", "score": 0.8, "duration_s": None})
    assert ev["state"]["activations_today"] == 1 and ev["state"]["last_activation"]
    ev = t.on_event("ativacao", {"palavra": "friday", "nota": 0.3, "resultado": "barrada", "duracao_s": None})
    assert ev["state"]["rejected_today"] == 1 and ev["event"][1] == "rejected"
    assert t.on_event("ativacao", {"palavra": "x", "resultado": "sombra"})["event"][1] == "shadow"
    assert t.on_event("conversa", {"estado": "fechada"})["state"]["program_state"] == "idle"  # unpaired close
    assert t.on_event("conversa", {"estado": "aberta"})["state"]["program_state"] == "conversation"
    assert t.on_event("conversa", {"estado": "aberta"})["state"]["program_state"] == "conversation"
    cfg_ev = t.on_event("config", {"mudancas": {"volume": 11, "nao_perturbe": True}, "origem": "voz"})
    assert cfg_ev["origin"] == "voice" and cfg_ev["config"] == {"volume": 11, "nao_perturbe": True}
    assert t.on_event("config", {"mudancas": {}, "origem": "botao"})["origin"] == "button"
    assert t.on_event("config", {"mudancas": {}, "origem": "porta"})["origin"] == "agent"
    t.reset_day()
    assert t.counters() == {"activations_today": 0, "rejected_today": 0}


async def test_local_event_requires_localhost_and_token(aiohttp_client):
    got = []

    async def on_event(tipo, dados):
        got.append((tipo, dados))

    client = await aiohttp_client(local.make_app("sekret", on_event))
    body = {"tipo": "conversa", "dados": {"estado": "aberta"}}
    assert (await client.post("/v1/local/event", json=body)).status == 401
    assert (await client.post("/v1/local/event", json=body, headers={"Authorization": "Bearer no"})).status == 401
    ok = await client.post("/v1/local/event", json=body, headers={"Authorization": "Bearer sekret"})
    assert ok.status == 204 and got == [("conversa", {"estado": "aberta"})]
    bad = await client.post("/v1/local/event", data="x", headers={"Authorization": "Bearer sekret"})
    assert bad.status == 400
    assert local.is_loopback("127.0.0.1") and local.is_loopback("::1")
    assert not local.is_loopback("192.0.2.10") and not local.is_loopback(None)
