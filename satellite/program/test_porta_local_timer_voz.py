"""cd ~/satelite && ~/oww/bin/python -m unittest test_porta_local_timer_voz -v   (na Pi: precisa de aiohttp)
Spec 2026-11-24 (timer delivery): POST /timer_voice grava <dir>/<id>.wav do aviso falado de um timer ativo."""
import os, shutil, tempfile, unittest
import importlib.util
if importlib.util.find_spec("aiohttp") is None:  # the Pi venv and CI have it; a bare python does not
    raise unittest.SkipTest("needs aiohttp")
from aiohttp.test_utils import TestClient, TestServer
import configuracao as c
import porta_local

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 40


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.p = os.path.join(self.d, "config.json")
        c.carregar(self.p)
        self.pasta = os.path.join(self.d, "timer_voz")
        self.ativos = {"t1ab852"}
        app = porta_local.criar_app(self.p, lambda: {"estado": "parada"}, lambda n: None,
                                    timer_voz_dir=self.pasta, timer_ativo_fn=lambda tid: tid in self.ativos)
        self.cli = TestClient(TestServer(app)); await self.cli.start_server()
        self.h = {"Authorization": f"Bearer {c.carregar(self.p)['senha_porta_local']}", "X-Timer-Id": "t1ab852"}

    async def asyncTearDown(self):
        await self.cli.close()


class TestTimerVoice(Base):
    async def test_timer_voice_stores_wav_for_active_timer(self):
        r = await self.cli.post("/timer_voice", data=WAV, headers=self.h)
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())["ok"], True)
        with open(os.path.join(self.pasta, "t1ab852.wav"), "rb") as f:
            self.assertEqual(f.read(), WAV)
        self.assertEqual(os.listdir(self.pasta), ["t1ab852.wav"])  # sem .tmp sobrando

    async def test_timer_voice_unknown_timer_404(self):
        r = await self.cli.post("/timer_voice", data=WAV, headers=dict(self.h, **{"X-Timer-Id": "t000000"}))
        self.assertEqual(r.status, 404)
        self.assertFalse(os.path.exists(self.pasta) and os.listdir(self.pasta))

    async def test_timer_voice_rejects_non_wav_and_bad_id(self):
        r = await self.cli.post("/timer_voice", data=b"ID3 not a wav", headers=self.h)
        self.assertEqual(r.status, 400)
        for ruim in ("curto", "T1AB852", "../../etc/passwd", "t1ab85", "t1ab8523"):
            r = await self.cli.post("/timer_voice", data=WAV, headers=dict(self.h, **{"X-Timer-Id": ruim}))
            self.assertEqual(r.status, 400, ruim)
        h = {k: v for k, v in self.h.items() if k != "X-Timer-Id"}
        self.assertEqual((await self.cli.post("/timer_voice", data=WAV, headers=h)).status, 400)

    async def test_timer_voice_needs_bearer(self):
        h = {k: v for k, v in self.h.items() if k != "Authorization"}
        r = await self.cli.post("/timer_voice", data=WAV, headers=h)
        self.assertEqual(r.status, 401)

    async def test_timer_voice_over_the_limit_413(self):
        r = await self.cli.post("/timer_voice", data=WAV + b"\x00" * (porta_local.TIMER_VOZ_MAX_BYTES + 1), headers=self.h)
        self.assertEqual(r.status, 413)
        self.assertFalse(os.path.exists(self.pasta) and os.listdir(self.pasta))


if __name__ == "__main__":
    unittest.main()
