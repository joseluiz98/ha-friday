"""Fixed mapping between the satellite program's settings and the F.R.I.D.A.Y. protocol.

Pure Python (no I/O). `Mapper` keeps the last non-zero ducking level; `Tracker` turns the
program's local events into protocol state, events and counters (idempotent).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from . import hardware as hw

VERSION = "0.1.0"
WHISPER_DEFAULT_DB = 5.5
DUCK_DEFAULT = 15
MODES = {"ativa": "active", "sombra": "shadow", "desligada": "off"}
MODES_BACK = {v: k for k, v in MODES.items()}
STATES = {"parada": "idle", "alarme": "idle", "cadastro": "idle", "conectando": "listening",
          "ouvindo": "conversation", "aguardando": "conversation", "cortada": "conversation"}
ORIGINS = {"voz": "voice", "botao": "button", "porta": "agent"}
RESULTS = {"conversa": "conversation", "barrada": "rejected", "sombra": "shadow"}
_WW = re.compile(r"^wakeword_(.+)_(mode|sensitivity)$")


def percent_from_position(pos) -> int:
    return round(int(pos) * 100 / 11)


def position_from_percent(pct) -> int:
    return max(1, round(float(pct) * 11 / 100))


def _d(key, platform, name, **kw) -> dict:
    return {"key": key, "platform": platform, "name": name, **kw}


def controls_from_config(cfg: dict, wakewords: list[dict]) -> list[dict]:
    c = "config"
    out = [
        _d("volume", "number", "Volume", min=0, max=100, step=1, unit="%", mode="slider", icon="mdi:volume-high"),
        _d("do_not_disturb", "switch", "Do not disturb", icon="mdi:minus-circle"),
        _d("alarm_volume", "number", "Alarm volume", min=1, max=10, step=1, category=c, mode="slider"),
        _d("alarm_ramp", "number", "Alarm ramp", min=0, max=120, step=1, unit="s", category=c, mode="box"),
        _d("sounds_volume", "number", "Sounds volume", min=-40, max=0, step=1, unit="dB", category=c, mode="slider"),
        _d("whisper_mode", "switch", "Whisper mode", category=c, icon="mdi:account-voice"),
        _d("follow_up", "number", "Follow-up wait", min=2, max=30, step=1, unit="s", category=c, mode="box"),
        _d("ducking", "switch", "Music ducking", category=c, icon="mdi:music-note-minus"),
        _d("ducking_level", "number", "Ducking level", min=1, max=25, step=1, unit="%", category=c, mode="box"),
        _d("ducking_restore", "number", "Ducking restore delay", min=0, max=10, step=0.5, unit="s",
           category=c, mode="box"),
        _d("voice_enabled", "switch", "Voice", icon="mdi:account-voice"),
        _d("mute", "switch", "Mute", icon="mdi:microphone-off"),
        _d("turn_sounds", "switch", "Turn sounds", category=c, icon="mdi:bell-ring"),
        _d("reboot", "button", "Reboot", device_class="restart", category=c),
        _d("restart_program", "button", "Restart program", hidden=True, available=False, category=c),
    ]
    for w in wakewords:
        n = w["nome"]
        out.append(_d(f"wakeword_{n}_mode", "select", f"Wake word {n} mode", category=c,
                      options=["active", "shadow", "off"], icon="mdi:microphone-message"))
        out.append(_d(f"wakeword_{n}_sensitivity", "number", f"Wake word {n} threshold", category=c,
                      min=0.05, max=0.99, step=0.01, mode="box"))
    out += [
        _d("program_state", "sensor", "Program state", device_class="enum",
           options=["off", "idle", "listening", "conversation"]),
        _d("activations_today", "sensor", "Activations today", state_class="total_increasing", category="diagnostic"),
        _d("rejected_today", "sensor", "Rejected today", state_class="total_increasing", category="diagnostic"),
        _d("last_activation", "sensor", "Last activation", device_class="timestamp", category="diagnostic"),
        _d("sw_version", "sensor", "Software version", category="diagnostic"),
        _d("activation", "event", "Activation", event_types=["conversation", "rejected", "shadow"]),
    ]
    return out + hw.descriptors()


ROOM_DESCRIPTORS = {
    "room_temperature": ("Room temperature", {"unit": "°C", "device_class": "temperature"}),
    "room_humidity": ("Room humidity", {"unit": "%", "device_class": "humidity"}),
    "room_pressure": ("Room pressure", {"unit": "hPa", "device_class": "pressure"}),
}


def room_descriptors(keys) -> list[dict]:
    """Sensor descriptors for the detected room sensors (the integration consumes temperature/humidity)."""
    return [_d(k, "sensor", ROOM_DESCRIPTORS[k][0], state_class="measurement", **ROOM_DESCRIPTORS[k][1])
            for k in ROOM_DESCRIPTORS if k in keys]


class Mapper:
    def __init__(self) -> None:
        self.last_duck = DUCK_DEFAULT

    def state(self, cfg: dict | None, wakewords: list[dict], status: dict | None) -> dict:
        """Protocol state from the program's config; None cfg = program down."""
        if cfg is None:
            return {"program_state": "off"}
        duck = cfg.get("music_duck_percent")
        if isinstance(duck, int) and duck > 0:
            self.last_duck = duck
        pos = cfg.get("volume")
        if pos is None and status:
            pos = status.get("volume")
        st = {
            "do_not_disturb": bool(cfg.get("nao_perturbe")),
            "mute": bool(cfg.get("mudo", False)),
            "turn_sounds": cfg.get("sons_turno", True) is not False,
            "alarm_volume": cfg.get("volume_alertas"),
            "alarm_ramp": cfg.get("alarm_ramp_s"),
            "sounds_volume": cfg.get("volume_sons"),
            "whisper_mode": cfg.get("whisper_min_ratio_db") is not None,
            "follow_up": cfg.get("espera_resposta_s"),
            "ducking": bool(duck),
            "ducking_level": duck if duck else self.last_duck,
            "ducking_restore": cfg.get("music_restore_s"),
            "program_state": "idle",
        }
        if pos is not None:
            st["volume"] = percent_from_position(pos)
        for w in wakewords:
            st[f"wakeword_{w['nome']}_mode"] = MODES.get(w.get("modo"))
            st[f"wakeword_{w['nome']}_sensitivity"] = w.get("limiar")
        if status:
            st["program_state"] = STATES.get(str(status.get("estado")), "idle")
        return st

    def to_program(self, cfg: dict, key: str, value) -> dict | None:
        """Translate a `set` into {"config": {...}} or {"wakeword": (name, mode)}; None = unknown key."""
        simple = {"do_not_disturb": "nao_perturbe", "mute": "mudo", "turn_sounds": "sons_turno", "alarm_volume": "volume_alertas", "alarm_ramp": "alarm_ramp_s",
                  "sounds_volume": "volume_sons", "follow_up": "espera_resposta_s",
                  "ducking_level": "music_duck_percent", "ducking_restore": "music_restore_s"}
        if key in simple:
            return {"config": {simple[key]: value}}
        if key == "volume":
            return {"config": {"volume": position_from_percent(value)}}
        if key == "whisper_mode":
            return {"config": {"whisper_min_ratio_db": WHISPER_DEFAULT_DB if value else None}}
        if key == "ducking":
            return {"config": {"music_duck_percent": self.last_duck if value else 0}}
        m = _WW.match(key)
        if not m:
            return None
        name, kind = m.groups()
        if kind == "mode":
            mode = MODES_BACK.get(value)
            return {"wakeword": (name, mode)} if mode else None
        if name == "friday":
            return {"config": {"limiar_palavra": value}}
        extras = [dict(e, limiar=value) if e.get("nome") == name else e for e in cfg.get("palavras_extras", [])]
        return {"config": {"palavras_extras": extras}}


