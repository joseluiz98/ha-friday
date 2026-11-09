import os
"""DESCARTÁVEL: o bot recebe o áudio da Pi? Manda um trecho de gravação real em dois ritmos.
Uso: diag_ritmo.py compassado|rajada   (o resultado está no log do add-on: "[Transcription:user]")"""
import asyncio, fractions, sys, time, wave
import aiohttp, av, numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription

GRAV = "/home/jose/gravacoes/ao_vivo_20261005_183841.wav"


class Mic(MediaStreamTrack):
    kind = "audio"
    def __init__(self, compassado):
        super().__init__(); self.fila = asyncio.Queue(); self.pts = 0; self.compassado = compassado
    async def recv(self):
        if self.compassado:
            await asyncio.sleep(0.02)
        pcm = await self.fila.get()
        a = np.frombuffer(pcm, dtype=np.int16).reshape(1, -1)
        q = av.AudioFrame.from_ndarray(a, format="s16", layout="mono")
        q.sample_rate = 16000; q.pts = self.pts; q.time_base = fractions.Fraction(1, 16000)
        self.pts += a.shape[1]
        return q


async def main(modo):
    with wave.open(GRAV) as w:
        w.setpos(16000 * 270)
        trecho = w.readframes(16000 * 15)
    pc, mic = RTCPeerConnection(), Mic(modo == "compassado")
    pc.addTrack(mic); pc.createDataChannel("rtvi")
    of = await pc.createOffer(); await pc.setLocalDescription(of)
    async with aiohttp.ClientSession() as s:
        async with s.post(os.environ["FRIDAY_URL"] + "/api/offer", ssl=False, json={
                "sdp": pc.localDescription.sdp, "type": "offer",
                "request_data": {"origem": "satelite", "satelite_id": "medicao"}}) as r:
            resp = await r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=resp["sdp"], type=resp["type"]))
    while pc.connectionState != "connected":
        await asyncio.sleep(0.02)
    t0 = time.monotonic()
    if modo == "compassado":
        for i in range(0, len(trecho), 640):
            mic.fila.put_nowait(trecho[i:i + 640])
    else:  # como a captura: a cada 80 ms chegam 4 quadros de uma vez
        for i in range(0, len(trecho), 2560):
            for j in range(i, i + 2560, 640):
                mic.fila.put_nowait(trecho[j:j + 640])
            await asyncio.sleep(0.08)
    while mic.fila.qsize():
        await asyncio.sleep(0.1)
    await asyncio.sleep(5)
    print(modo, "enviado em", round(time.monotonic() - t0, 1), "s")
    await pc.close()

asyncio.run(main(sys.argv[1]))
