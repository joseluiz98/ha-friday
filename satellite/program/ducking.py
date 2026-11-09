"""Ducking da música (spec 2026-10-21 §6.2): abaixa o controle `Music` (softvol) quando o satélite fala/ouve e
restaura ~1 s depois. Lógica pura: quem escreve no mixer é a função injetada (`MusicMixer.write`). Testes:
test_ducking.py.

Sem rede no caminho. Falha ao escrever nunca derruba o programa: só registra `music_duck_error`."""
import json

# 04/11: pergunta ao Sendspin se há música tocando (a Pi roda como `jose`, com sudo sem senha; o socket é do root)
SENDSPIN_STATUS = ["sudo", "-n", "sendspin-cli", "status", "--control-socket", "/run/sendspin-cli/control.sock"]


def sendspin_playing(status_text: str) -> bool:
    """True só com uma linha exatamente `state: playing` (espaços nas pontas ignorados)."""
    return any(linha.strip() == "state: playing" for linha in (status_text or "").splitlines())


def should_duck(maestro_state: str, last_output, now: float, output_grace_s: float = 0.5) -> bool:
    """Abaixa enquanto o maestro não está parado (conversa, alarme, cadastro...) e por `output_grace_s` depois de
    qualquer saída do programa (sons, recado, bipe de volume), mesmo fora de conversa."""
    if maestro_state != "parada":
        return True
    return last_output is not None and (now - last_output) < output_grace_s


class MusicDucker:
    def __init__(self, write, duck_percent: int = 15, restore_s: float = 1.0, ramp_s: float = 0.3, log=print):
        self._write = write
        self.duck_percent = duck_percent
        self.restore_s = restore_s
        self.ramp_s = ramp_s
        self._log = log
        self._level = 100.0       # nível lógico atual (0–100)
        self._last_written = None
        self._from, self._t0 = 100.0, 0.0
        self._last_active = -1e9
        self._target = 100
        self._ramp = None  # (início, duração_s): subida de alarme crescente com música (ramp_to)

    def _emit(self, percent: int):
        if percent == self._last_written:
            return
        try:
            self._write(percent)
            self._last_written = percent
        except Exception as e:  # o ducking falha calado: a música fica como está
            self._log(json.dumps({"music_duck_error": f"{type(e).__name__}: {e}"}))

    def _set_target(self, target: int, now: float):
        if target != self._target:
            self._target, self._from, self._t0 = target, self._level, now

    def start(self, now: float):
        """Ao subir o programa a música volta a 100%: nunca fica presa em 15% depois de um crash."""
        self._level, self._target, self._from, self._t0 = 100.0, 100, 100.0, now
        self._last_active = -1e9
        self._last_written = None
        self._emit(100)

    RAMP_GRACE_S = 0.7  # o fim da campainha ainda conta como saída do programa: não corta a subida

    def ramp_to(self, start_percent: int, seconds: float, now: float):
        """Alarme crescente com música (spec §11): o `Music` sobe de `start_percent` a 100% em `seconds`. A fala
        durante a subida abaixa como sempre (e cancela a rampa); só o 'rabo' da campainha, nos primeiros
        RAMP_GRACE_S, é ignorado."""
        self._level = self._from = float(start_percent)
        self._target, self._t0, self._ramp = 100, now, (now, float(seconds))
        self._last_active = now - self.restore_s - 1  # nada de restauração pendente
        self._emit(int(start_percent))

    def update(self, active: bool, now: float):
        if self._ramp is not None:
            inicio, dur = self._ramp
            if active and now - inicio >= self.RAMP_GRACE_S:
                self._ramp = None  # alguém falou: ducking normal
            else:
                frac = 1.0 if dur <= 0 else min(1.0, (now - inicio) / dur)
                self._level = self._from + (100 - self._from) * frac
                self._emit(int(round(self._level)))
                if frac >= 1.0:
                    self._ramp = None
                return
        if active:
            self._last_active = now
            self._set_target(min(self.duck_percent, 100), now)
        else:
            if now - self._last_active >= self.restore_s:  # conta do último instante ativo
                self._set_target(100, now)
        if self._level == self._target:
            return
        frac = 1.0 if self.ramp_s <= 0 else min(1.0, max(0.0, (now - self._t0) / self.ramp_s))
        self._level = self._from + (self._target - self._from) * frac
        self._emit(int(round(self._level)))
