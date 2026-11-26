"""Two-beam listening D7 (shadow): scores 1.5 s windows of the caller's turn audio on the server agent's localhost
route and records the verdicts. It never acts: no frame is sent, nothing is cut."""
import asyncio

from loguru import logger

URL = "http://127.0.0.1:8801/v1/speaker_gate/score"
RATE = 16000
JANELA_BYTES = int(1.5 * RATE) * 2
PRAZO_S = 1.0


async def post_http(body: dict) -> dict:
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=PRAZO_S)) as s:
        async with s.post(URL, json=body) as r:
            r.raise_for_status()
            return await r.json()


class SombraPortao:
    _avisou = False  # once per process

    def __init__(self, sat_id, post=post_http, limiar=0.44):
        self.sat_id, self._post, self.limiar = sat_id, post, limiar
        self.caller = None
        self._primeiro = True
        self._ativo = False
        self._reset()

    def _reset(self):
        self._buf = bytearray()
        self._tarefas = []
        self._janelas = []  # (indice, scores dict) in window order
        self._erros = 0
        self._falhas_seguidas = getattr(self, "_falhas_seguidas", 0)

    def comeco_do_turno(self, tv_active: bool = False):
        """Pi `speech_started`: the first turn is always scored, later ones only with the TV active."""
        self._reset()
        self._ativo = self._primeiro or bool(tv_active)

    def audio(self, pcm: bytes):
        if not self._ativo:
            return
        self._buf.extend(pcm)
        while len(self._buf) >= JANELA_BYTES:
            janela, self._buf = bytes(self._buf[:JANELA_BYTES]), self._buf[JANELA_BYTES:]
            i = len(self._tarefas)
            self._tarefas.append(asyncio.get_running_loop().create_task(self._pontuar(i, janela)))

    def _avisar(self):
        if not SombraPortao._avisou:
            SombraPortao._avisou = True
            logger.warning(f"two-beam: speaker gate unavailable (sat={self.sat_id})")

    async def _pontuar(self, i, janela):
        import base64
        try:
            r = await asyncio.wait_for(self._post({"sat_id": self.sat_id,
                                                   "pcm16_b64": base64.b64encode(janela).decode()}), PRAZO_S)
            if not isinstance(r, dict):
                raise ValueError("bad response")
        except Exception:  # noqa: BLE001 - shadow: a failure is counted, never raised
            self._erros += 1
            self._falhas_seguidas += 1
            if self._falhas_seguidas >= 3:
                self._avisar()
            return
        self._falhas_seguidas = 0
        if not r.get("available", False):
            self._avisar()
            return
        self._janelas.append((i, dict(r.get("scores") or {})))

    async def fim_do_turno(self):
        """Waits (briefly) for the in-flight scores and returns the turn's record, or None if the turn was not scored."""
        if not self._ativo:
            return None
        if self._tarefas:
            await asyncio.wait(self._tarefas, timeout=PRAZO_S + 0.2)
        janelas = [s for _, s in sorted(self._janelas, key=lambda x: x[0])]
        melhor = {}
        for s in janelas:
            for k, v in s.items():
                melhor[k] = max(v, melhor.get(k, v))
        best = max(melhor, key=melhor.get) if melhor else None
        if self._primeiro and best is not None and melhor[best] >= self.limiar:
            self.caller = best
        if self._primeiro and self._tarefas:
            self._primeiro = False
        would_end = False
        if self.caller:
            seguidas = 0
            for s in janelas:
                seguidas = seguidas + 1 if s.get(self.caller, 0.0) < self.limiar else 0
                if seguidas >= 2:
                    would_end = True
        rec = {"windows": [s.get(self.caller) if self.caller else (max(s.values()) if s else None) for s in janelas],
               "best": best, "callerMatch": self.caller, "wouldEnd": would_end, "errors": self._erros}
        self._ativo = False
        return rec
