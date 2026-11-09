"""cd ~/satelite && ~/oww/bin/python -m unittest test_conexao -v   (na Pi: precisa de aiortc/aiohttp)"""
import asyncio, json, os, tempfile, unittest
import importlib.util
if importlib.util.find_spec("aiohttp") is None or importlib.util.find_spec("av") is None:  # Pi venv only (aiortc stack)
    raise unittest.SkipTest("needs aiohttp and av")
from aiohttp.test_utils import TestClient, TestServer
import configuracao as c
import porta_local
from conexao import traduzir_rtvi


class TestRtvi(unittest.TestCase):
    def test_mapa(self):
        self.assertEqual(traduzir_rtvi({"label": "rtvi-ai", "type": "bot-started-speaking"}), ("ela_comecou", None))
        self.assertEqual(traduzir_rtvi({"label": "rtvi-ai", "type": "bot-stopped-speaking"}), ("ela_parou", None))
        self.assertEqual(traduzir_rtvi({"label": "rtvi-ai", "type": "server-message",
                                        "data": {"evento": "cerebro_pendente", "pendentes": 2}}), ("cerebro_pendente", 2))
        self.assertEqual(traduzir_rtvi({"label": "rtvi-ai", "type": "server-message",
                                        "data": {"evento": "cerebro_entregando"}}), ("cerebro_entregando", None))
        self.assertEqual(traduzir_rtvi({"label": "rtvi-ai", "type": "server-message",
                                        "data": {"evento": "encerrar"}}), ("encerrar", None))
        self.assertEqual(traduzir_rtvi({"label": "rtvi-ai", "type": "server-message",
                                        "data": {"evento": "nao_perturbe", "ligado": True}}), ("nao_perturbe", True))
        self.assertIsNone(traduzir_rtvi({"label": "rtvi-ai", "type": "bot-llm-text"}))

    def test_ferramenta_e_encerrar_com_motivo(self):
        msg = lambda d: {"label": "rtvi-ai", "type": "server-message", "data": d}
        self.assertEqual(traduzir_rtvi(msg({"evento": "ferramenta", "em_andamento": 2})), ("ferramenta", 2))
        self.assertEqual(traduzir_rtvi(msg({"evento": "encerrar", "motivo": "agradecimento"})),
                         ("encerrar", "agradecimento"))
        self.assertEqual(traduzir_rtvi(msg({"evento": "encerrar", "motivo": "qualquer"})), ("encerrar", None))

    def test_comando_vira_evento_com_os_dados(self):
        # spec 2026-10-12 §4: volume e timers chegam como "comando" com id; a Pi responde com o mesmo id
        d = {"evento": "comando", "id": "c1", "acao": "volume", "numero": 4}
        self.assertEqual(traduzir_rtvi({"label": "rtvi-ai", "type": "server-message", "data": d}), ("comando", d))


