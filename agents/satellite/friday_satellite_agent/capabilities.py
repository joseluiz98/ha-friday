"""Fixed mapping between the satellite program's settings and the F.R.I.D.A.Y. protocol.

Pure Python (no I/O). `Mapper` keeps the last non-zero ducking level; `Tracker` turns the
program's local events into protocol state, events and counters (idempotent).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timezone

from . import hardware as hw
from friday_agent_core import version as _version

VERSION = _version.installed_version(_version.FILE)
WHISPER_DEFAULT_DB = 5.5
DUCK_DEFAULT = 15
INT_KEYS = {"ducking_level", "alarm_volume", "follow_up_window"}
MODES = {"ativa": "active", "sombra": "shadow", "desligada": "off"}
MODES_BACK = {v: k for k, v in MODES.items()}
STATES = {"parada": "idle", "alarme": "idle", "cadastro": "idle", "conectando": "listening",
          "ouvindo": "conversation", "aguardando": "conversation", "cortada": "conversation"}
ONBOARDING_RESULTS = ("wake_word_ok", "wake_word_failed", "area_selected")
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
        _d("reply_wait", "number", "Reply wait", min=2, max=30, step=1, unit="s", category=c, mode="box"),
        _d("follow_up_window", "number", "Follow-up window", min=0, max=30, step=1, unit="s", category=c, mode="box"),
        _d("no_follow_up_while_music", "switch", "No follow-up while music plays", category=c, icon="mdi:music-off"),
        _d("no_follow_up_after_home_commands", "switch", "No follow-up after home commands", category=c,
           icon="mdi:home-export-outline"),
        _d("end_of_speech_pause", "number", "End-of-speech pause", min=0.5, max=2.0, step=0.1, unit="s",
           category=c, mode="box"),
        _d("close_mic_awaiting_reply", "switch", "Close mic while awaiting reply", category=c,
           icon="mdi:microphone-message-off"),
        _d("ducking", "switch", "Music ducking", category=c, icon="mdi:music-note-minus"),
        _d("ducking_level", "number", "Ducking level", min=1, max=25, step=1, unit="%", category=c, mode="box"),
        _d("ducking_restore", "number", "Ducking restore delay", min=0, max=10, step=0.5, unit="s",
           category=c, mode="box"),
        _d("voice_enabled", "switch", "Voice", icon="mdi:account-voice"),
        _d("mute", "switch", "Mute", icon="mdi:microphone-off"),
        _d("turn_sounds", "switch", "Turn sounds", category=c, icon="mdi:bell-ring"),
        _d("reboot", "button", "Reboot", device_class="restart", category=c),
        _d("restart_program", "button", "Restart program", hidden=True, available=False, category=c),
        _d("reset_network", "button", "Reset network", category=c, icon="mdi:wifi-cog"),
        _d("reset_home", "button", "Forget home", category=c, icon="mdi:home-remove"),
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
        _d("activations_today", "sensor", "Activations today", category="diagnostic"),
        _d("rejected_today", "sensor", "Rejected today", category="diagnostic"),
        _d("last_activation", "sensor", "Last activation", device_class="timestamp", category="diagnostic"),
        _d("last_wake_word", "sensor", "Last wake word", category="diagnostic", icon="mdi:account-voice"),
        _d("sw_version", "sensor", "Software version", category="diagnostic"),
        _d("activation", "event", "Activation", event_types=["conversation", "rejected", "shadow"]),
        _d("next_timer", "sensor", "Next timer", device_class="timestamp", icon="mdi:timer-outline"),
        _d("timer", "event", "Timer", event_types=["finished"], icon="mdi:timer-check-outline"),
        _d("onboarding", "event", "Onboarding", event_types=list(ONBOARDING_RESULTS), category="diagnostic",
           icon="mdi:account-voice"),
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


def timers_state(timers) -> dict:
    """Next timer from the program's timers file (list of {id, nome, duracao_s, termino(epoch)})."""
    valid = sorted((t for t in (timers if isinstance(timers, list) else [])
                    if isinstance(t, dict) and isinstance(t.get("termino"), (int, float))),
                   key=lambda t: t["termino"])
    if not valid:
        return {"next_timer": None, "next_timer_attributes": {"total_active": 0}}
    t = valid[0]
    return {"next_timer": datetime.fromtimestamp(t["termino"], timezone.utc).isoformat(),
            "next_timer_attributes": {"name": t.get("nome"), "id": t.get("id"),
                                      "duration_s": t.get("duracao_s"), "total_active": len(valid)}}


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
            # software mute (HA) OR the Jabra's own mute button (detected by the program, `mudo` in /status)
            "mute": bool(cfg.get("mudo", False)) or bool(status and status.get("mudo") is True),
            "turn_sounds": cfg.get("sons_turno", True) is not False,
            "alarm_volume": cfg.get("volume_alertas"),
            "alarm_ramp": cfg.get("alarm_ramp_s"),
            "sounds_volume": cfg.get("volume_sons"),
            "whisper_mode": cfg.get("whisper_min_ratio_db") is not None,
            "reply_wait": cfg.get("espera_resposta_s"),
            "follow_up_window": cfg.get("continuacao_s"),
            "no_follow_up_while_music": cfg.get("no_follow_up_while_music", True) is not False,
            "no_follow_up_after_home_commands": cfg.get("no_follow_up_after_home_commands", True) is not False,
            "end_of_speech_pause": cfg.get("end_of_speech_pause_s", 0.8),
            "close_mic_awaiting_reply": cfg.get("close_mic_awaiting_reply") is True,
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
                  "sounds_volume": "volume_sons", "reply_wait": "espera_resposta_s",
                  "follow_up_window": "continuacao_s", "no_follow_up_while_music": "no_follow_up_while_music",
                  "no_follow_up_after_home_commands": "no_follow_up_after_home_commands",
                  "end_of_speech_pause": "end_of_speech_pause_s",
                  "ducking_level": "music_duck_percent", "ducking_restore": "music_restore_s",
                  "close_mic_awaiting_reply": "close_mic_awaiting_reply"}
        if key in simple:
            if key in INT_KEYS and isinstance(value, float) and value.is_integer():
                value = int(value)  # HA number entities send 20.0; the program accepts only int here
            return {"config": {simple[key]: value}}
        if key == "volume":
            return {"config": {"volume": position_from_percent(value)}}
        if key == "whisper_mode":
            return {"config": {"whisper_min_ratio_db": WHISPER_DEFAULT_DB if value else None}}
        if key == "ducking":
            return {"config": {"music_duck_percent": int(self.last_duck) if value else 0}}
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


