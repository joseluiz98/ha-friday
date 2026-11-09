import os
"""DESCARTÁVEL (Task 2 do plano do satélite): mede a conexão WebRTC Pi→bot.py. Não é o produto."""
import asyncio, fractions, json, resource, subprocess, sys, time, uuid
import aiohttp, av, numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from openwakeword.model import Model
from openwakeword.vad import VAD

ENDERECO = os.environ["FRIDAY_URL"]
CHUNK = 2560  # 80 ms a 16 kHz mono S16


class Mic(MediaStreamTrack):
    kind = "audio"
    def __init__(self):
        super().__init__(); self.fila = asyncio.Queue(); self.pts = 0
    async def recv(self):
        pcm = await self.fila.get()
        a = np.frombuffer(pcm, dtype=np.int16).reshape(1, -1)
        q = av.AudioFrame.from_ndarray(a, format="s16", layout="mono")
        q.sample_rate = 16000; q.pts = self.pts; q.time_base = fractions.Fraction(1, 16000)
        self.pts += a.shape[1]
        return q


async def main(segundos=20):
    m = Model(wakeword_models=["/home/jose/modelos/friday.onnx"], inference_framework="onnx")
    vad = VAD()
    pc, mic = RTCPeerConnection(), Mic()
    pc.addTrack(mic)
    canal = pc.createDataChannel("rtvi")
    marcos = {"t0": time.monotonic()}
    falas = []

    @canal.on("open")
    def _aberto():
        canal.send(json.dumps({"label": "rtvi-ai", "type": "client-ready", "id": str(uuid.uuid4()),
                               "data": {"version": "2.1.0", "about": {"library": "medicao"}}}))
    @canal.on("message")
    def _msg(msg):
        if isinstance(msg, str) and msg.startswith("{"):
            d = json.loads(msg)
            if d.get("type") in ("bot-started-speaking", "bot-stopped-speaking", "server-message"):
                falas.append((round(time.monotonic() - marcos["t0"], 2), d["type"]))
    aplay = subprocess.Popen(["aplay", "-q", "-D", "default", "-t", "raw", "-f", "S16_LE", "-r", "48000",
                              "-c", "2", "--buffer-time=100000"], stdin=subprocess.PIPE)
    @pc.on("track")
    def _trilha(t):
        async def tocar():
            while True:
                q = await t.recv()
                marcos.setdefault("primeiro_audio", time.monotonic() - marcos["t0"])
                aplay.stdin.write(q.to_ndarray().tobytes())
        asyncio.ensure_future(tocar())
    of = await pc.createOffer(); await pc.setLocalDescription(of)
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{ENDERECO}/api/offer", ssl=False,
                          json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}) as r:
            resp = await r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=resp["sdp"], type=resp["type"]))
    while pc.connectionState != "connected":
        await asyncio.sleep(0.02)
    marcos["conectado"] = time.monotonic() - marcos["t0"]
    async def pingar():
        while True:
            canal.send(f"ping: {time.time()}"); await asyncio.sleep(1)
    asyncio.ensure_future(pingar())
    rec = await asyncio.create_subprocess_exec("arecord", "-q", "-D", "default", "-f", "S16_LE", "-r", "16000",
                                               "-c", "1", "-t", "raw", stdout=asyncio.subprocess.PIPE)
    cpu0, fim, lentos = time.process_time(), time.monotonic() + segundos, 0
    while time.monotonic() < fim:
        pcm = await rec.stdout.readexactly(CHUNK)
        a = time.perf_counter()
        x = np.frombuffer(pcm, dtype=np.int16)
        m.predict(x); vad.predict(x, frame_size=640)
        lentos += (time.perf_counter() - a) > 0.08
        mic.fila.put_nowait(pcm)
    cpu = (time.process_time() - cpu0) / segundos
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    rec.terminate(); await pc.close(); aplay.terminate()
    print(json.dumps({"conectado_s": round(marcos["conectado"], 2),
                      "primeiro_audio_s": round(marcos.get("primeiro_audio", -1), 2),
                      "cpu_nucleos": round(cpu, 2), "rss_mb": round(rss), "pedacos_lentos": lentos,
                      "eventos": falas}, ensure_ascii=False))

asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 20))