class TestMensagemDoCliente(unittest.IsolatedAsyncioTestCase):
    async def test_enviar_mensagem_usa_client_message_do_rtvi(self):
        from conexao import ConexaoFriday
        c = ConexaoFriday("https://x", {}, lambda *a: None, lambda b: None)

        class Canal:
            readyState = "open"
            def __init__(self): self.enviados = []
            def send(self, m): self.enviados.append(json.loads(m))
        c._canal = Canal()
        self.assertTrue(c.enviar_mensagem("resposta", {"id": "c1", "ok": True}))
        m = c._canal.enviados[0]
        self.assertEqual((m["label"], m["type"], m["data"]), ("rtvi-ai", "client-message",
                                                              {"t": "resposta", "d": {"id": "c1", "ok": True}}))

    async def test_silenciar_descarta_audio_que_chega(self):
        # Review Focus 3 (spec 2026-11-04): pacotes que o bot ainda manda antes de a conexão fechar não podem tocar
        from conexao import ConexaoFriday
        tocou = []
        c = ConexaoFriday("https://x", {}, lambda *a: None, tocou.append)

        class Quadro:
            def to_ndarray(self):
                class A:
                    def tobytes(self_): return b"voz"
                return A()

        class Trilha:
            def __init__(self): self.n = 0
            async def recv(self_):
                self_.n += 1
                if self_.n == 3:
                    c.silenciar()
                if self_.n >= 5:
                    c._fechando = True
                return Quadro()
        await c._tocar(Trilha())
        self.assertEqual(tocou, [b"voz", b"voz"])  # os dois primeiros; do terceiro em diante, nada

    async def test_silenciar_tambem_cala_os_eventos_da_conexao_descartada(self):
        from conexao import ConexaoFriday
        eventos = []
        c = ConexaoFriday("https://x", {}, lambda *a: eventos.append(a), lambda b: None)
        c.silenciar()
        c._ao_mensagem(json.dumps({"label": "rtvi-ai", "type": "bot-stopped-speaking"}))
        self.assertEqual(eventos, [])

    async def test_cancelar_sem_canal_nao_levanta(self):
        # Review Focus 2: o vermelho com a conexão já fechando ou sem canal não pode levantar exceção
        from conexao import ConexaoFriday
        c = ConexaoFriday("https://x", {}, lambda *a: None, lambda b: None)
        self.assertFalse(c.enviar_mensagem("cancelar", {}))      # canal ainda None
        c._fechando = True
        self.assertFalse(c.enviar_mensagem("cancelar", {}))      # já fechando

    async def test_mensagem_antes_do_canal_abrir_vai_depois_em_ordem(self):
        """Revisão final (20/10): o `conectou` chega antes de o canal de dados abrir; o speech_started do modo
        sussurro não pode se perder (com o VAD do servidor desligado, o Live nunca abriria o turno)."""
        from conexao import ConexaoFriday
        c = ConexaoFriday("https://x", {}, lambda *a: None, lambda b: None)

        class Canal:
            readyState = "connecting"
            def __init__(self): self.enviados = []
            def send(self, m): self.enviados.append(json.loads(m))
        c._canal = Canal()
        self.assertTrue(c.enviar_mensagem("speech_started", {}))
        self.assertTrue(c.enviar_mensagem("speech_stopped", {}))
        self.assertEqual(c._canal.enviados, [])
        c._canal.readyState = "open"
        c._despejar_fila()
        self.assertEqual([m["data"]["t"] for m in c._canal.enviados], ["speech_started", "speech_stopped"])
        c._despejar_fila()
        self.assertEqual(len(c._canal.enviados), 2)  # não repete

    async def test_fechando_nao_enfileira(self):
        from conexao import ConexaoFriday
        c = ConexaoFriday("https://x", {}, lambda *a: None, lambda b: None)

        class Canal:
            readyState = "connecting"
            def send(self, m): raise AssertionError("não deveria enviar")
        c._canal, c._fechando = Canal(), True
        self.assertFalse(c.enviar_mensagem("speech_started", {}))

    async def test_sem_canal_aberto_nao_quebra(self):
        from conexao import ConexaoFriday
        c = ConexaoFriday("https://x", {}, lambda *a: None, lambda b: None)
        self.assertFalse(c.enviar_mensagem("resposta", {}))


class TestQuadros(unittest.IsolatedAsyncioTestCase):
    async def test_enviar_parte_em_quadros_de_20ms(self):
        # 06/10: pedaços de 80 ms viravam vários pacotes Opus com o mesmo carimbo de tempo e o
        # receptor (aiortc no bot) descartava tudo ("No audio frame received"). Quadros de 20 ms = 1 pacote.
        from conexao import ConexaoFriday
        c = ConexaoFriday("https://x", {}, lambda *a: None, lambda b: None)
        c.enviar(bytes(2560)); c.enviar_silencio(0.08)
        tamanhos = [c.mic.fila.get_nowait() for _ in range(c.mic.fila.qsize())]
        self.assertEqual([len(t) for t in tamanhos], [640] * 8)


