"""Controlled two-beam measurement session (spec D9). Run ON the Pi with the satellite program STOPPED.

    python3 two_beam_session.py --out DIR [--dry-run]

Steps (prompts in Portuguese): 1. TV alone, 60 s; 2. elevation sweep; 3. owner speaking from the sofa, TV off, 60 s;
4. the same with the TV on, beam A on the owner and beam T on the TV. Each step records a WAV with arecord and logs
the registers with xvf_telemetry.Board. The beams are always released in a `finally`; SAVE_CONFIGURATION is never sent.
"""
import argparse
import json
import math
import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xvf_telemetry as xt  # noqa: E402

REG_W = {
    "AEC_FIXEDBEAMSONOFF": (33, 37, 1, "i"),
    "AEC_FIXEDBEAMSAZIMUTH_VALUES": (33, 81, 2, "f"),
    "AEC_FIXEDBEAMSELEVATION_VALUES": (33, 82, 2, "f"),
    "AUDIO_MGR_OP_L": (35, 15, 2, "B"),
    "AUDIO_MGR_OP_R": (35, 19, 2, "B"),
}
ELEVATIONS = [0, 15, 30, 45, 60, 75]
STEPS = [
    "1. TV sozinha, 60 s: mede o azimute da TV (feixe livre)",
    "2. Varredura de elevacao (0..75 graus, 3 s cada) com um feixe fixo na TV",
    "3. Dono falando do sofa, TV desligada, 60 s",
    "4. O mesmo com a TV ligada: feixe A no dono, feixe T na TV",
]


class WBoard(xt.Board):
    """Board with vendor OUT writes (same control transfer as reads, direction OUT)."""

    def write(self, name, values):
        import usb.core
        import usb.util
        if self.dev is None:
            self.dev = usb.core.find(idVendor=xt.VID, idProduct=xt.PID)
        resid, cmd, n, t = REG_W[name]
        payload = b"".join(struct.pack("<" + t, v) for v in values)
        req = usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE
        self.dev.ctrl_transfer(req, 0, cmd, resid, payload, self.timeout)


def cleanup(b):
    """Fixed beams off, elevations [0, 0], outputs 7 3 / 8 0. Every write is tried even if one fails."""
    for name, vals in (("AEC_FIXEDBEAMSONOFF", [0]), ("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, 0.0]),
                       ("AUDIO_MGR_OP_R", [7, 3]), ("AUDIO_MGR_OP_L", [8, 0])):
        try:
            b.write(name, vals)
        except Exception as e:  # noqa: BLE001
            print("cleanup failed for %s: %s" % (name, e), file=sys.stderr)


def record(out, name, seconds, b, log):
    wav = os.path.join(out, name + ".wav")
    p = subprocess.Popen(["arecord", "-D", "hw:CARD=Array", "-f", "S16_LE", "-r", "48000", "-c", "2",
                          "-d", str(int(seconds)), wav])
    end = time.time() + seconds
    while time.time() < end:
        r = xt.fast_record(b, time.time())
        r["step"] = name
        log.write(json.dumps(r, separators=(",", ":")) + "\n")
        log.flush()
        time.sleep(0.5)
    p.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        print("Sessao controlada (dry-run), saida em %s" % a.out)
        for s in STEPS:
            print(" ", s)
        print("  Ao final (sempre): feixes fixos off, elevacoes [0,0], OP_R 7 3, OP_L 8 0; sem SAVE_CONFIGURATION")
        return
    os.makedirs(a.out, exist_ok=True)
    b = WBoard()
    with open(os.path.join(a.out, "registers.ndjson"), "a") as log:
        try:
            input("Passo 1: so a TV ligada, sala em silencio. Enter para comecar (60 s)... ")
            record(a.out, "1_tv_sozinha", 60, b, log)
            az = b.read("AEC_AZIMUTH_VALUES")
            tv_rad = az[2] if az and az[2] == az[2] else 0.0
            print("Azimute livre da TV: %s graus" % xt.deg(tv_rad))
            input("Passo 2: varredura de elevacao, fique em silencio. Enter... ")
            best = {}
            b.write("AEC_FIXEDBEAMSAZIMUTH_VALUES", [tv_rad, tv_rad])
            for phi in ELEVATIONS:
                b.write("AEC_FIXEDBEAMSELEVATION_VALUES", [math.radians(phi)] * 2)
                b.write("AEC_FIXEDBEAMSONOFF", [1])
                vals, end = [], time.time() + 3
                while time.time() < end:
                    e = b.read("AEC_SPENERGY_VALUES")
                    if e:
                        vals.append(e[0])
                    time.sleep(0.125)
                best[phi] = sum(vals) / len(vals) if vals else 0.0
            cleanup(b)
            json.dump(best, open(os.path.join(a.out, "energy_by_elevation.json"), "w"))
            print("Energia por elevacao:", best)
            input("Passo 3: TV DESLIGADA; fale do sofa por 60 s. Enter... ")
            record(a.out, "3_dono_sem_tv", 60, b, log)
            owner = input("Azimute do dono (graus, medido no passo 3 ou estimado): ")
            input("Passo 4: TV LIGADA; fale do sofa por 60 s. Enter... ")
            b.write("AEC_FIXEDBEAMSAZIMUTH_VALUES", [math.radians(float(owner)), tv_rad])
            b.write("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, math.radians(max(best, key=best.get))])
            b.write("AEC_FIXEDBEAMSONOFF", [1])
            record(a.out, "4_dono_com_tv", 60, b, log)
        finally:
            cleanup(b)
    print("Pronto. Feixes liberados.")


if __name__ == "__main__":
    main()
