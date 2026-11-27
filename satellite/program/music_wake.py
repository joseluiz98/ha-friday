"""Music-tolerant wake word (27/11/2026). With music playing, the Jabra Speak 410's echo processing chops the
caller's voice (the "d" of "Friday" disappears): the base model still crosses the verifier threshold, but the personal
verifier (trained on clean speech) and the VAD voice gate reject it. Evidence: MK1 27/11 11:41:46, 11:41:54 and
11:47:21, three real "Friday" barred (verifier 0.06-0.08; voice 0.42-0.82) while Whisper heard no speech at all.

While music plays (and for `music_hold_s` after it stops) every word's verifier is set aside and the voice gate drops
to `music_voice_threshold`. Off = exactly the detector of before. `music_relaxed_wake`: None = auto (on with the Jabra
only; the XVF3800 cancels echo with a reference), or an explicit bool for any board.

Pure logic; the program polls Sendspin and feeds `Playback`. Tests: test_music_wake.py."""

MUSIC_VOICE_DEFAULT = 0.35  # voice of the chopped attempts: 0.42-0.47 (11:41:54), 0.50 (11:47:21)
HOLD_DEFAULT_S = 5.0
POLL_S = 3.0  # `sendspin-cli status` costs ~80 ms on the Pi 3
STALE_S = 15.0  # no answer from the poller for this long = do not trust "playing"


def enabled(cfg: dict, board: str | None) -> bool:
    v = cfg.get("music_relaxed_wake")
    if isinstance(v, bool):
        return v
    return board == "jabra"


def voice_threshold(cfg: dict, relaxed: bool) -> float:
    normal = cfg["limiar_voz_palavra"]
    if not relaxed:
        return normal
    return min(normal, cfg.get("music_voice_threshold", MUSIC_VOICE_DEFAULT))


class Playback:
    """Last Sendspin answer. `active` = playing now, or played within `hold_s` (the poll before the stop)."""
    def __init__(self):
        self._last_playing = None  # monotonic time of the last poll that said "playing"
        self._last_poll = None

    def update(self, playing: bool, now: float):
        self._last_poll = now
        if playing:
            self._last_playing = now

    def active(self, now: float, hold_s: float) -> bool:
        if self._last_playing is None or self._last_poll is None:
            return False
        if now - self._last_poll > STALE_S:
            return False
        if self._last_poll == self._last_playing:
            return True
        return now - self._last_playing <= hold_s


class VerifierBypass:
    """Sets aside every verifier of the openWakeWord model (`custom_verifier_models`, which predict consults) and
    puts the same objects back (the spy of palavra_modelo.espiar included, so barred-word logging survives).
    `set` returns True when it changed something. Call `forget` when the model is rebuilt."""
    def __init__(self):
        self._removed = {}
        self.active = False

    def forget(self):
        self._removed, self.active = {}, False

    def set(self, model, bypass: bool) -> bool:
        vs = getattr(model, "custom_verifier_models", None)
        if vs is None:
            return False
        if bypass and not self.active:
            if not vs:
                return False
            self._removed = dict(vs)
            vs.clear()
            self.active = True
            return True
        if not bypass and self.active:
            vs.update(self._removed)
            self._removed, self.active = {}, False
            return True
        return False
