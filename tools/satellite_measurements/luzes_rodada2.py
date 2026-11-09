"""DESCARTÁVEL (Task 3, rodada 2, 06/10): 41 (candidata), 02 e 20 (perdidas), depois 41 x 10 alternando.
Bipes = número da luz. Branco 3 s entre uma e outra."""
import os, subprocess, time

def bipes(n):
    for _ in range(n):
        subprocess.run(["aplay", "-q", "-D", "default", "/home/jose/satelite/sons/bipe.wav"]); time.sleep(0.15)

hid = os.open("/dev/hidraw0", os.O_RDWR)
def luz(b): os.write(hid, bytes([3, b, 0]))
luz(0); time.sleep(3)
for i, b in enumerate([0x41, 0x02, 0x20], 1):
    bipes(i); luz(b); print(f"{i}) 0x{b:02x}", flush=True); time.sleep(10); luz(0); time.sleep(3)
bipes(4); print("4) 0x41 x 0x10 alternando a cada 3 s", flush=True)
for _ in range(3):
    luz(0x41); time.sleep(3); luz(0x10); time.sleep(3)
luz(0); print("FIM", flush=True)
