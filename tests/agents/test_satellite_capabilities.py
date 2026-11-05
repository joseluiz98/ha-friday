"""Satellite agent: control mapping, program-down state, local event route."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import capabilities as cap  # noqa: E402
from friday_satellite_agent import local  # noqa: E402

CFG = {"volume": 6, "nao_perturbe": False, "volume_alertas": 6, "alarm_ramp_s": 0, "volume_sons": -12,
       "whisper_min_ratio_db": 5.5, "espera_resposta_s": 8, "continuacao_s": 5, "music_duck_percent": 15, "music_restore_s": 1.0,
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
              "reply_wait", "ducking", "ducking_level", "ducking_restore", "wakeword_friday_mode",
              "wakeword_sexta_feira_sensitivity", "program_state", "activations_today", "rejected_today",
              "last_activation", "sw_version", "activation"):
        assert k in keys, k
    assert keys["volume"].get("category") is None and keys["do_not_disturb"].get("category") is None
    assert keys["reply_wait"]["category"] == "config"
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
    assert t.on_event("config", {"mudancas": {"volume": 3}, "origem": "porta"})["origin"] == "agent"
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


def test_porta_echo_after_agent_set_is_ha():
    now = [100.0]
    t = cap.Tracker(clock=lambda: now[0])
    t.mark_ha({"volume": 6})
    echo = {"mudancas": {"volume": 6}, "origem": "porta"}
    assert t.on_event("config", echo)["origin"] == "ha"
    assert t.on_event("config", {"mudancas": {"nao_perturbe": True}, "origem": "porta"})["origin"] == "agent"
    now[0] += 6
    assert t.on_event("config", echo)["origin"] == "agent"  # marker expired


async def test_announce_failure_does_not_raise(monkeypatch):
    from friday_satellite_agent import announce as an

    def boom():
        raise OSError("no network")

    monkeypatch.setattr(an, "lan_ip", boom)
    assert an.announce("sat-1", 8701) is None


def test_daily_counters_survive_agent_restart_and_reset_on_new_day(tmp_path):
    path, day = str(tmp_path / "counters.json"), ["2026-10-30"]
    t = cap.Tracker(path=path, today=lambda: day[0])
    t.on_event("ativacao", {"palavra": "friday", "resultado": "conversa"})
    t.on_event("ativacao", {"palavra": "friday", "resultado": "barrada"})
    t.on_event("ativacao", {"palavra": "friday", "resultado": "barrada"})
    again = cap.Tracker(path=path, today=lambda: day[0])  # agent restart, same day
    assert again.counters() == {"activations_today": 1, "rejected_today": 2}
    day[0] = "2026-10-31"
    assert cap.Tracker(path=path, today=lambda: day[0]).counters() == {"activations_today": 0, "rejected_today": 0}
    again.reset_day()
    day[0] = "2026-10-30"
    assert cap.Tracker(path=path, today=lambda: day[0]).counters() == {"activations_today": 0, "rejected_today": 0}


def test_daily_counters_corrupt_file_is_ignored(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("not json")
    assert cap.Tracker(path=str(p)).counters() == {"activations_today": 0, "rejected_today": 0}


def test_daily_counters_have_no_state_class():
    d = {x["key"]: x for x in cap.controls_from_config({}, [])}
    assert "state_class" not in d["activations_today"] and "state_class" not in d["rejected_today"]


def test_ducking_level_float_from_ha_becomes_int():
    m = cap.Mapper()
    assert m.to_program({}, "ducking_level", 20.0) == {"config": {"music_duck_percent": 20}}
    assert m.to_program({}, "alarm_volume", 7.0) == {"config": {"volume_alertas": 7}}


def test_mute_reflects_the_jabra_button():
    m = cap.Mapper()
    assert m.state({"mudo": False}, [], {"estado": "ocioso", "mudo": True})["mute"] is True
    assert m.state({"mudo": False}, [], {"estado": "ocioso", "mudo": False})["mute"] is False
    assert m.state({"mudo": True}, [], {"estado": "ocioso", "mudo": None})["mute"] is True


def test_timers_state_next_and_empty():
    st = cap.timers_state([{"id": "tb", "nome": None, "duracao_s": 600, "termino": 2000.0},
                           {"id": "ta", "nome": "pizza", "duracao_s": 60, "termino": 1000.0}])
    assert st["next_timer"] == "1970-01-01T00:16:40+00:00"
    assert st["next_timer_attributes"] == {"name": "pizza", "id": "ta", "duration_s": 60, "total_active": 2}
    assert cap.timers_state([]) == {"next_timer": None, "next_timer_attributes": {"total_active": 0}}
    assert cap.timers_state("junk")["next_timer"] is None


def test_timer_finished_becomes_event():
    t = cap.Tracker()
    out = t.on_event("timer", {"id": "ta", "nome": "pizza", "duracao_s": 60})
    assert out == {"event": ("timer", "finished", {"name": "pizza", "id": "ta", "duration_s": 60})}


def test_activation_data_full_mapping():
    d = cap.activation_data({"palavra": "friday", "nota": 0.86, "resultado": "barrada", "duracao_s": None, "modo": "ativa",
        "limiar": 0.35, "limiar_voz": 0.6, "voz": 0.52, "causa": "sem_voz", "nota_verificador": 0.86,
        "limiar_verificador": 0.3, "razao_sussurro_db": -11.6, "sussurro": False, "paciencia": 2,
        "gravacao": "sem_voz_20261103_094322.wav", "extra": 1})
    assert d == {"word": "friday", "score": 0.86, "duration_s": None, "mode": "active", "threshold": 0.35,
                 "voice_threshold": 0.6, "voice": 0.52, "cause": "no_voice", "verifier_score": 0.86,
                 "verifier_threshold": 0.3, "whisper_ratio_db": -11.6, "whisper": False, "patience": 2,
                 "recording": "sem_voz_20261103_094322.wav"}


def test_activation_data_old_program_payload():
    assert cap.activation_data({"palavra": "friday", "nota": 0.8, "resultado": "conversa", "duracao_s": None}) == \
        {"word": "friday", "score": 0.8, "duration_s": None}


def test_activation_data_drops_bad_values():
    d = cap.activation_data({"palavra": "friday", "nota": 0.5, "causa": "x", "modo": "y", "gravacao": "../a.wav"})
    assert "cause" not in d and "mode" not in d and "recording" not in d


def test_rejected_event_carries_cause_and_counts():
    t = cap.Tracker()
    ev = t.on_event("ativacao", {"palavra": "friday", "nota": 0.86, "resultado": "barrada", "causa": "sem_voz"})
    assert ev["event"][1] == "rejected" and ev["event"][2]["cause"] == "no_voice" and ev["state"]["rejected_today"] == 1


def test_close_mic_awaiting_reply_switch():
    keys = {c["key"]: c for c in cap.controls_from_config(CFG, WW)}
    d = keys["close_mic_awaiting_reply"]
    assert d["platform"] == "switch" and d["name"] == "Close mic while awaiting reply"
    m = cap.Mapper()
    assert m.state(CFG, WW, None)["close_mic_awaiting_reply"] is False  # absent = off (program default)
    assert m.state(dict(CFG, close_mic_awaiting_reply=True), WW, None)["close_mic_awaiting_reply"] is True
    assert m.to_program(CFG, "close_mic_awaiting_reply", True) == {"config": {"close_mic_awaiting_reply": True}}


def test_follow_up_entities_names_and_mapping():
    keys = {c["key"]: c for c in cap.controls_from_config(CFG, WW)}
    assert "follow_up" not in keys
    spec = {
        "reply_wait": ("number", "Reply wait", (2, 30, 1)),
        "follow_up_window": ("number", "Follow-up window", (0, 30, 1)),
        "no_follow_up_while_music": ("switch", "No follow-up while music plays", None),
        "no_follow_up_after_home_commands": ("switch", "No follow-up after home commands", None),
        "end_of_speech_pause": ("number", "End-of-speech pause", (0.5, 2.0, 0.1)),
    }
    for k, (plat, name, rng) in spec.items():
        assert keys[k]["platform"] == plat and keys[k]["name"] == name, k
        assert keys[k]["category"] == "config", k
        if rng:
            assert (keys[k]["min"], keys[k]["max"], keys[k]["step"]) == rng, k
    m = cap.Mapper()
    st = m.state(dict(CFG, no_follow_up_while_music=False, no_follow_up_after_home_commands=False,
                      end_of_speech_pause_s=1.2), WW, None)
    assert (st["reply_wait"], st["follow_up_window"]) == (8, 5)
    assert st["no_follow_up_while_music"] is False and st["no_follow_up_after_home_commands"] is False
    assert st["end_of_speech_pause"] == 1.2
    st = m.state(CFG, WW, None)  # absent = defaults
    assert st["no_follow_up_while_music"] is True and st["no_follow_up_after_home_commands"] is True
    assert st["end_of_speech_pause"] == 0.8
    assert m.to_program(CFG, "reply_wait", 9) == {"config": {"espera_resposta_s": 9}}
    assert m.to_program(CFG, "follow_up_window", 7.0) == {"config": {"continuacao_s": 7}}
    assert isinstance(m.to_program(CFG, "follow_up_window", 7.0)["config"]["continuacao_s"], int)
    assert m.to_program(CFG, "no_follow_up_while_music", False) == {"config": {"no_follow_up_while_music": False}}
    assert m.to_program(CFG, "no_follow_up_after_home_commands", True) == {
        "config": {"no_follow_up_after_home_commands": True}}
    assert m.to_program(CFG, "end_of_speech_pause", 1.2) == {"config": {"end_of_speech_pause_s": 1.2}}
    assert m.to_program(CFG, "follow_up", 9) is None
