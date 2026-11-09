import os
"""DESCARTÁVEL: depois da fala, o que faz o Gemini fechar o turno? Uso: diag_corte.py continuo|zeros|corte
Fala = 1ª tentativa gravada de "Sexta-feira" (05/10). Resultado no log do add-on ("[Transcription:user]")."""
import asyncio, fractions, sys, time, wave
import aiohttp, av, numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription

GRAV = "/home/jose/gravacoes/tentativas_sexta_feira_20261005_184810.wav"
Q = 640  # 20 ms


class Mic(MediaStreamTrack):
    kind = "audio"
    def __init__(self):
        super().__init__(); self.fila = asyncio.Queue(); self.pts = 0
    async def recv(self):
        await asyncio.sleep(0.02)
        pcm = await self.fila.get()
        a = np.frombuffer(pcm, dtype=np.int16).reshape(1, -1)
        q = av.AudioFrame.from_ndarray(a, format="s16", layout="mono")
        q.sample_rate = 16000; q.pts = self.pts; q.time_base = fractions.Fraction(1, 16000)
        self.pts += a.shape[1]
        return q


async def main(modo):
    with wave.open(GRAV) as w:
        w.setpos(int(16000 * float(sys.argv[2] if len(sys.argv) > 2 else 1.5)))
        fala = w.readframes(int(16000 * 3.0))  # a 1ª tentativa fica entre 1,5 e 4,5 s
    fala = fala[: len(fala) // Q * Q]
    pc, mic = RTCPeerConnection(), Mic()
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
    for i in range(0, len(fala), Q):
        mic.fila.put_nowait(fala[i:i + Q])
    if modo == "continuo":
        ruido = (np.random.randn(16000 * 6) * 40).astype(np.int16).tobytes()
        for i in range(0, len(ruido), Q):
            mic.fila.put_nowait(ruido[i:i + Q])
    elif modo == "zeros":
        for _ in range(300):
            mic.fila.put_nowait(bytes(Q))
    else:  # corte
        for _ in range(75):
            mic.fila.put_nowait(bytes(Q))
    while mic.fila.qsize():
        await asyncio.sleep(0.1)
    await asyncio.sleep(8)
    print(modo, "fim")
    await pc.close()

asyncio.run(main(sys.argv[1]))
