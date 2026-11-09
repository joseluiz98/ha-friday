"""cd ~/satelite && ~/oww/bin/python -m unittest test_porta_local_wakewords -v   (na Pi: precisa de aiohttp)
spec 2026-10-24 (wakeword-retrain): GET /wakewords e PUT /wakewords/{nome}."""
import json, os, tempfile, unittest
import importlib.util
if importlib.util.find_spec("aiohttp") is None:  # the Pi venv and CI have it; a bare python does not
    raise unittest.SkipTest("needs aiohttp")
from aiohttp.test_utils import TestClient, TestServer
import configuracao as c
import porta_local

EXTRA = {"nome": "sexta_feira", "arquivo": "/home/jose/modelos/sexta_feira.onnx", "limiar": 0.5, "modo": "sombra"}


class Base(unittest.IsolatedAsyncioTestCase):
    carregadas = {"friday", "sexta_feira"}

    async def asyncSetUp(self):
        self.d = tempfile.mkdtemp(); self.p = os.path.join(self.d, "config.json")
        cfg = c.carregar(self.p)
        cfg["palavras_extras"] = [dict(EXTRA)]
        c.gravar(self.p, cfg)
        self.mudancas = []
        app = porta_local.criar_app(self.p, lambda: {"estado": "parada"}, self.mudancas.append,
                                    palavras_fn=lambda: self.carregadas)
        self.cli = TestClient(TestServer(app)); await self.cli.start_server()
        self.h = {"Authorization": f"Bearer {c.carregar(self.p)['senha_porta_local']}"}

    async def asyncTearDown(self):
        await self.cli.close()


class TestWakewords(Base):
    async def test_get_sem_senha_401(self):
        self.assertEqual((await self.cli.get("/wakewords")).status, 401)

    async def test_put_sem_senha_401(self):
        r = await self.cli.put("/wakewords/friday", json={"modo": "sombra"})
        self.assertEqual(r.status, 401)

    async def test_get_lista_duas_palavras_sem_caminho(self):
        r = await self.cli.get("/wakewords", headers=self.h)
        self.assertEqual(r.status, 200)
        corpo = await r.json()
        self.assertEqual(corpo, [
            {"nome": "friday", "modo": "ativa", "limiar": c.carregar(self.p)["limiar_palavra"], "principal": True,
             "treinada": True},
            {"nome": "sexta_feira", "modo": "sombra", "limiar": 0.5, "principal": False, "treinada": True}])
        self.assertNotIn("onnx", json.dumps(corpo))

    async def test_put_ativa_grava_e_avisa_uma_vez(self):
        r = await self.cli.put("/wakewords/sexta_feira", headers=self.h, json={"modo": "ativa"})
        self.assertEqual(r.status, 200)
        self.assertEqual(json.load(open(self.p))["palavras_extras"][0]["modo"], "ativa")
        self.assertEqual(len(self.mudancas), 1)
        self.assertEqual([p["modo"] for p in await r.json()], ["ativa", "ativa"])

    async def test_put_inexistente_404(self):
        antes = open(self.p).read()
        r = await self.cli.put("/wakewords/nao_existe", headers=self.h, json={"modo": "ativa"})
        self.assertEqual(r.status, 404)
        self.assertEqual(open(self.p).read(), antes); self.assertEqual(self.mudancas, [])

    async def test_put_modo_invalido_400(self):
        antes = open(self.p).read()
        for corpo in ({"modo": "x"}, {}, [1], {"modo": None}):
            r = await self.cli.put("/wakewords/sexta_feira", headers=self.h, json=corpo)
            self.assertEqual(r.status, 400, corpo)
        self.assertEqual(open(self.p).read(), antes); self.assertEqual(self.mudancas, [])

    async def test_put_friday_desligada(self):
        r = await self.cli.put("/wakewords/friday", headers=self.h, json={"modo": "desligada"})
        self.assertEqual(r.status, 200)
        self.assertEqual(json.load(open(self.p))["modo_friday"], "desligada")
        self.assertEqual(len(self.mudancas), 1)


class TestNaoCarregada(Base):
    carregadas = {"friday"}

    async def test_ligar_sem_modelo_carregado_409_e_nada_muda(self):
        """Foco 2: o .onnx não carregou -> recusa e o arquivo não muda."""
        antes = open(self.p).read()
        for modo in ("ativa", "sombra"):
            r = await self.cli.put("/wakewords/sexta_feira", headers=self.h, json={"modo": modo})
            self.assertEqual(r.status, 409)
        self.assertEqual(open(self.p).read(), antes); self.assertEqual(self.mudancas, [])

    async def test_desligar_sempre_pode(self):
        r = await self.cli.put("/wakewords/sexta_feira", headers=self.h, json={"modo": "desligada"})
        self.assertEqual(r.status, 200)
        self.assertEqual(json.load(open(self.p))["palavras_extras"][0]["modo"], "desligada")
        lista = await r.json()
        self.assertFalse([p for p in lista if p["nome"] == "sexta_feira"][0]["treinada"])


class TestSemPalavrasFn(unittest.IsolatedAsyncioTestCase):
    async def test_rotas_nao_registradas(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, "config.json"); cfg = c.carregar(p)
        cli = TestClient(TestServer(porta_local.criar_app(p, lambda: {}, lambda n: None)))
        await cli.start_server()
        try:
            r = await cli.get("/wakewords", headers={"Authorization": f"Bearer {cfg['senha_porta_local']}"})
            self.assertEqual(r.status, 404)
        finally:
            await cli.close()


if __name__ == "__main__":
    unittest.main()
