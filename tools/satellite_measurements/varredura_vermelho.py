"""DESCARTÁVEL (Task 3, 08/10): procura vermelho SEM mudo. Tom longo = início; cada código: 1 bipe, 6 s aceso
(mede 2 s de microfone), 2 s branco. O dono filma o anel; a ordem dos bipes mapeia os códigos."""
import os, select, subprocess, time, wave
import numpy as np

B1 = [0x04, 0x06, 0x0C, 0x14, 0x24, 0x44, 0x22, 0x26, 0x28, 0x2C, 0x34, 0x60, 0x80, 0x81, 0x84]
B2 = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80]
CODIGOS = [(b, 0) for b in B1] + [(0, b) for b in B2]

def tom_longo(caminho="/tmp/tom_longo.wav"):
    t = np.arange(int(48000 * 1.2)) / 48000
    a = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    with wave.open(caminho, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(a.tobytes())
    return caminho

def toca(c): subprocess.run(["aplay", "-q", "-D", "default", c])

def zeros(s=2):
    b = subprocess.run(["arecord", "-q", "-D", "default", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw",
                        "-d", str(s)], capture_output=True).stdout
    a = np.frombuffer(b, dtype=np.int16); return (a == 0).mean()

hid = os.open("/dev/hidraw0", os.O_RDWR | os.O_NONBLOCK)
def relatorios():
    out = []
    while select.select([hid], [], [], 0)[0]: out.append(os.read(hid, 64)[:3].hex())
    return out

os.write(hid, bytes([3, 0, 0])); time.sleep(1); relatorios()
print(f"branco: microfone com zeros {zeros():.0%} (referência)", flush=True)
toca(tom_longo()); time.sleep(1)
for i, (b1, b2) in enumerate(CODIGOS, 1):
    toca("/home/jose/satelite/sons/bipe.wav")
    t0 = time.time(); os.write(hid, bytes([3, b1, b2]))
    z = zeros(2); time.sleep(max(0, 6 - (time.time() - t0)))
    print(f"{i:2d}º bipe: 03 {b1:02x} {b2:02x}  microfone zeros {z:.0%}  relatorios {relatorios()}", flush=True)
    os.write(hid, bytes([3, 0, 0])); time.sleep(2)
relatorios(); print("FIM", flush=True)