class Tracker:
    """Day counters and conversation state from the program's local events."""

    PENDING_S = 5.0

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._pending: dict[str, float] = {}
        self._act = 0
        self._rej = 0
        self._last = None
        self._conv = False

    def mark_ha(self, keys) -> None:
        """Keys the agent just wrote for HA: their echo from the program is origin `ha`."""
        now = self._clock()
        for k in keys:
            self._pending[k] = now + self.PENDING_S

    def _echo_of_ha(self, keys) -> bool:
        now = self._clock()
        self._pending = {k: t for k, t in self._pending.items() if t > now}
        return bool(keys) and all(k in self._pending for k in keys)

    def counters(self) -> dict:
        return {"activations_today": self._act, "rejected_today": self._rej}

    def reset_day(self) -> dict:
        self._act = self._rej = 0
        return self.counters()

    def on_event(self, tipo: str, dados: dict) -> dict | None:
        if tipo == "ativacao":
            kind = RESULTS.get(dados.get("resultado"))
            if kind is None:
                return None
            state = {}
            if kind == "conversation":
                self._act += 1
                self._last = datetime.now(timezone.utc).isoformat()
                state["last_activation"] = self._last
            elif kind == "rejected":
                self._rej += 1
            state.update(self.counters())
            data = {"word": dados.get("palavra"), "score": dados.get("nota"), "duration_s": dados.get("duracao_s")}
            return {"event": ("activation", kind, data), "state": state}
        if tipo == "conversa":
            self._conv = dados.get("estado") == "aberta"  # idempotent: last one wins
            return {"state": {"program_state": "conversation" if self._conv else "idle"}}
        if tipo == "config":
            changes = dict(dados.get("mudancas") or {})
            origin = ORIGINS.get(dados.get("origem"), "agent")
            if dados.get("origem") == "porta" and self._echo_of_ha(changes):
                origin = "ha"
            return {"config": changes, "origin": origin}
        return None
