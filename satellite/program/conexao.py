"""Cliente WebRTC (aiortc) do bot.py, igual à página do navegador, avisando que é o satélite (spec D6, §2)."""
import asyncio, fractions, json, time, uuid
import aiohttp, av, numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription

PEDACO_80MS = 2560
QUADRO_20MS = 640  # 320 amostras S16 a 16 kHz
MOTIVOS_ENCERRAR = ("agradecimento",)  # spec 2026-10-15 D6: só motivos conhecidos entram no registro


def traduzir_rtvi(msg: dict):
    tipo = msg.get("type")
    if tipo == "bot-started-speaking":
        return ("ela_comecou", None)
    if tipo == "bot-stopped-speaking":
        return ("ela_parou", None)
    if tipo == "server-message":
        d = msg.get("data") or {}
        if d.get("evento") == "cerebro_pendente":
            return ("cerebro_pendente", int(d.get("pendentes", 0)))
        if d.get("evento") == "cerebro_entregando":
            return ("cerebro_entregando", None)
        if d.get("evento") == "encerrar":
            m = d.get("motivo")
            return ("encerrar", m if m in MOTIVOS_ENCERRAR else None)
        if d.get("evento") == "ferramenta":  # D2: ferramentas da voz em andamento
            return ("ferramenta", int(d.get("em_andamento") or 0))
        if d.get("evento") == "nao_perturbe":
            return ("nao_perturbe", bool(d.get("ligado")))
        if d.get("evento") == "comando":  # spec 2026-10-12 §4: volume e timers, respondidos pelo mesmo id
            return ("comando", d)
    return None


class _Microfone(MediaStreamTrack):
    """O aiortc pede quadros com recv(); devolvemos o que estiver na fila. A pré-gravação sai em rajada
    (mais rápido que o tempo real) e o resto acompanha a captura."""
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.fila = asyncio.Queue()
        self._pts = 0

    async def recv(self):
        pcm = await self.fila.get()
        a = np.frombuffer(pcm, dtype=np.int16).reshape(1, -1)
        q = av.AudioFrame.from_ndarray(a, format="s16", layout="mono")
        q.sample_rate, q.pts, q.time_base = 16000, self._pts, fractions.Fraction(1, 16000)
        self._pts += a.shape[1]
        return q


