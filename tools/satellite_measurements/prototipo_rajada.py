import os
"""DESCARTÁVEL (Task 2, step 4): pré-gravação em rajada + corte por silêncio, já no modo satélite.
Uso: prototipo_rajada.py normal|rajada
- normal: conecta, espera 2 s, bipe, o dono pergunta.
- rajada: bipe, o dono pergunta JÁ; a conexão só abre 0,8 s depois (como após a palavra) e o áudio
  guardado sai primeiro.
Nos dois: quando o VAD vê 0,8 s de silêncio depois da fala, manda 1,5 s de zeros e PARA de mandar áudio
(o corte do produto). Mede fim da fala → ela começar a falar."""
import asyncio, fractions, json, subprocess, sys, time, uuid
import aiohttp, av, numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from openwakeword.vad import VAD

ENDERECO, CHUNK = os.environ["FRIDAY_URL"], 2560


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


async def conectar(pc, mic, eventos, t0, aplay):
    pc.addTrack(mic)
    canal = pc.createDataChannel("rtvi")
    @canal.on("open")
    def _a():
        canal.send(json.dumps({"label": "rtvi-ai", "type": "client-ready", "id": str(uuid.uuid4()),
                               "data": {"version": "2.1.0", "about": {"library": "medicao"}}}))
        async def pingar():
            while True:
                canal.send(f"ping: {time.time()}"); await asyncio.sleep(1)
        asyncio.ensure_future(pingar())
    @canal.on("message")
    def _m(msg):
        if isinstance(msg, str) and msg.startswith("{"):
            d = json.loads(msg)
            if d.get("type") in ("bot-started-speaking", "bot-stopped-speaking"):
                eventos.append((round(time.monotonic() - t0, 2), d["type"]))
    @pc.on("track")
    def _t(tr):
        async def tocar():
            try:
                while True:
                    aplay.stdin.write((await tr.recv()).to_ndarray().tobytes())
            except Exception:
                pass
        asyncio.ensure_future(tocar())
    of = await pc.createOffer(); await pc.setLocalDescription(of)
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{ENDERECO}/api/offer", ssl=False, json={
                "sdp": pc.localDescription.sdp, "type": pc.localDescription.type,
                "request_data": {"origem": "satelite", "satelite_id": "medicao"}}) as r:
            resp = await r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=resp["sdp"], type=resp["type"]))
    while pc.connectionState != "connected":
        await asyncio.sleep(0.02)


async def main(modo):
    vad, pc, mic, eventos = VAD(), RTCPeerConnection(), Mic(), []
    aplay = subprocess.Popen(["aplay", "-q", "-D", "default", "-t", "raw", "-f", "S16_LE", "-r", "48000", "-c", "2",
                              "--buffer-time=100000"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    t0 = time.monotonic()
    rec = await asyncio.create_subprocess_exec("arecord", "-q", "-D", "default", "-f", "S16_LE", "-r", "16000", "-c",
                                               "1", "-t", "raw", stdout=asyncio.subprocess.PIPE)
    import wave
    with wave.open("/home/jose/satelite/sons/bipe.wav") as w:
        som_bipe = w.readframes(w.getnframes())
    def bipe():  # pelo MESMO aplay: um segundo aplay no Jabra dá "Device or resource busy"
        aplay.stdin.write(som_bipe); aplay.stdin.flush()
    conexao = None
    def por(b):  # quadros de 20 ms (um pacote Opus cada)
        for i in range(0, len(b), 640):
            mic.fila.put_nowait(b[i:i + 640])
    if modo == "normal":
        await conectar(pc, mic, eventos, t0, aplay); await asyncio.sleep(2)
    bipe(); t_bipe = time.monotonic() - t0
    falou, fim_fala, silencio, cortado, enviando = False, None, 0, False, True
    fim = time.monotonic() + 20
    while time.monotonic() < fim:
        pcm = await rec.stdout.readexactly(CHUNK)
        agora = time.monotonic() - t0
        if modo == "rajada" and conexao is None and agora - t_bipe >= 0.8:
            conexao = asyncio.ensure_future(conectar(pc, mic, eventos, t0, aplay))
        p = vad.predict(np.frombuffer(pcm, dtype=np.int16), frame_size=640)
        if p >= 0.5:
            falou, silencio = True, 0
        elif falou and not cortado:
            silencio += 1
            if silencio >= 10:  # 0,8 s de silêncio
                fim_fala = agora - 0.8
                for _ in range(19):
                    por(bytes(CHUNK))  # 1,5 s de zeros
                cortado, enviando = True, False
        if enviando:
            por(pcm)
    rec.terminate(); await pc.close(); aplay.terminate()
    inicio_dela = next((t for t, e in eventos if e == "bot-started-speaking" and fim_fala and t > fim_fala), None)
    print(json.dumps({"modo": modo, "fim_da_fala_s": fim_fala and round(fim_fala, 2),
                      "ela_comecou_s": inicio_dela,
                      "latencia_s": round(inicio_dela - fim_fala, 2) if inicio_dela and fim_fala else None,
                      "eventos": eventos}, ensure_ascii=False))

asyncio.run(main(sys.argv[1]))