class TestConexaoVelha(unittest.IsolatedAsyncioTestCase):
    """Revisão final I1/I2 (08/10)."""
    def nova(self):
        from conexao import ConexaoFriday
        self.ev = []
        return ConexaoFriday("https://127.0.0.1:9", {}, lambda n, v: self.ev.append(n), lambda b: None)

    async def test_fechada_nao_emite_mais_nada(self):
        c = self.nova()
        await c.fechar()
        await c.abrir()  # porta fechada: falharia, mas a conexão já foi descartada
        c._ao_mensagem('{"label": "rtvi-ai", "type": "bot-started-speaking"}')
        self.assertEqual(self.ev, [])

    async def test_audio_que_para_de_chegar_e_queda(self):
        c = self.nova()
        c.silencio_max_s = 0.2

        class Trilha:
            def __init__(self): self.n = 0
            async def recv(self):
                self.n += 1
                if self.n > 1:
                    await asyncio.sleep(10)  # o bot morreu: nada mais chega
                class Q:
                    def to_ndarray(self):
                        import numpy as np; return np.zeros((1, 1920), dtype=np.int16)
                return Q()
        await asyncio.wait_for(c._tocar(Trilha()), 2)
        self.assertEqual(self.ev, ["caiu"])


class TestPorta(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.d = tempfile.mkdtemp(); self.p = os.path.join(self.d, "config.json")
        self.cfg = c.carregar(self.p); self.mudancas = []
        self.campainhas = []

        def campainha(eid):
            self.campainhas.append(eid)
            return "vou_abrir"
        app = porta_local.criar_app(self.p, lambda: {"estado": "parada"}, self.mudancas.append, campainha)
        self.cli = TestClient(TestServer(app)); await self.cli.start_server()
        self.h = {"Authorization": f"Bearer {self.cfg['senha_porta_local']}"}

    async def asyncTearDown(self):
        await self.cli.close()

    async def test_sem_senha_401(self):
        self.assertEqual((await self.cli.get("/status")).status, 401)

    async def test_estado_e_config_sem_senha_exposta(self):
        self.assertEqual(await (await self.cli.get("/status", headers=self.h)).json(), {"estado": "parada"})
        cfg = await (await self.cli.get("/config", headers=self.h)).json()
        self.assertNotIn("senha_porta_local", cfg)

    async def test_put_valido_grava_e_avisa(self):
        r = await self.cli.put("/config", headers=self.h, json={"continuacao_s": 10})
        self.assertEqual(r.status, 200)
        self.assertEqual(json.load(open(self.p))["continuacao_s"], 10)
        self.assertEqual(self.mudancas[-1]["continuacao_s"], 10)

    async def test_put_invalido_400_sem_alterar(self):
        antes = open(self.p).read()
        r = await self.cli.put("/config", headers=self.h, json={"limiar_palavra": 3})
        self.assertEqual(r.status, 400); self.assertEqual(open(self.p).read(), antes)

    async def test_put_corpo_nao_objeto_400_sem_alterar(self):
        # M4 (revisão 08/10): JSON válido mas não-objeto (lista) dava 500, não 400.
        antes = open(self.p).read()
        r = await self.cli.put("/config", headers=self.h, json=[1, 2, 3])
        self.assertEqual(r.status, 400); self.assertEqual(open(self.p).read(), antes)


    async def test_campainha_com_senha_responde_a_decisao(self):
        # spec 2026-10-12 §3: só o id da mensagem; o texto nunca passa pela porta
        r = await self.cli.post("/doorbell", headers=self.h, json={"mensagem": "abcd1234ef"})
        self.assertEqual((r.status, await r.json()), (200, {"resposta": "vou_abrir"}))
        self.assertEqual(self.campainhas, ["abcd1234ef"])

    async def test_campainha_sem_senha_401(self):
        r = await self.cli.post("/doorbell", json={"mensagem": "abcd1234ef"})
        self.assertEqual(r.status, 401)
        self.assertEqual(self.campainhas, [])

    async def test_campainha_com_id_invalido_400(self):
        for corpo in ({"mensagem": "../etc"}, {"mensagem": "a"}, {}, [1]):
            r = await self.cli.post("/doorbell", headers=self.h, json=corpo)
            self.assertEqual(r.status, 400, corpo)
        self.assertEqual(self.campainhas, [])

    async def test_rotas_antigas_somem(self):
        # spec 2026-10-18 §4.11: rotas em inglês; as antigas devolvem 404 mesmo com a senha certa
        self.assertEqual((await self.cli.get("/estado", headers=self.h)).status, 404)
        r = await self.cli.post("/campainha", headers=self.h, json={"mensagem": "abcd1234ef"})
        self.assertEqual(r.status, 404)
        self.assertEqual(self.campainhas, [])


if __name__ == "__main__":
    unittest.main()
