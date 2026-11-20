"""cd ~/satelite && ~/oww/bin/python -m unittest test_porta_local_anuncio -v   (na Pi: precisa de aiohttp)
Spec 2026-11-20 (announcements on the satellites): POST /announce grava <dir>/<id>.wav e pergunta ao maestro."""
import os, shutil, tempfile, unittest
import importlib.util
if importlib.util.find_spec("aiohttp") is None:  # the Pi venv and CI have it; a bare python does not
    raise unittest.SkipTest("needs aiohttp")
from aiohttp.test_utils import TestClient, TestServer
import configuracao as c
import porta_local

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 40


class Base(unittest.IsolatedAsyncioTestCase):
    com_fn = True

    async def asyncSetUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.p = os.path.join(self.d, "config.json")
        c.carregar(self.p)
        self.pasta = os.path.join(self.d, "anuncios")
        self.pedidos = []

        def fn(aid):
            self.pedidos.append(aid)
            return "vou_tocar"
        app = porta_local.criar_app(self.p, lambda: {"estado": "parada"}, lambda n: None,
                                    anuncio_fn=fn if self.com_fn else None, anuncio_dir=self.pasta)
        self.cli = TestClient(TestServer(app)); await self.cli.start_server()
        self.h = {"Authorization": f"Bearer {c.carregar(self.p)['senha_porta_local']}"}

    async def asyncTearDown(self):
        await self.cli.close()


class TestAnnounce(Base):
    async def test_sem_senha_401(self):
        r = await self.cli.post("/announce", data=WAV, headers={"X-Announcement-Id": "abcd1234"})
        self.assertEqual(r.status, 401)
        self.assertEqual(self.pedidos, [])

    async def test_id_invalido_400(self):
        for ruim in (None, "curto", "ABCD1234", "../../etc/passwd", "a" * 33):
            h = dict(self.h)
            if ruim is not None:
                h["X-Announcement-Id"] = ruim
            r = await self.cli.post("/announce", data=WAV, headers=h)
            self.assertEqual(r.status, 400, ruim)
        self.assertEqual(self.pedidos, [])

    async def test_corpo_sem_riff_400(self):
        r = await self.cli.post("/announce", data=b"ID3 not a wav", headers=dict(self.h, **{"X-Announcement-Id": "abcd1234"}))
        self.assertEqual(r.status, 400)
        self.assertEqual(self.pedidos, [])
        self.assertEqual(os.listdir(self.pasta) if os.path.isdir(self.pasta) else [], [])

    async def test_acima_do_teto_413(self):
        corpo = b"RIFF" + b"\x00" * (porta_local.ANUNCIO_MAX_BYTES + 10)
        r = await self.cli.post("/announce", data=corpo, headers=dict(self.h, **{"X-Announcement-Id": "abcd1234"}))
        self.assertEqual(r.status, 413)
        self.assertEqual(self.pedidos, [])
        self.assertEqual(os.listdir(self.pasta) if os.path.isdir(self.pasta) else [], [])

    async def test_grava_o_wav_e_devolve_a_resposta_do_maestro(self):
        r = await self.cli.post("/announce", data=WAV, headers=dict(self.h, **{"X-Announcement-Id": "abcd1234"}))
        self.assertEqual(r.status, 200)
        self.assertEqual(await r.json(), {"resposta": "vou_tocar"})
        self.assertEqual(self.pedidos, ["abcd1234"])
        with open(os.path.join(self.pasta, "abcd1234.wav"), "rb") as f:
            self.assertEqual(f.read(), WAV)
        self.assertEqual(os.listdir(self.pasta), ["abcd1234.wav"])  # nenhum .tmp sobra

    async def test_funcao_que_levanta_nao_deixa_o_arquivo(self):
        def quebra(aid):
            raise RuntimeError("boom")
        # a second app whose fn raises
        app = porta_local.criar_app(self.p, lambda: {}, lambda n: None, anuncio_fn=quebra, anuncio_dir=self.pasta)
        cli = TestClient(TestServer(app)); await cli.start_server()
        try:
            r = await cli.post("/announce", data=WAV, headers=dict(self.h, **{"X-Announcement-Id": "abcd1234"}))
            self.assertEqual(r.status, 500)
        finally:
            await cli.close()
        self.assertEqual(os.listdir(self.pasta), [])

    async def test_dois_ids_dois_arquivos(self):
        for aid in ("abcd1234", "efgh5678"):
            r = await self.cli.post("/announce", data=WAV, headers=dict(self.h, **{"X-Announcement-Id": aid}))
            self.assertEqual(r.status, 200)
        self.assertEqual(sorted(os.listdir(self.pasta)), ["abcd1234.wav", "efgh5678.wav"])
        self.assertEqual(self.pedidos, ["abcd1234", "efgh5678"])


class TestSemFuncao(Base):
    com_fn = False

    async def test_rota_nao_existe_404(self):
        r = await self.cli.post("/announce", data=WAV, headers=dict(self.h, **{"X-Announcement-Id": "abcd1234"}))
        self.assertEqual(r.status, 404)


if __name__ == "__main__":
    unittest.main()