_CAUSES = {"verificador": "verifier", "sem_voz": "no_voice", "paciencia": "patience"}
_MODES = {"ativa": "active", "sombra": "shadow"}
_PLAIN = {"limiar": "threshold", "voz": "voice", "limiar_voz": "voice_threshold",
          "nota_verificador": "verifier_score", "limiar_verificador": "verifier_threshold",
          "razao_sussurro_db": "whisper_ratio_db", "sussurro": "whisper", "paciencia": "patience"}
_REC = re.compile(r"^[A-Za-z0-9_-]+\.wav$")


def activation_data(dados: dict) -> dict:
    """Map the program's Portuguese payload to English event attributes (absent stays absent)."""
    data = {"word": dados.get("palavra"), "score": dados.get("nota"), "duration_s": dados.get("duracao_s")}
    if dados.get("causa") in _CAUSES:
        data["cause"] = _CAUSES[dados["causa"]]
    if dados.get("modo") in _MODES:
        data["mode"] = _MODES[dados["modo"]]
    for src, dst in _PLAIN.items():
        if dados.get(src) is not None:
            data[dst] = dados[src]
    rec = dados.get("gravacao")
    if isinstance(rec, str) and _REC.match(rec):
        data["recording"] = rec
    return data


class Tracker:
    """Day counters and conversation state from the program's local events."""

    PENDING_S = 5.0

    def __init__(self, clock=time.monotonic, path: str | None = None, today=None) -> None:
        self._clock = clock
        self._path = path
        self._today = today or (lambda: date.today().isoformat())
        self._pending: dict[str, float] = {}
        self._act = 0
        self._rej = 0
        self._by_word: dict[str, int] = {}
        self._last = None
        self._conv = False
        self._load()

    def _load(self) -> None:
        """Restore today's counts after an agent restart (counts of an earlier day are dropped)."""
        if not self._path:
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("date") == self._today():
                self._act, self._rej = int(saved.get("act", 0)), int(saved.get("rej", 0))
                words = saved.get("by_word")
                if isinstance(words, dict):
                    self._by_word = {str(k): int(v) for k, v in words.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save(self) -> None:
        if not self._path:
            return
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"date": self._today(), "act": self._act, "rej": self._rej,
                           "by_word": self._by_word}, f)
            os.replace(tmp, self._path)
        except OSError:
            pass

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
        return {"activations_today": self._act, "rejected_today": self._rej,
                "activations_today_attributes": {"by_word": dict(self._by_word)}}

    def reset_day(self) -> dict:
        self._act = self._rej = 0
        self._by_word = {}
        self._save()
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
                word = dados.get("palavra")
                if isinstance(word, str) and word:
                    self._by_word[word] = self._by_word.get(word, 0) + 1
                    state["last_wake_word"] = word
                    state["last_wake_word_attributes"] = {"score": dados.get("nota"), "at": self._last}
            elif kind == "rejected":
                self._rej += 1
            self._save()
            state.update(self.counters())
            data = activation_data(dados)
            return {"event": ("activation", kind, data), "state": state}
        if tipo == "onboarding":
            result = dados.get("resultado")
            if result not in ONBOARDING_RESULTS:
                return None
            return {"event": ("onboarding", result, {k: v for k, v in dados.items() if k != "resultado"})}
        if tipo == "timer":
            return {"event": ("timer", "finished", {"name": dados.get("nome"), "id": dados.get("id"),
                                                    "duration_s": dados.get("duracao_s")})}
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