class ConexaoFriday:
    def __init__(self, endereco, request_data, ao_evento, ao_audio):
        self.endereco, self.request_data = endereco, request_data
        self.ao_evento, self.ao_audio = ao_evento, ao_audio
        self.mic = _Microfone()
        self.pc = None
        self._canal = None  # canal de dados do RTVI: respostas aos comandos (spec 2026-10-12 §4)
        self._fila_msgs = []  # client-messages pedidas antes de o canal abrir (revisão final 20/10)
        self._tarefas, self._fechando = [], False
        self._silenciado = False  # `silenciar()`: conexão em descarte (botão vermelho), nada dela chega mais ao programa
        # Revisão final I2 (08/10): o bot manda áudio sem parar (silêncio inclusive); se parar por este tempo
        # com a conexão de pé, ele morreu (OOM/reinício) e o ICE só desistiria em ~30 s.
        self.silencio_max_s = 5.0  # medido 08/10: maior intervalo 0,22 s com ela calada

    def enviar(self, pcm16k: bytes):
        # Quadros de 20 ms = um pacote Opus cada. Com 80 ms o aiortc gera vários pacotes com o mesmo
        # carimbo de tempo e o receptor do bot descarta tudo (medido em 06/10: "No audio frame received").
        for i in range(0, len(pcm16k), QUADRO_20MS):
            self.mic.fila.put_nowait(pcm16k[i:i + QUADRO_20MS])

    def enviar_silencio(self, segundos=1.5):
        for _ in range(int(round(segundos / 0.08))):
            self.enviar(bytes(PEDACO_80MS))

    async def abrir(self):
        # Revisão final I1: depois de fechar() esta conexão foi descartada e não avisa mais nada (um aviso
        # atrasado derrubaria a conversa seguinte).
        if self._fechando:
            return
        try:
            await asyncio.wait_for(self._abrir(), timeout=8)
            if not self._fechando:
                self.ao_evento("conectou", None)
        except Exception:
            if self._fechando:
                return
            await self.fechar()
            self.ao_evento("falha_conexao", None)

    async def _abrir(self):
        self.pc = RTCPeerConnection()
        self.pc.addTrack(self.mic)
        canal = self.pc.createDataChannel("rtvi")
        self._canal = canal

        @canal.on("open")
        def _aberto():
            canal.send(json.dumps({"label": "rtvi-ai", "type": "client-ready", "id": str(uuid.uuid4()),
                                   "data": {"version": "2.1.0", "about": {"library": "friday-satelite"}}}))
            self._despejar_fila()
            self._tarefas.append(asyncio.ensure_future(self._pingar(canal)))

        canal.on("message", self._ao_mensagem)

        @self.pc.on("track")
        def _trilha(t):
            if t.kind == "audio":
                self._tarefas.append(asyncio.ensure_future(self._tocar(t)))

        @self.pc.on("connectionstatechange")
        async def _estado():
            if self.pc and self.pc.connectionState in ("failed", "closed") and not self._fechando and not self._silenciado:
                self.ao_evento("caiu", None)

        oferta = await self.pc.createOffer()
        await self.pc.setLocalDescription(oferta)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.endereco}/api/offer", ssl=False, timeout=aiohttp.ClientTimeout(total=6),
                              json={"sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type,
                                    "request_data": self.request_data}) as r:
                r.raise_for_status()
                resp = await r.json()
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=resp["sdp"], type=resp["type"]))
        while self.pc.connectionState != "connected":
            if self.pc.connectionState in ("failed", "closed"):
                raise ConnectionError(self.pc.connectionState)
            await asyncio.sleep(0.02)

    def enviar_mensagem(self, tipo: str, dados) -> bool:
        """Mensagem do cliente pelo RTVI (o bot recebe em on_client_message). False se o canal não está aberto."""
        if self._fechando or self._canal is None:
            return False
        msg = json.dumps({"label": "rtvi-ai", "type": "client-message", "id": str(uuid.uuid4()),
                          "data": {"t": tipo, "d": dados}})
        if getattr(self._canal, "readyState", None) != "open":
            # revisão final (20/10): o `conectou` chega antes de o canal abrir; a mensagem sai, em ordem, ao abrir
            self._fila_msgs.append(msg)
            return True
        self._canal.send(msg)
        return True

    def _despejar_fila(self):
        """Manda, em ordem, as mensagens guardadas antes de o canal abrir (chamado no `open`, depois do client-ready)."""
        fila, self._fila_msgs = self._fila_msgs, []
        for msg in fila:
            self._canal.send(msg)

    def silenciar(self):
        """Botão vermelho (spec 2026-11-04): a partir daqui o áudio e os eventos que o bot ainda mandar antes de a
        conexão fechar (~0,3 s) são descartados; senão a fala dela voltaria a tocar e eventos velhos chegariam à
        conversa seguinte."""
        self._silenciado = True

    def _ao_mensagem(self, m):
        if self._fechando or self._silenciado:
            return
        if isinstance(m, str) and m.startswith("{"):
            ev = traduzir_rtvi(json.loads(m))
            if ev:
                self.ao_evento(*ev)

    async def _pingar(self, canal):
        while not self._fechando:
            canal.send(f"ping: {time.time()}")  # o SmallWebRTC usa o ping como sinal de vida
            await asyncio.sleep(1)

    async def _tocar(self, trilha):
        try:
            while not self._fechando:
                q = await asyncio.wait_for(trilha.recv(), self.silencio_max_s)
                if not self._silenciado:
                    self.ao_audio(q.to_ndarray().tobytes())  # aiortc entrega s16 48 kHz estéreo intercalado
        except asyncio.TimeoutError:
            if not self._fechando and not self._silenciado:
                self.ao_evento("caiu", None)
        except Exception:
            pass

    async def fechar(self):
        self._fechando = True
        for t in self._tarefas:
            t.cancel()
        if self.pc:
            await self.pc.close()
