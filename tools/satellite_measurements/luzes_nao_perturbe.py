"""DESCARTÁVEL (Task 3): mostra combinações HID do anel do Jabra e mede se cortam o microfone."""
import os, subprocess, sys, time
import numpy as np
CANDIDATAS = [0x02, 0x20, 0x40, 0x03, 0x11, 0x12, 0x18, 0x21, 0x30, 0x41, 0x50]

def toca_bipes(n):
    for _ in range(n):
        subprocess.run(["aplay", "-q", "-D", "default", "/home/jose/satelite/sons/bipe.wav"]); time.sleep(0.15)

def zeros_no_microfone(segundos=2):
    bruto = subprocess.run(["arecord", "-q", "-D", "default", "-f", "S16_LE", "-r", "16000", "-c", "1",
                            "-t", "raw", "-d", str(segundos)], capture_output=True).stdout
    a = np.frombuffer(bruto, dtype=np.int16)
    return float((a == 0).mean())

# 06/10: ritmo para o dono anotar sem contar bipes — 1 bipe, a luz fica 10 s, branco 3 s entre uma e outra.
hid = os.open("/dev/hidraw0", os.O_RDWR)
os.write(hid, bytes([3, 0, 0])); print("branco (referência) por 5 s", flush=True); time.sleep(5)
for i, bits in enumerate(CANDIDATAS, 1):
    toca_bipes(1)
    os.write(hid, bytes([3, bits, 0])); print(f"{i:2d}) 0x{bits:02x}", flush=True)
    t0 = time.time(); z = zeros_no_microfone(2); time.sleep(max(0, 10 - (time.time() - t0)))
    print(f"    microfone com zeros: {z:.0%}", flush=True)
    os.write(hid, bytes([3, 0, 0])); time.sleep(3)
print("FIM", flush=True)
