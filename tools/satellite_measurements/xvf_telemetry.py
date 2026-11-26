"""Read-only XVF3800 telemetry logger (two-beam listening study, 26/11/2026).

Runs next to the satellite program on the same Pi, in its own process, and only READS control registers (vendor
control transfers to the device; no interface is claimed, nothing is written, never SAVE_CONFIGURATION). Twice a second
it records the beam azimuths, the per-beam speech energy, the processed DoA and the fixed-beam state, so we can learn
where the TV sits and how speech energy splits between beams in real use. Output: one NDJSON file per local day in
--out (default ~/xvf-telemetry). Stops by itself after --days.

    sudo systemd-run --unit=friday-xvf-telemetry --uid=jose -p Nice=19 -p MemoryMax=80M -p RuntimeMaxSec=7d \
        /opt/friday/current/venv/bin/python /home/jose/xvf-telemetry/xvf_telemetry.py

Record fields: t (epoch s), az (deg: focused 0, focused 1, free-running, auto-select), e (speech energy, same order),
sel (deg: processed DoA, auto-select; null = no speech), doa ([deg, speech flag], Seeed), fx (fixed beams on).
Every 30 s a slow record adds fxaz (fixed azimuths, deg), op_l / op_r (output routing), agc (PP_AGCGAIN), rt60.
"""
import argparse
import datetime
import json
import math
import os
import struct
import time

VID, PID = 0x2886, 0x001A
OK, RETRY = 0, 64
# name: (resid, cmdid, count, type) - copied from the vendor xvf_host.py PARAMETERS table (fw 2.1.1)
REG = {
    "AEC_AZIMUTH_VALUES": (33, 75, 4, "f"),
    "AEC_SPENERGY_VALUES": (33, 80, 4, "f"),
    "AUDIO_MGR_SELECTED_AZIMUTHS": (35, 11, 2, "f"),
    "DOA_VALUE": (20, 18, 2, "H"),
    "AEC_FIXEDBEAMSONOFF": (33, 37, 1, "i"),
    "AEC_FIXEDBEAMSAZIMUTH_VALUES": (33, 81, 2, "f"),
    "AUDIO_MGR_OP_L": (35, 15, 2, "B"),
    "AUDIO_MGR_OP_R": (35, 19, 2, "B"),
    "PP_AGCGAIN": (17, 13, 1, "f"),
    "AEC_RT60": (33, 9, 1, "f"),
}
SIZE = {"f": 4, "i": 4, "H": 2, "B": 1}
FAST = ("AEC_AZIMUTH_VALUES", "AEC_SPENERGY_VALUES", "AUDIO_MGR_SELECTED_AZIMUTHS", "DOA_VALUE", "AEC_FIXEDBEAMSONOFF")
SLOW = ("AEC_FIXEDBEAMSAZIMUTH_VALUES", "AUDIO_MGR_OP_L", "AUDIO_MGR_OP_R", "PP_AGCGAIN", "AEC_RT60")


def decode(name, payload: bytes):
    _, _, n, t = REG[name]
    return list(struct.unpack("<" + t * n, payload[1:1 + n * SIZE[t]]))


def deg(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(math.degrees(v) % 360, 1)


class Board:
    """Short timeouts and at most 5 retries: a slow read never piles up behind the satellite program."""
    def __init__(self, timeout_ms=300):
        self.timeout, self.dev = timeout_ms, None

    def read(self, name):
        import usb.core
        import usb.util
        if self.dev is None:
            self.dev = usb.core.find(idVendor=VID, idProduct=PID)
            if self.dev is None:
                return None
        resid, cmd, n, t = REG[name]
        req = usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE
        try:
            for _ in range(5):
                r = bytes(self.dev.ctrl_transfer(req, 0, 0x80 | cmd, resid, n * SIZE[t] + 1, self.timeout))
                if r[0] == OK:
                    return decode(name, r)
                if r[0] != RETRY:
                    return None
                time.sleep(0.01)
        except Exception:  # noqa: BLE001 - board gone or busy: reopen on the next read
            self.dev = None
        return None


def fast_record(b: Board, now: float) -> dict:
    az, e, sel, doa, fx = (b.read(n) for n in FAST)
    return {"t": round(now, 2), "az": [deg(x) for x in az] if az else None,
            "e": [round(x, 4) for x in e] if e else None, "sel": [deg(x) for x in sel] if sel else None,
            "doa": doa, "fx": fx[0] if fx else None}


def slow_record(b: Board, now: float) -> dict:
    fxaz, opl, opr, agc, rt60 = (b.read(n) for n in SLOW)
    return {"t": round(now, 2), "slow": 1, "fxaz": [deg(x) for x in fxaz] if fxaz else None, "op_l": opl,
            "op_r": opr, "agc": round(agc[0], 2) if agc else None, "rt60": round(rt60[0], 3) if rt60 else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/xvf-telemetry"))
    ap.add_argument("--period", type=float, default=0.5)
    ap.add_argument("--days", type=float, default=7.0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    b, fim, prox_lento = Board(), time.time() + a.days * 86400, 0.0
    while time.time() < fim:
        ini = time.time()
        rec = fast_record(b, ini)
        linhas = [rec]
        if ini >= prox_lento:
            linhas.append(slow_record(b, ini))
            prox_lento = ini + 30.0
        dia = datetime.datetime.fromtimestamp(ini).strftime("%Y%m%d")
        with open(os.path.join(a.out, dia + ".ndjson"), "a") as f:
            for r in linhas:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        time.sleep(max(0.05, a.period - (time.time() - ini)))


if __name__ == "__main__":
    main()
