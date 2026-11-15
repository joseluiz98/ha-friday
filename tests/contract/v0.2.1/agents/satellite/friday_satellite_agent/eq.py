"""Simple 3-band equalizer (bass/mid/treble) on the Pi output, on top of the Music Assistant EQ.

Uses the ALSA `equal` plugin (alsaequal + LADSPA `caps` Eq10) inserted after dmix. Declared only if the
`equal` control device exists; today it does not (spike pending), so no capability is declared.
All three bands at 0 dB = flat = today's sound. Nothing is ever written to Music Assistant.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess

# Eq10 controls, in order: 31 63 125 | 250 500 1k 2k | 4k 8k 16k Hz.
CONTROLS = ["00. 31 Hz", "01. 63 Hz", "02. 125 Hz", "03. 250 Hz", "04. 500 Hz",
            "05. 1 kHz", "06. 2 kHz", "07. 4 kHz", "08. 8 kHz", "09. 16 kHz"]
BANDS = {"eq_bass": range(0, 3), "eq_mid": range(3, 7), "eq_treble": range(7, 10)}
FLAT = 66   # alsaequal slider value for 0 dB; STEP per dB is a spike calibration
STEP = 1.4
LIMIT = 12
DEFAULT_STATE = os.path.expanduser("~/.friday-agent/eq.json")


def run_command(argv: list[str], timeout: float = 3.0) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
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


def has_all_controls(text: str) -> bool:
    return all(f"'{n}'" in text for n in CONTROLS)


class Equalizer:
    """Inert (no capability, no writes) until detect() finds the `equal` device with all 10 Eq10 controls."""

    def __init__(self, runner=run_command, state_path: str = DEFAULT_STATE) -> None:
        self._run = runner
        self._path = state_path
        self._gains = {k: 0 for k in BANDS}
        self.available = False
        self._want: dict = {}
        self._lock = asyncio.Lock()

    def detect(self) -> bool:
        """Blocking (runs amixer): call via asyncio.to_thread."""
        rc, out = self._run(["amixer", "-D", "equal", "scontrols"])
        self.available = rc == 0 and has_all_controls(out)
        if self.available:
            self._gains = self._load()
            self._want = dict(self._gains)
        return self.available

    def _load(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return {k: _clamp(data.get(k, 0)) for k in BANDS}
        except (OSError, ValueError, TypeError, AttributeError):
            return {k: 0 for k in BANDS}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._gains, f)
        except OSError:
            pass

    def descriptors(self) -> list[dict]:
        if not self.available:
            return []
        names = {"eq_bass": "Bass", "eq_mid": "Mid", "eq_treble": "Treble"}
        return [{"key": k, "platform": "number", "name": n, "min": -LIMIT, "max": LIMIT, "step": 1,
                 "unit": "dB", "category": "config", "mode": "slider"} for k, n in names.items()]

    def state(self) -> dict:
        return dict(self._gains) if self.available else {}

    def _write(self) -> bool:
        """Blocking. Writes the wanted gains band by band; reported gains advance only per fully written band."""
        want = dict(self._want)
        vals = control_values(want["eq_bass"], want["eq_mid"], want["eq_treble"])
        ok = True
        for key, idx in BANDS.items():
            if self._gains[key] == want[key]:
                continue
            band_ok = True
            for i in idx:
                rc, _ = self._run(["amixer", "-D", "equal", "cset", f"name={CONTROLS[i]}", str(vals[CONTROLS[i]])])
                band_ok = band_ok and rc == 0
            if band_ok:
                self._gains[key] = want[key]
            ok = ok and band_ok
        self._save()
        return ok

    async def apply(self, changes: dict) -> dict:
        """Last value wins: concurrent callers merge into the wanted gains; the writer applies the latest."""
        if not self.available:
            return {"ok": False, "error": "unavailable"}
        for k in BANDS:
            if k in changes:
                self._want[k] = _clamp(changes[k])
        async with self._lock:
            if self._want == self._gains:
                return {"ok": True}
            ok = await asyncio.to_thread(self._write)
        return {"ok": True} if ok else {"ok": False, "error": "command_failed"}

    async def startup(self) -> dict:
        """Re-apply the persisted (or flat) gains so audio and HA agree."""
        if not self.available:
            return {"ok": False, "error": "unavailable"}
        self._gains = {k: None for k in BANDS}  # force a write of every band
        async with self._lock:
            ok = await asyncio.to_thread(self._write)
        return {"ok": True} if ok else {"ok": False, "error": "command_failed"}
