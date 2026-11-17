"""Entity placement rule (spec 2026-11-17-diagnostic-sensors, docs/protocol.md#entity-placement).

The main section of a device page only holds what a resident uses or wants to see. Hardware, network, versions and
maintenance/infra declare `"category": "diagnostic"`; settings declare `"category": "config"`. So:

  * a key in the allowlist below must have NO category (it is the main section);
  * every other shown (not `hidden`) descriptor MUST declare `diagnostic` or `config`.

A new descriptor without a category fails here. If it really is for residents, add its key to the allowlist of its
agent kind; otherwise give it a category. Entities the integration builds by itself follow the same rule
(tests/integration/test_entity_placement.py).
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "satellite"))
sys.path.insert(0, str(ROOT / "agents" / "server"))

from friday_satellite_agent import capabilities as cap  # noqa: E402
from friday_satellite_agent import eq as eqmod  # noqa: E402
from friday_satellite_agent import hardware as hw  # noqa: E402
from friday_satellite_agent import ota  # noqa: E402
from friday_server_agent import collectors as c  # noqa: E402

CFG = {"volume": 6, "nao_perturbe": False, "volume_alertas": 6, "alarm_ramp_s": 0, "volume_sons": -12,
       "whisper_min_ratio_db": 5.5, "espera_resposta_s": 8, "continuacao_s": 5, "music_duck_percent": 15,
       "music_restore_s": 1.0, "limiar_palavra": 0.5, "modo_friday": "ativa",
       "palavras_extras": [{"nome": "sexta_feira", "modo": "sombra", "limiar": 0.35}]}
WW = [{"nome": "friday", "modo": "ativa", "limiar": 0.5, "principal": True, "treinada": True},
      {"nome": "sexta_feira", "modo": "sombra", "limiar": 0.35, "principal": False, "treinada": True}]

SATELLITE_MAIN = frozenset({"volume", "do_not_disturb", "mute", "activation", "timer", "next_timer",
                            "room_temperature", "room_humidity", "room_pressure"})
SERVER_MAIN = frozenset({"voice", "cost_today_brl", "cost_month_brl", "cost_month_projection_brl",
                         "cost_month_over_budget", "restart_friday"})


def all_descriptors(kind: str, monkeypatch=None) -> list[dict]:
    if kind == "server":
        return c.capabilities(proxmox=True)
    patch = monkeypatch.setattr if monkeypatch is not None else None
    if patch:
        patch(hw, "nmcli_available", lambda: True)
    eq = eqmod.Equalizer()
    eq.available = True
    found = (cap.controls_from_config(CFG, WW) + cap.room_descriptors(cap.ROOM_DESCRIPTORS) + eq.descriptors()
             + ota.OtaClient.descriptors() + hw.descriptors(network=True))
    by_key: dict[str, dict] = {}
    for d in found:
        by_key.setdefault(d["key"], d)
    return list(by_key.values())


@pytest.mark.parametrize("kind,main", [("satellite", SATELLITE_MAIN), ("server", SERVER_MAIN)])
def test_every_descriptor_is_placed(kind, main, monkeypatch):
    shown = [d for d in all_descriptors(kind, monkeypatch) if not d.get("hidden")]
    bad = [d["key"] for d in shown
           if (d["key"] in main) == (d.get("category") in ("config", "diagnostic"))]
    # main keys have no category; every other shown key declares one (see the module docstring)
    assert bad == [], f"see docs/protocol.md#entity-placement: {bad}"
    assert main <= {d["key"] for d in shown}  # the allowlist never goes stale
    if kind == "satellite":  # 17/11: pressure shown exactly like temperature and humidity
        by = {d["key"]: d for d in shown}
        assert by["room_pressure"]["state_class"] == by["room_humidity"]["state_class"] == "measurement"
        assert "category" not in by["room_pressure"] and by["room_pressure"]["device_class"] == "pressure"
