"""Simple 3-band equalizer (bass/mid/treble) on the Pi output, on top of the Music Assistant EQ.

Uses the ALSA `equal` plugin (alsaequal + LADSPA `caps` Eq10) inserted after dmix. Declared only if the
`equal` control device exists; today it does not (spike pending), so no capability is declared.
All three bands at 0 dB = flat = today's sound. Nothing is ever written to Music Assistant.
"""

from __future__ import annotations

import subprocess

# Eq10 controls, in order: 31 63 125 | 250 500 1k 2k | 4k 8k 16k Hz.
CONTROLS = ["00. 31 Hz", "01. 63 Hz", "02. 125 Hz", "03. 250 Hz", "04. 500 Hz",
            "05. 1 kHz", "06. 2 kHz", "07. 4 kHz", "08. 8 kHz", "09. 16 kHz"]
BANDS = {"eq_bass": range(0, 3), "eq_mid": range(3, 7), "eq_treble": range(7, 10)}
FLAT = 66   # alsaequal slider value for 0 dB; STEP per dB is a spike calibration
STEP = 1.4
LIMIT = 12


def run_command(argv: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _clamp(db) -> int:
    return max(-LIMIT, min(LIMIT, int(round(float(db)))))


def control_values(bass=0, mid=0, treble=0) -> dict[str, int]:
    gains = {"eq_bass": _clamp(bass), "eq_mid": _clamp(mid), "eq_treble": _clamp(treble)}
    out = {}
    for key, idx in BANDS.items():
        for i in idx:
            out[CONTROLS[i]] = round(FLAT + STEP * gains[key])
    return out


class Equalizer:
    def __init__(self, runner=run_command) -> None:
        self._run = runner
        self._gains = {k: 0 for k in BANDS}
        rc, _ = self._run(["amixer", "-D", "equal", "scontrols"])
        self.available = rc == 0

    def descriptors(self) -> list[dict]:
        if not self.available:
            return []
        names = {"eq_bass": "Bass", "eq_mid": "Mid", "eq_treble": "Treble"}
        return [{"key": k, "platform": "number", "name": n, "min": -LIMIT, "max": LIMIT, "step": 1,
                 "unit": "dB", "category": "config", "mode": "slider"} for k, n in names.items()]

    def state(self) -> dict:
        return dict(self._gains) if self.available else {}

    def apply(self, changes: dict) -> bool:
        if not self.available:
            return False
        for k in BANDS:
            if k in changes:
                self._gains[k] = _clamp(changes[k])
        vals = control_values(self._gains["eq_bass"], self._gains["eq_mid"], self._gains["eq_treble"])
        ok = True
        for name, v in vals.items():
            rc, _ = self._run(["amixer", "-D", "equal", "cset", f"name={name}", str(v)])
            ok = ok and rc == 0
        return ok
